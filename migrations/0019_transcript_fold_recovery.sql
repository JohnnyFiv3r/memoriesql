-- Mechanical recovery only. Old commands, receipts and human-only readers keep
-- their published meanings. No grants to principals or semantic work are added.
-- An indexed per-source position is needed because UUID/time allocation order
-- does not establish commit visibility across concurrent fold transactions.
ALTER TABLE memoriesql.transcript_fold_receipts ADD COLUMN recovery_position bigint;
DROP TRIGGER transcript_fold_receipts_immutable ON memoriesql.transcript_fold_receipts;
WITH positions AS (
    SELECT tenant_id, transcript_fold_receipt_id,
      row_number() OVER (PARTITION BY tenant_id, source_object_id
                        ORDER BY transcript_fold_receipt_id) AS position
    FROM memoriesql.transcript_fold_receipts
)
UPDATE memoriesql.transcript_fold_receipts r SET recovery_position=p.position
FROM positions p WHERE r.tenant_id=p.tenant_id
 AND r.transcript_fold_receipt_id=p.transcript_fold_receipt_id;
CREATE TRIGGER transcript_fold_receipts_immutable
BEFORE UPDATE OR DELETE ON memoriesql.transcript_fold_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_immutable_change();
ALTER TABLE memoriesql.transcript_fold_receipts
  ALTER COLUMN recovery_position SET NOT NULL,
  ADD CONSTRAINT transcript_fold_recovery_positive CHECK (recovery_position > 0);
CREATE UNIQUE INDEX transcript_fold_recovery_order_idx
ON memoriesql.transcript_fold_receipts(tenant_id, source_object_id, recovery_position);

CREATE FUNCTION memoriesql.assign_fold_recovery_position() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
BEGIN
    IF NEW.recovery_position IS NOT NULL THEN
        RAISE EXCEPTION 'fold_recovery_position_is_internal' USING ERRCODE='22023';
    END IF;
    -- Held until commit, including through insertion of all outcomes/lineage.
    -- No later position can become visible before an earlier pending commit.
    PERFORM pg_advisory_xact_lock(hashtextextended(
      NEW.tenant_id::text || ':fold-recovery:' || NEW.source_object_id::text,0));
    SELECT COALESCE(max(recovery_position),0)+1 INTO NEW.recovery_position
    FROM memoriesql.transcript_fold_receipts
    WHERE tenant_id=NEW.tenant_id AND source_object_id=NEW.source_object_id;
    RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.assign_fold_recovery_position() FROM PUBLIC;
CREATE TRIGGER transcript_fold_recovery_position
BEFORE INSERT ON memoriesql.transcript_fold_receipts
FOR EACH ROW EXECUTE FUNCTION memoriesql.assign_fold_recovery_position();

CREATE FUNCTION memoriesql.fold_recovery_key_valid(k jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,memoriesql AS $$
BEGIN
    RETURN jsonb_typeof(k)='object'
      AND k ?& ARRAY['source_object_id','fold_receipt_id','outcome_ordinal']
      AND (SELECT count(*)=3 FROM jsonb_object_keys(k))
      AND jsonb_typeof(k->'source_object_id')='string'
      AND (k->>'source_object_id')::uuid IS NOT NULL
      AND jsonb_typeof(k->'fold_receipt_id')='string'
      AND (k->>'fold_receipt_id')::uuid IS NOT NULL
      AND jsonb_typeof(k->'outcome_ordinal')='number'
      AND (k->>'outcome_ordinal') ~ '^[0-9]+$'
      AND (k->>'outcome_ordinal')::integer BETWEEN 0 AND 255;
EXCEPTION WHEN OTHERS THEN RETURN false;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.fold_recovery_key_valid(jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.recover_transcript_fold_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE
    s memoriesql.source_objects%ROWTYPE;
    r memoriesql.transcript_fold_receipts%ROWTYPE;
    o memoriesql.transcript_fold_outcomes%ROWTYPE;
    l memoriesql.transcript_fold_outcome_ranges%ROWTYPE;
    raw memoriesql.source_range_capture_receipts%ROWTYPE;
    chunk memoriesql.captured_source_ranges%ROWTYPE;
    source_id uuid; op text; k jsonb; anchor jsonb; cursor_value jsonb;
    permitted text[]; field_name text; expected_ordinal integer; n integer; start_ordinal integer; count_lineage integer;
    after_position bigint:=0; after_ordinal integer:=-1;
    through_position bigint:=0; through_id uuid; last_key jsonb;
    items jsonb:='[]'; result jsonb; more boolean:=false;
    stored_bytes bytea; receipt_bytes bytea; page_bytes bytea;
    lineage_byte bigint;
    expected_hash text; envelope_hash text; envelope_size integer;
    at_byte bigint; end_byte bigint; expected_chunk_byte bigint;
    started timestamptz:=clock_timestamp();
BEGIN
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'fold_recovery_requires_read_committed' USING ERRCODE='25001';
    END IF;
    op:=request->>'operation';
    permitted:=CASE op
      WHEN 'discover' THEN ARRAY['contract_version','operation','source_object_id','continuation','after','limit']
      WHEN 'inspect' THEN ARRAY['contract_version','operation','key']
      WHEN 'lineage' THEN ARRAY['contract_version','operation','key','next_ordinal','limit']
      WHEN 'read' THEN ARRAY['contract_version','operation','key','evidence','lineage_ordinal','expected_sha256','byte_offset','max_bytes']
    END;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object'
      OR octet_length(request::text)>8192 OR request->'contract_version' IS DISTINCT FROM '1'::jsonb
      OR permitted IS NULL OR EXISTS(SELECT 1 FROM jsonb_object_keys(request) x WHERE NOT x=ANY(permitted)) THEN
        RAISE EXCEPTION 'invalid_fold_recovery_request' USING ERRCODE='22023';
    END IF;
    FOREACH field_name IN ARRAY ARRAY['limit','next_ordinal','max_bytes','byte_offset','lineage_ordinal'] LOOP
        IF request ? field_name AND NOT (field_name='lineage_ordinal' AND request->field_name='null'::jsonb)
          AND (jsonb_typeof(request->field_name) IS DISTINCT FROM 'number'
               OR request->>field_name !~ '^[0-9]+$') THEN
            RAISE EXCEPTION 'invalid_fold_recovery_integer' USING ERRCODE='22023';
        END IF;
    END LOOP;
    IF op='discover' THEN
        IF jsonb_typeof(request->'source_object_id') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'invalid_fold_recovery_source' USING ERRCODE='22023';
        END IF;
        source_id:=(request->>'source_object_id')::uuid;
    ELSE
        k:=request->'key';
        IF memoriesql.fold_recovery_key_valid(k) IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'invalid_fold_recovery_key' USING ERRCODE='22023';
        END IF;
        source_id:=(k->>'source_object_id')::uuid;
    END IF;
    IF source_id IS NULL THEN RAISE EXCEPTION 'invalid_fold_recovery_source' USING ERRCODE='22023'; END IF;
    -- Existing capability/resource/delegation checks and authority mutation fence.
    -- No principal-kind exception and no automatic grant.
    s:=memoriesql.evidence_package_authorize(source_id,false);

    IF op='discover' THEN
        n:=COALESCE((request->>'limit')::integer,32);
        IF n NOT BETWEEN 1 AND 32 THEN RAISE EXCEPTION 'invalid_fold_recovery_limit' USING ERRCODE='22023'; END IF;
        cursor_value:=NULLIF(request->'continuation','null');
        anchor:=NULLIF(request->'after','null');
        IF cursor_value IS NOT NULL THEN
            IF anchor IS NOT NULL OR jsonb_typeof(cursor_value)<>'object'
              OR NOT cursor_value ?& ARRAY['through_receipt_id','after']
              OR (SELECT count(*) FROM jsonb_object_keys(cursor_value))<>2 THEN
                RAISE EXCEPTION 'invalid_fold_recovery_cursor' USING ERRCODE='22023';
            END IF;
            anchor:=cursor_value->'after';
            through_id:=(cursor_value->>'through_receipt_id')::uuid;
            SELECT recovery_position INTO through_position FROM memoriesql.transcript_fold_receipts
            WHERE tenant_id=s.tenant_id AND source_object_id=source_id AND transcript_fold_receipt_id=through_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'stale_fold_recovery_cursor' USING ERRCODE='22023'; END IF;
        ELSE
            SELECT recovery_position,transcript_fold_receipt_id INTO through_position,through_id
            FROM memoriesql.transcript_fold_receipts
            WHERE tenant_id=s.tenant_id AND source_object_id=source_id
            ORDER BY recovery_position DESC LIMIT 1;
            through_position:=COALESCE(through_position,0);
        END IF;
        IF anchor IS NOT NULL THEN
            IF memoriesql.fold_recovery_key_valid(anchor) IS DISTINCT FROM true
              OR (anchor->>'source_object_id')::uuid<>source_id THEN
                RAISE EXCEPTION 'invalid_fold_recovery_cursor' USING ERRCODE='22023';
            END IF;
            SELECT receipt.recovery_position,outcome.outcome_ordinal INTO after_position,after_ordinal
            FROM memoriesql.transcript_fold_receipts receipt JOIN memoriesql.transcript_fold_outcomes outcome
              USING(tenant_id,transcript_fold_receipt_id)
            WHERE receipt.tenant_id=s.tenant_id AND receipt.source_object_id=source_id
              AND receipt.transcript_fold_receipt_id=(anchor->>'fold_receipt_id')::uuid
              AND outcome.outcome_ordinal=(anchor->>'outcome_ordinal')::integer;
            IF NOT FOUND OR after_position>through_position THEN
                RAISE EXCEPTION 'stale_fold_recovery_cursor' USING ERRCODE='22023';
            END IF;
        ELSIF cursor_value IS NOT NULL THEN
            RAISE EXCEPTION 'invalid_fold_recovery_cursor' USING ERRCODE='22023';
        END IF;
        last_key:=anchor;
        -- At most n+2 receipts, each with a PK-limited outcome read. Never sort or
        -- scan the source's full outcome history behind a small response limit.
        FOR r IN SELECT * FROM memoriesql.transcript_fold_receipts
          WHERE tenant_id=s.tenant_id AND source_object_id=source_id
            AND recovery_position>=after_position AND recovery_position<=through_position
          ORDER BY recovery_position LIMIT n+2
        LOOP
            expected_ordinal:=CASE WHEN r.recovery_position=after_position THEN after_ordinal+1 ELSE 0 END;
            FOR o IN SELECT * FROM memoriesql.transcript_fold_outcomes
              WHERE tenant_id=s.tenant_id AND transcript_fold_receipt_id=r.transcript_fold_receipt_id
                AND outcome_ordinal>CASE WHEN r.recovery_position=after_position THEN after_ordinal ELSE -1 END
              ORDER BY outcome_ordinal LIMIT n+1-jsonb_array_length(items)
            LOOP
                IF o.outcome_ordinal<>expected_ordinal OR o.outcome_ordinal>=r.outcome_count THEN
                    RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
                END IF;
                expected_ordinal:=expected_ordinal+1;
                IF jsonb_array_length(items)=n THEN more:=true; EXIT; END IF;
                last_key:=jsonb_build_object('source_object_id',source_id,'fold_receipt_id',r.transcript_fold_receipt_id,'outcome_ordinal',o.outcome_ordinal);
                items:=items || jsonb_build_array(jsonb_build_object('key',last_key,
                  'outcome_kind',o.outcome_kind,'byte_start',o.byte_start,'byte_end_exclusive',o.byte_end_exclusive,
                  'start_record_index',o.start_record_index,'end_record_index',o.end_record_index,'source_bytes_sha256',o.source_bytes_sha256));
            END LOOP;
            EXIT WHEN more;
            IF expected_ordinal<>r.outcome_count THEN
                RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
            END IF;
        END LOOP;
        result:=jsonb_build_object('source_object_id',source_id,'through_receipt_id',through_id,'outcomes',items,
          'resume_after',last_key,'continuation',CASE WHEN more THEN jsonb_build_object('through_receipt_id',through_id,'after',last_key) ELSE NULL END);
    ELSE
        SELECT * INTO r FROM memoriesql.transcript_fold_receipts
        WHERE tenant_id=s.tenant_id AND source_object_id=source_id AND transcript_fold_receipt_id=(k->>'fold_receipt_id')::uuid;
        IF NOT FOUND THEN RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002'; END IF;
        SELECT * INTO o FROM memoriesql.transcript_fold_outcomes
        WHERE tenant_id=s.tenant_id AND transcript_fold_receipt_id=r.transcript_fold_receipt_id
          AND outcome_ordinal=(k->>'outcome_ordinal')::integer AND source_object_id=source_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002'; END IF;
        IF op='inspect' THEN
            SELECT count(*) INTO count_lineage FROM memoriesql.transcript_fold_outcome_ranges
            WHERE tenant_id=s.tenant_id AND transcript_fold_receipt_id=r.transcript_fold_receipt_id AND outcome_ordinal=o.outcome_ordinal;
            IF count_lineage NOT BETWEEN 1 AND 1024 THEN RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002'; END IF;
            IF o.outcome_kind='exact_turn' THEN
                SELECT envelope_sha256,octet_length(envelope_canonical_json) INTO envelope_hash,envelope_size
                FROM memoriesql.transcript_fold_exact_turns
                WHERE tenant_id=s.tenant_id AND transcript_fold_receipt_id=r.transcript_fold_receipt_id AND outcome_ordinal=o.outcome_ordinal;
                IF NOT FOUND OR envelope_hash IS DISTINCT FROM o.exact_envelope_sha256 THEN
                    RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
                END IF;
            END IF;
            result:=jsonb_build_object('key',k,'outcome_kind',o.outcome_kind,
              'byte_start',o.byte_start,'byte_end_exclusive',o.byte_end_exclusive,'start_record_index',o.start_record_index,
              'end_record_index',o.end_record_index,'source_bytes_sha256',o.source_bytes_sha256,
              'source_revision_key',r.source_revision_key,'file_identity_key',r.file_identity_key,'file_identity',r.file_identity,
              'connector_id',r.connector_id,'capability_id',r.capability_id,'adapter_profile_version',r.adapter_profile_version,
              'observed_source_format_version',r.observed_source_format_version,'folded_by_principal_id',r.folded_by_principal_id,
              'folded_at',r.folded_at,'idempotency_receipt_id',r.idempotency_receipt_id,'outcomes_sha256',r.outcomes_sha256,
              'exact_envelope_sha256',envelope_hash,'exact_envelope_bytes',envelope_size,'lineage_count',count_lineage,
              'source_qualification','not_established',
              'policy_disposition',CASE WHEN o.outcome_kind='policy_disposition' THEN jsonb_build_object('disposition_code',o.disposition_code,'scope_reason',o.scope_reason) ELSE NULL END,
              'transcript_span',CASE WHEN o.outcome_kind='transcript_span' THEN jsonb_build_object(
                'transcript_span_version',1,'source_object_id',source_id,'source_revision_key',r.source_revision_key,'file_identity_key',r.file_identity_key,
                'byte_start',o.byte_start,'byte_end_exclusive',o.byte_end_exclusive,'start_record_index',o.start_record_index,
                'end_record_index',o.end_record_index,'source_bytes_sha256',o.source_bytes_sha256,'topology_status','unknown','content_storage','retained_source_ranges') ELSE NULL END);
        ELSIF op='lineage' THEN
            n:=COALESCE((request->>'limit')::integer,16); start_ordinal:=COALESCE((request->>'next_ordinal')::integer,0);
            IF n NOT BETWEEN 1 AND 16 OR start_ordinal NOT BETWEEN 0 AND 1023 THEN
                RAISE EXCEPTION 'invalid_fold_recovery_limit' USING ERRCODE='22023';
            END IF;
            lineage_byte:=o.byte_start;
            IF start_ordinal>0 THEN
                SELECT byte_end_exclusive INTO lineage_byte FROM memoriesql.transcript_fold_outcome_ranges
                WHERE tenant_id=s.tenant_id AND transcript_fold_receipt_id=r.transcript_fold_receipt_id
                  AND outcome_ordinal=o.outcome_ordinal AND lineage_ordinal=start_ordinal-1;
                IF NOT FOUND THEN RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002'; END IF;
            END IF;
            FOR l IN SELECT * FROM memoriesql.transcript_fold_outcome_ranges
              WHERE tenant_id=s.tenant_id AND transcript_fold_receipt_id=r.transcript_fold_receipt_id
                AND outcome_ordinal=o.outcome_ordinal AND lineage_ordinal>=start_ordinal
              ORDER BY lineage_ordinal LIMIT n+1
            LOOP
                IF l.lineage_ordinal<>start_ordinal+jsonb_array_length(items) OR l.byte_start<>lineage_byte OR l.byte_end_exclusive>o.byte_end_exclusive THEN
                    RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
                END IF;
                IF jsonb_array_length(items)=n THEN more:=true; EXIT; END IF;
                lineage_byte:=l.byte_end_exclusive;
                items:=items || jsonb_build_array(jsonb_build_object(
                  'lineage_ordinal',l.lineage_ordinal,'source_range_receipt_id',l.source_range_receipt_id,
                  'receipt_byte_start',l.receipt_byte_start,'receipt_byte_end_exclusive',l.receipt_byte_end_exclusive,
                  'receipt_payload_sha256',l.receipt_payload_sha256,'byte_start',l.byte_start,
                  'byte_end_exclusive',l.byte_end_exclusive,'source_bytes_sha256',l.source_bytes_sha256));
            END LOOP;
            IF jsonb_array_length(items)=0 OR (NOT more AND lineage_byte<>o.byte_end_exclusive) THEN RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002'; END IF;
            result:=jsonb_build_object('key',k,'ranges',items,'next_ordinal',CASE WHEN more THEN start_ordinal+n ELSE NULL END);
        ELSE
            n:=COALESCE((request->>'max_bytes')::integer,32768); at_byte:=COALESCE((request->>'byte_offset')::bigint,0);
            IF n NOT BETWEEN 1 AND 32768 OR at_byte<0 OR COALESCE(request->>'expected_sha256','') !~ '^[a-f0-9]{64}$' THEN
                RAISE EXCEPTION 'invalid_fold_recovery_read' USING ERRCODE='22023';
            END IF;
            IF request->>'evidence'='exact_envelope' AND NULLIF(request->'lineage_ordinal','null') IS NULL AND o.outcome_kind='exact_turn' THEN
                SELECT convert_to(envelope_canonical_json,'UTF8'),envelope_sha256 INTO stored_bytes,expected_hash
                FROM memoriesql.transcript_fold_exact_turns WHERE tenant_id=s.tenant_id
                  AND transcript_fold_receipt_id=r.transcript_fold_receipt_id AND outcome_ordinal=o.outcome_ordinal;
                IF NOT FOUND OR expected_hash IS DISTINCT FROM o.exact_envelope_sha256 THEN
                    RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
                END IF;
            ELSIF request->>'evidence'='raw_lineage' AND (request->>'lineage_ordinal')::integer BETWEEN 0 AND 1023 THEN
                SELECT * INTO l FROM memoriesql.transcript_fold_outcome_ranges WHERE tenant_id=s.tenant_id
                  AND transcript_fold_receipt_id=r.transcript_fold_receipt_id AND outcome_ordinal=o.outcome_ordinal
                  AND lineage_ordinal=(request->>'lineage_ordinal')::integer;
                IF NOT FOUND THEN RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002'; END IF;
                SELECT * INTO raw FROM memoriesql.source_range_capture_receipts WHERE tenant_id=s.tenant_id
                  AND source_range_receipt_id=l.source_range_receipt_id AND source_object_id=source_id;
                IF NOT FOUND OR raw.source_revision_key<>r.source_revision_key OR raw.file_identity_key<>r.file_identity_key
                  OR raw.byte_start<>l.receipt_byte_start OR raw.byte_end_exclusive<>l.receipt_byte_end_exclusive
                  OR raw.payload_sha256<>l.receipt_payload_sha256 OR raw.payload_byte_count>262144 THEN
                    RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
                END IF;
                receipt_bytes:=''::bytea; expected_chunk_byte:=raw.byte_start; start_ordinal:=0;
                FOR chunk IN SELECT * FROM memoriesql.captured_source_ranges WHERE tenant_id=s.tenant_id
                  AND source_range_receipt_id=raw.source_range_receipt_id ORDER BY chunk_ordinal LIMIT 256
                LOOP
                    IF chunk.byte_start<>expected_chunk_byte OR chunk.chunk_ordinal<>start_ordinal OR chunk.byte_end_exclusive>raw.byte_end_exclusive THEN
                        RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
                    END IF;
                    receipt_bytes:=receipt_bytes || chunk.payload_bytes;
                    expected_chunk_byte:=chunk.byte_end_exclusive; start_ordinal:=start_ordinal+1;
                END LOOP;
                IF expected_chunk_byte<>raw.byte_end_exclusive OR encode(sha256(receipt_bytes),'hex')<>raw.payload_sha256 THEN
                    RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
                END IF;
                stored_bytes:=substring(receipt_bytes FROM (l.byte_start-raw.byte_start+1)::integer FOR (l.byte_end_exclusive-l.byte_start)::integer);
                expected_hash:=l.source_bytes_sha256;
            ELSE RAISE EXCEPTION 'invalid_fold_recovery_evidence' USING ERRCODE='22023'; END IF;
            IF encode(sha256(stored_bytes),'hex') IS DISTINCT FROM expected_hash THEN
                RAISE EXCEPTION 'fold_recovery_unavailable' USING ERRCODE='P0002';
            END IF;
            IF expected_hash<>request->>'expected_sha256' OR at_byte>=octet_length(stored_bytes) THEN
                RAISE EXCEPTION 'stale_fold_recovery_read' USING ERRCODE='22023';
            END IF;
            end_byte:=LEAST(at_byte+n,octet_length(stored_bytes));
            page_bytes:=substring(stored_bytes FROM (at_byte+1)::integer FOR (end_byte-at_byte)::integer);
            result:=jsonb_build_object('key',k,'evidence',request->>'evidence','lineage_ordinal',request->'lineage_ordinal',
              'evidence_sha256',expected_hash,'total_bytes',octet_length(stored_bytes),'byte_offset',at_byte,
              'byte_end_exclusive',end_byte,'content_hex',encode(page_bytes,'hex'),'content_sha256',encode(sha256(page_bytes),'hex'),
              'next_byte_offset',CASE WHEN end_byte<octet_length(stored_bytes) THEN end_byte ELSE NULL END);
        END IF;
    END IF;
    PERFORM memoriesql.evidence_package_authorize(source_id,false);
    IF clock_timestamp()-started>interval '2 seconds' THEN RAISE EXCEPTION 'fold_recovery_work_timeout' USING ERRCODE='57014'; END IF;
    IF octet_length(result::text)>131072 THEN RAISE EXCEPTION 'fold_recovery_response_bound' USING ERRCODE='54000'; END IF;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.recover_transcript_fold_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.recover_transcript_fold_v1(jsonb) TO memoriesql_application;
COMMENT ON COLUMN memoriesql.transcript_fold_receipts.recovery_position IS
'Internal committed discovery order, not source time or semantic occurrence identity. Historical backfill orders receipt IDs; future inserts serialize per source.';
