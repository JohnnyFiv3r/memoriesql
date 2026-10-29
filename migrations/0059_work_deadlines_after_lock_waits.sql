-- Work deadlines count work, not lock waits.
--
-- Six kernel functions bound their own work with a 2 s deadline measured from
-- entry. Each also waits, inside that window, for locks: the tenant
-- outcome-authority fence its authorization takes shared, keyed advisory locks,
-- and a package or role row lock. Every wait may last up to the 500 ms lock
-- timeout, so contention alone could exhaust the budget before any work began
-- (57014 evidence_work_timeout / fold_recovery_work_timeout /
-- materialization_operation_expired, 54000 stored_bead_budget).
--
-- Each function below is its latest definition with only timing statements
-- added. Every wait point is bracketed by `lock_at:=clock_timestamp()` and
-- `waited:=waited+(clock_timestamp()-lock_at)`, and every 2 s check subtracts
-- `waited`. The deadline itself, every lock, authorization check, refusal and
-- response are unchanged; waits inside other callees still count.
-- Forward-only; earlier migrations keep their bytes.

CREATE OR REPLACE FUNCTION memoriesql.write_evidence_package_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, memoriesql
SET lock_timeout = '500ms' AS $$
DECLARE
    started timestamptz := clock_timestamp();
    waited interval := interval '0';
    lock_at timestamptz;
    c memoriesql.authorization_contexts%ROWTYPE;
    s memoriesql.source_objects%ROWTYPE;
    p memoriesql.evidence_packages%ROWTYPE;
    old_receipt memoriesql.idempotency_receipts%ROWTYPE;
    raw memoriesql.source_range_capture_receipts%ROWTYPE;
    op text := request ->> 'operation'; d jsonb; part jsonb; entry jsonb; slice jsonb;
    req_hash text; decl_hash text; occ_hash text; calculated text; prior text;
    new_receipt uuid := uuidv7(); result jsonb; existing boolean := false;
    segment bytea; source_bytes bytea := ''::bytea; n integer; total_source integer := 0;
    next_offset bigint; chunk record;
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR pg_column_size(request)>524288 THEN
        RAISE EXCEPTION 'evidence_command_bound' USING ERRCODE='22023';
    END IF;
    IF request ->> 'contract_version' IS DISTINCT FROM '1' OR op IS NULL OR op NOT IN ('create','append','seal') OR
       octet_length(request::text) > 524288 OR
       jsonb_typeof(request->'idempotency_key') IS DISTINCT FROM 'string' OR
       COALESCE(length(request ->> 'idempotency_key'),0) NOT BETWEEN 1 AND 512 THEN
        RAISE EXCEPTION 'invalid_evidence_contract' USING ERRCODE='22023';
    END IF;
    IF op='create' THEN
        IF request - ARRAY['contract_version','operation','idempotency_key','declaration'] <> '{}'::jsonb THEN
            RAISE EXCEPTION 'invalid_evidence_contract' USING ERRCODE='22023';
        END IF;
        d := request -> 'declaration';
        IF d IS NULL OR jsonb_typeof(d) <> 'object' OR
           d - ARRAY['source_object_id','source_revision_key','occurrence_key','occurrence_identity_basis','normalization_policy_version','package_revision','native','qualification','expected_parts','expected_characters','expected_utf8_bytes','expected_inventory_sha256'] <> '{}'::jsonb OR
           (SELECT count(*) FROM jsonb_object_keys(d)) <> 12 OR
           octet_length(memoriesql.canonical_semantic_json_text(d)) > 8192 OR
           jsonb_typeof(d->'source_revision_key') IS DISTINCT FROM 'string' OR
           jsonb_typeof(d->'occurrence_key') IS DISTINCT FROM 'string' OR
           jsonb_typeof(d->'normalization_policy_version') IS DISTINCT FROM 'string' OR
           COALESCE(length(d->>'source_revision_key'),0) NOT BETWEEN 1 AND 512 OR
           COALESCE(length(d->>'occurrence_key'),0) NOT BETWEEN 1 AND 256 OR
           COALESCE(length(d->>'normalization_policy_version'),0) NOT BETWEEN 1 AND 128 OR
           COALESCE((d->>'package_revision')::integer,0) < 1 OR
           COALESCE((d->>'expected_parts')::integer,0) NOT BETWEEN 1 AND 256 OR
           COALESCE((d->>'expected_utf8_bytes')::integer,0) NOT BETWEEN 1 AND 16777216 OR
           COALESCE((d->>'expected_characters')::integer,0) NOT BETWEEN 1 AND (d->>'expected_utf8_bytes')::integer OR
           COALESCE(d->>'expected_inventory_sha256','') !~ '^[a-f0-9]{64}$' OR
           COALESCE(d->>'occurrence_identity_basis','') NOT IN ('native','producer_assigned') OR
           (d->>'occurrence_identity_basis'='native' AND COALESCE(length(d#>>'{native,native_id}'),0)=0) OR
           jsonb_typeof(d->'native') IS DISTINCT FROM 'object' OR
           NOT memoriesql.evidence_native_facts_valid(d->'native') OR
           NOT memoriesql.evidence_qualification_valid(d->'qualification') THEN
            RAISE EXCEPTION 'invalid_evidence_declaration' USING ERRCODE='22023';
        END IF;
        lock_at:=clock_timestamp();
        s := memoriesql.evidence_package_authorize((d->>'source_object_id')::uuid,true);
        waited:=waited+(clock_timestamp()-lock_at);
    ELSE
        IF request - (CASE op WHEN 'append' THEN ARRAY['contract_version','operation','idempotency_key','package_id','part'] ELSE ARRAY['contract_version','operation','idempotency_key','package_id'] END) <> '{}'::jsonb THEN
            RAISE EXCEPTION 'invalid_evidence_contract' USING ERRCODE='22023';
        END IF;
        SELECT * INTO c FROM memoriesql.current_authorization_context();
        SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
        lock_at:=clock_timestamp();
        s := memoriesql.evidence_package_authorize(p.source_object_id,true);
        waited:=waited+(clock_timestamp()-lock_at);
        d := p.declaration;
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    -- Order: authority, operation idempotency, occurrence. Recheck after waits.
    lock_at:=clock_timestamp();
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text || ':evidence-operation:' || (request->>'idempotency_key'),0));
    waited:=waited+(clock_timestamp()-lock_at);
    -- Server-local request identity uses deterministic jsonb serialization.
    -- Do not run the historical character-loop canonicalizer over large content.
    req_hash := encode(sha256(convert_to(request::text,'UTF8')),'hex');
    SELECT * INTO old_receipt FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='evidence_package.'||op AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old_receipt.request_hash <> req_hash OR old_receipt.workspace_id <> c.workspace_id THEN
            RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505';
        END IF;
        PERFORM memoriesql.evidence_package_authorize(s.source_object_id,true);
        RETURN old_receipt.response_receipt || jsonb_build_object('replayed',true);
    END IF;
    occ_hash := encode(sha256(convert_to(jsonb_build_array(s.source_object_id,d->>'source_revision_key',d->>'occurrence_key')::text,'UTF8')),'hex');
    lock_at:=clock_timestamp();
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text || ':evidence-occurrence:' || occ_hash,0));
    PERFORM memoriesql.evidence_package_authorize(s.source_object_id,true);
    waited:=waited+(clock_timestamp()-lock_at);
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,new_receipt,'evidence_package.'||op,request->>'idempotency_key',req_hash,'in_progress','source',s.source_object_id,1,clock_timestamp(),clock_timestamp());
    IF op='create' THEN
        decl_hash := encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(d),'UTF8')),'hex');
        SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND occurrence_hash=occ_hash AND declaration->>'normalization_policy_version'=d->>'normalization_policy_version' AND declaration->>'package_revision'=d->>'package_revision';
        IF FOUND THEN
            IF p.declaration_hash <> decl_hash THEN RAISE EXCEPTION 'evidence_representation_conflict' USING ERRCODE='23505'; END IF;
            existing := true;
        ELSE
            INSERT INTO memoriesql.evidence_packages(tenant_id,workspace_id,access_scope_id,package_id,source_object_id,occurrence_hash,declaration,declaration_hash,producer_principal_id,created_receipt_id)
            VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uuidv7(),s.source_object_id,occ_hash,d,decl_hash,c.principal_id,new_receipt) RETURNING * INTO p;
        END IF;
    ELSE
        lock_at:=clock_timestamp();
        SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=p.package_id FOR UPDATE;
        waited:=waited+(clock_timestamp()-lock_at);
        IF op='append' THEN
            part := request->'part';
            IF part IS NULL OR jsonb_typeof(part)<>'object' OR
               part - ARRAY['part_id','ordinal','component_key','component_offset','parent_component_key','kind','native','derivation','lineage','content','content_sha256'] <> '{}'::jsonb OR
               (SELECT count(*) FROM jsonb_object_keys(part)) <> 11 OR
               COALESCE(octet_length(part->>'content'),0) NOT BETWEEN 1 AND 65536 OR
               COALESCE((part->>'ordinal')::integer,-1) NOT BETWEEN 0 AND 255 OR
               COALESCE(length(part->>'component_key'),0) NOT BETWEEN 1 AND 256 OR
               COALESCE((part->>'component_offset')::bigint,-1)<0 OR
               jsonb_typeof(part->'content') IS DISTINCT FROM 'string' OR
               jsonb_typeof(part->'component_key') IS DISTINCT FROM 'string' OR
               (part->'parent_component_key'<>'null'::jsonb AND (jsonb_typeof(part->'parent_component_key')<>'string' OR char_length(part->>'parent_component_key')>256)) OR
               jsonb_typeof(part->'kind') IS DISTINCT FROM 'string' OR
               COALESCE(length(part->>'kind'),0) NOT BETWEEN 1 AND 64 OR
               COALESCE(part->>'derivation','') NOT IN ('identity_utf8','producer_normalized') OR
               jsonb_typeof(part->'lineage') IS DISTINCT FROM 'array' OR
               jsonb_array_length(part->'lineage') NOT BETWEEN 1 AND 16 OR
               NOT memoriesql.evidence_native_facts_valid(part->'native') OR
               part->>'content_sha256' IS DISTINCT FROM encode(sha256(convert_to(part->>'content','UTF8')),'hex') THEN
                RAISE EXCEPTION 'invalid_evidence_part' USING ERRCODE='22023';
            END IF;
            entry := (part-'content') || jsonb_build_object('characters',char_length(part->>'content'),'utf8_bytes',octet_length(part->>'content'));
            calculated := encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(entry),'UTF8')),'hex');
            SELECT inventory_hash INTO prior FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=p.package_id AND ordinal=(part->>'ordinal')::integer;
            IF FOUND THEN
                IF prior<>calculated THEN RAISE EXCEPTION 'evidence_part_conflict' USING ERRCODE='23505'; END IF;
                existing := true;
            ELSE
                IF p.sealed_receipt_id IS NOT NULL THEN RAISE EXCEPTION 'evidence_package_immutable' USING ERRCODE='55000'; END IF;
                IF (part->>'ordinal')::integer <> p.part_count OR p.part_count >= (d->>'expected_parts')::integer OR
                   p.character_count+char_length(part->>'content') > (d->>'expected_characters')::integer OR
                   p.utf8_byte_count+octet_length(part->>'content') > (d->>'expected_utf8_bytes')::integer THEN
                    RAISE EXCEPTION 'evidence_inventory_order_or_totals' USING ERRCODE='22023';
                END IF;
                IF (part->>'component_offset')::bigint <> COALESCE((
                    SELECT sum((inventory->>'characters')::integer) FROM memoriesql.evidence_package_parts
                    WHERE tenant_id=c.tenant_id AND package_id=p.package_id AND inventory->>'component_key'=part->>'component_key'
                ),0) THEN RAISE EXCEPTION 'evidence_component_gap' USING ERRCODE='22023'; END IF;
                IF EXISTS (
                    SELECT 1 FROM memoriesql.evidence_package_parts prior_part
                    WHERE tenant_id=c.tenant_id AND package_id=p.package_id AND inventory->>'component_key'=part->>'component_key'
                    AND (inventory->'native' IS DISTINCT FROM part->'native' OR inventory->'parent_component_key' IS DISTINCT FROM part->'parent_component_key' OR inventory->'kind' IS DISTINCT FROM part->'kind')
                ) THEN RAISE EXCEPTION 'evidence_component_metadata_conflict' USING ERRCODE='22023'; END IF;
                -- Only point-indexed immutable receipts/chunks; <=16 receipts,
                -- <=4096 existing raw chunks, <=256 KiB returned source slices.
                FOR slice IN SELECT value FROM jsonb_array_elements(part->'lineage') LOOP
                    IF slice - ARRAY['source_range_receipt_id','byte_start','byte_end_exclusive','source_bytes_sha256','fold_receipt_id','fold_outcome_ordinal'] <> '{}'::jsonb OR
                       (SELECT count(*) FROM jsonb_object_keys(slice)) <> 6 THEN RAISE EXCEPTION 'invalid_evidence_lineage' USING ERRCODE='22023'; END IF;
                    n := (slice->>'byte_end_exclusive')::bigint-(slice->>'byte_start')::bigint;
                    total_source := total_source+n;
                    IF n IS NULL OR COALESCE((slice->>'byte_start')::bigint,-1)<0 OR n NOT BETWEEN 1 AND 262144 OR total_source>262144 THEN RAISE EXCEPTION 'evidence_lineage_bound' USING ERRCODE='22023'; END IF;
                    SELECT * INTO raw FROM memoriesql.source_range_capture_receipts WHERE tenant_id=c.tenant_id AND source_range_receipt_id=(slice->>'source_range_receipt_id')::uuid;
                    IF raw.source_object_id IS DISTINCT FROM s.source_object_id OR raw.source_revision_key IS DISTINCT FROM d->>'source_revision_key' OR raw.byte_start>(slice->>'byte_start')::bigint OR raw.byte_end_exclusive<(slice->>'byte_end_exclusive')::bigint THEN
                        RAISE EXCEPTION 'evidence_lineage_mismatch' USING ERRCODE='22023';
                    END IF;
                    segment := ''::bytea; next_offset := (slice->>'byte_start')::bigint;
                    FOR chunk IN SELECT * FROM memoriesql.captured_source_ranges WHERE tenant_id=c.tenant_id AND source_range_receipt_id=raw.source_range_receipt_id ORDER BY chunk_ordinal LOOP
                        IF chunk.byte_end_exclusive<=next_offset OR chunk.byte_start >= (slice->>'byte_end_exclusive')::bigint THEN CONTINUE; END IF;
                        IF chunk.byte_start>next_offset THEN RAISE EXCEPTION 'evidence_source_gap' USING ERRCODE='22023'; END IF;
                        segment := segment || substring(chunk.payload_bytes FROM (next_offset-chunk.byte_start+1)::integer FOR (LEAST(chunk.byte_end_exclusive,(slice->>'byte_end_exclusive')::bigint)-next_offset)::integer);
                        next_offset := LEAST(chunk.byte_end_exclusive,(slice->>'byte_end_exclusive')::bigint);
                    END LOOP;
                    IF next_offset<>(slice->>'byte_end_exclusive')::bigint OR octet_length(segment)<>n OR encode(sha256(segment),'hex') IS DISTINCT FROM slice->>'source_bytes_sha256' THEN
                        RAISE EXCEPTION 'evidence_source_integrity' USING ERRCODE='22023';
                    END IF;
                    IF (slice->>'fold_receipt_id' IS NULL) <> (slice->>'fold_outcome_ordinal' IS NULL) THEN RAISE EXCEPTION 'evidence_fold_identity' USING ERRCODE='22023'; END IF;
                    IF slice->>'fold_receipt_id' IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM memoriesql.transcript_fold_outcomes o JOIN memoriesql.transcript_fold_receipts f USING(tenant_id,transcript_fold_receipt_id)
                        WHERE o.tenant_id=c.tenant_id AND o.transcript_fold_receipt_id=(slice->>'fold_receipt_id')::uuid AND o.outcome_ordinal=(slice->>'fold_outcome_ordinal')::integer AND o.source_object_id=s.source_object_id AND f.source_revision_key=raw.source_revision_key AND f.file_identity_key=raw.file_identity_key AND o.byte_start<=(slice->>'byte_start')::bigint AND o.byte_end_exclusive>=(slice->>'byte_end_exclusive')::bigint
                    ) THEN RAISE EXCEPTION 'evidence_fold_mismatch' USING ERRCODE='22023'; END IF;
                    source_bytes := source_bytes || segment;
                    IF clock_timestamp()-started-waited>interval '2 seconds' THEN RAISE EXCEPTION 'evidence_work_timeout' USING ERRCODE='57014'; END IF;
                END LOOP;
                IF part->>'derivation'='identity_utf8' AND source_bytes<>convert_to(part->>'content','UTF8') THEN RAISE EXCEPTION 'evidence_identity_derivation_mismatch' USING ERRCODE='22023'; END IF;
                INSERT INTO memoriesql.evidence_package_parts VALUES(c.tenant_id,p.package_id,(part->>'ordinal')::integer,(part->>'part_id')::uuid,entry,calculated,part->>'content',new_receipt);
                UPDATE memoriesql.evidence_packages SET part_count=part_count+1,character_count=character_count+char_length(part->>'content'),utf8_byte_count=utf8_byte_count+octet_length(part->>'content') WHERE tenant_id=c.tenant_id AND package_id=p.package_id RETURNING * INTO p;
            END IF;
        ELSE
            IF p.sealed_receipt_id IS NOT NULL THEN existing:=true;
            ELSE
                -- Scan <=256 small hashes. No content hydration at seal.
                SELECT encode(sha256(convert_to(COALESCE(string_agg(inventory_hash,'' ORDER BY ordinal),''),'UTF8')),'hex') INTO calculated FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=p.package_id;
                IF p.part_count<>(d->>'expected_parts')::integer OR p.character_count<>(d->>'expected_characters')::integer OR p.utf8_byte_count<>(d->>'expected_utf8_bytes')::integer OR calculated<>d->>'expected_inventory_sha256' THEN
                    RAISE EXCEPTION 'evidence_inventory_incomplete' USING ERRCODE='22023';
                END IF;
                UPDATE memoriesql.evidence_packages SET inventory_hash=calculated,sealed_receipt_id=new_receipt WHERE tenant_id=c.tenant_id AND package_id=p.package_id RETURNING * INTO p;
            END IF;
        END IF;
    END IF;
    lock_at:=clock_timestamp();
    PERFORM memoriesql.evidence_package_authorize(s.source_object_id,true);
    waited:=waited+(clock_timestamp()-lock_at);
    IF clock_timestamp()-started-waited>interval '2 seconds' THEN RAISE EXCEPTION 'evidence_work_timeout' USING ERRCODE='57014'; END IF;
    result:=jsonb_build_object('contract_version',1,'package_id',p.package_id,'idempotency_receipt_id',new_receipt,'operation',op,'replayed',false,'already_exists',existing,'sealed',p.sealed_receipt_id IS NOT NULL,'appended_parts',p.part_count);
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=new_receipt;
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.read_evidence_package_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
SET lock_timeout='500ms' AS $$
DECLARE
    started timestamptz:=clock_timestamp(); waited interval:=interval '0'; lock_at timestamptz; c memoriesql.authorization_contexts%ROWTYPE;
    p memoriesql.evidence_packages%ROWTYPE; item memoriesql.evidence_package_parts%ROWTYPE;
    op text:=request->>'operation'; position integer:=0; maximum integer;
    following integer; result jsonb; cursor jsonb; entries jsonb; content text;
    ready boolean;
BEGIN
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR pg_column_size(request)>8192 THEN
        RAISE EXCEPTION 'evidence_read_bound' USING ERRCODE='22023';
    END IF;
    IF request->>'contract_version' IS DISTINCT FROM '1' OR op IS NULL OR op NOT IN ('inspect','inventory','read') OR
       octet_length(memoriesql.canonical_semantic_json_text(request))>4096 THEN RAISE EXCEPTION 'invalid_evidence_read' USING ERRCODE='22023'; END IF;
    IF request - (CASE op WHEN 'inspect' THEN ARRAY['contract_version','operation','package_id'] WHEN 'inventory' THEN ARRAY['contract_version','operation','package_id','continuation','limit'] ELSE ARRAY['contract_version','operation','package_id','part_id','continuation','max_characters'] END) <> '{}'::jsonb THEN RAISE EXCEPTION 'invalid_evidence_read' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
    lock_at:=clock_timestamp(); PERFORM memoriesql.evidence_package_authorize(p.source_object_id,false); waited:=waited+(clock_timestamp()-lock_at);
    IF op='inspect' THEN
        ready:=p.sealed_receipt_id IS NOT NULL AND p.declaration#>>'{qualification,boundary}'='qualified_native_unit' AND p.declaration#>>'{qualification,physical_records}'='complete' AND p.declaration#>>'{qualification,normalized_input}'='complete' AND p.declaration#>>'{qualification,source_completeness}'='producer_attested' AND p.declaration#>'{qualification,unresolved_coverage}'='[]'::jsonb;
        result:=jsonb_build_object('package_id',p.package_id,'declaration',p.declaration,'producer_principal_id',p.producer_principal_id,'appended_parts',p.part_count,'appended_characters',p.character_count,'appended_utf8_bytes',p.utf8_byte_count,'sealed',p.sealed_receipt_id IS NOT NULL,'inventory_sha256',p.inventory_hash,'readiness',CASE WHEN ready THEN 'ready_producer_attested' WHEN p.declaration#>>'{qualification,boundary}'='unresolved' OR p.declaration#>>'{qualification,physical_records}'='pending_tail' OR p.sealed_receipt_id IS NOT NULL THEN 'pending_source_qualification' ELSE 'assembling' END,'independently_proven_source_complete',false,'currently_authorized',true);
    ELSE
        IF p.sealed_receipt_id IS NULL THEN RAISE EXCEPTION 'evidence_package_not_sealed' USING ERRCODE='55000'; END IF;
        cursor:=request->'continuation';
        IF cursor IS NOT NULL AND cursor<>'null'::jsonb THEN
            IF jsonb_typeof(cursor)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(cursor))<>4 OR
               cursor-ARRAY['package_id','inventory_sha256','part_id','position']<>'{}'::jsonb OR
               cursor->>'package_id' IS DISTINCT FROM p.package_id::text OR cursor->>'inventory_sha256' IS DISTINCT FROM p.inventory_hash OR
               cursor->>'part_id' IS DISTINCT FROM request->>'part_id' OR
               COALESCE((cursor->>'position')::integer,-1)<0 THEN
                RAISE EXCEPTION 'invalid_evidence_continuation' USING ERRCODE='22023';
            END IF;
            position:=(cursor->>'position')::integer;
        END IF;
        IF op='inventory' THEN
            maximum:=(request->>'limit')::integer;
            IF maximum IS NULL OR maximum NOT BETWEEN 1 AND 4 OR position>=p.part_count THEN RAISE EXCEPTION 'invalid_evidence_continuation' USING ERRCODE='22023'; END IF;
            SELECT jsonb_agg(inventory ORDER BY ordinal) INTO entries FROM (
                SELECT inventory,ordinal FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=p.package_id AND ordinal>=position ORDER BY ordinal LIMIT maximum
            ) bounded;
            following:=LEAST(position+maximum,p.part_count);
            result:=jsonb_build_object('package_id',p.package_id,'inventory_sha256',p.inventory_hash,'entries',entries,'terminal',following=p.part_count,'continuation',CASE WHEN following=p.part_count THEN NULL ELSE jsonb_build_object('package_id',p.package_id,'inventory_sha256',p.inventory_hash,'part_id',NULL,'position',following) END);
        ELSE
            maximum:=(request->>'max_characters')::integer;
            SELECT * INTO item FROM memoriesql.evidence_package_parts WHERE tenant_id=c.tenant_id AND package_id=p.package_id AND part_id=(request->>'part_id')::uuid;
            IF item.part_id IS NULL THEN RAISE EXCEPTION 'evidence_unavailable' USING ERRCODE='42501'; END IF;
            IF maximum IS NULL OR maximum NOT BETWEEN 1 AND 1024 OR position>=(item.inventory->>'characters')::integer THEN RAISE EXCEPTION 'invalid_evidence_continuation' USING ERRCODE='22023'; END IF;
            content:=substring(item.content FROM position+1 FOR maximum);
            following:=position+char_length(content);
            result:=jsonb_build_object('package_id',p.package_id,'inventory_sha256',p.inventory_hash,'part_id',item.part_id,'part_sha256',item.inventory->>'content_sha256','character_start',position,'character_end_exclusive',following,'content',content,'content_sha256',encode(sha256(convert_to(content,'UTF8')),'hex'),'utf8_bytes',octet_length(content),'terminal',following=(item.inventory->>'characters')::integer,'continuation',CASE WHEN following=(item.inventory->>'characters')::integer THEN NULL ELSE jsonb_build_object('package_id',p.package_id,'inventory_sha256',p.inventory_hash,'part_id',item.part_id,'position',following) END);
        END IF;
    END IF;
    lock_at:=clock_timestamp(); PERFORM memoriesql.evidence_package_authorize(p.source_object_id,false); waited:=waited+(clock_timestamp()-lock_at);
    IF octet_length(memoriesql.canonical_semantic_json_text(result))>65536 THEN RAISE EXCEPTION 'evidence_response_bound' USING ERRCODE='22023'; END IF;
    IF clock_timestamp()-started-waited>interval '2 seconds' THEN RAISE EXCEPTION 'evidence_work_timeout' USING ERRCODE='57014'; END IF;
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.recover_transcript_fold_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE
    s memoriesql.source_objects%ROWTYPE;
    c memoriesql.authorization_contexts%ROWTYPE;
    authority_until timestamptz; grant_until timestamptz;
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
    waited interval:=interval '0';
    lock_at timestamptz;
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
    lock_at:=clock_timestamp();
    s:=memoriesql.evidence_package_authorize(source_id,false);
    waited:=waited+(clock_timestamp()-lock_at);
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    -- Existing tenant authority fence first, then this exact global role row.
    -- Role policy has no tenant mutation trigger. A shared row lock serializes
    -- UPDATE/DELETE through transaction end without changing legacy policies.
    lock_at:=clock_timestamp();
    PERFORM cap.role_key FROM memoriesql.role_capabilities cap
    JOIN memoriesql.workspace_memberships membership ON membership.role_key=cap.role_key
    WHERE membership.tenant_id=c.tenant_id AND membership.workspace_id=c.workspace_id
      AND membership.principal_id=c.principal_id AND cap.capability_key='source.raw.read'
    FOR SHARE OF cap;
    waited:=waited+(clock_timestamp()-lock_at);
    IF NOT FOUND THEN RAISE EXCEPTION 'evidence_unavailable' USING ERRCODE='42501'; END IF;

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
    lock_at:=clock_timestamp();
    PERFORM memoriesql.evidence_package_authorize(source_id,false);
    waited:=waited+(clock_timestamp()-lock_at);
    IF octet_length(result::text)>131072 THEN RAISE EXCEPTION 'fold_recovery_response_bound' USING ERRCODE='54000'; END IF;
    -- The published authorization helpers use the outer statement's timestamp.
    -- Supplement, never replace, that policy decision with a return-time expiry
    -- fence. Context expiry pins the credential/pairing deadline at context
    -- creation; also read current credential expiry after authority waits.
    SELECT LEAST(c.expires_at,credential.expires_at) INTO authority_until
    FROM memoriesql.authentication_credentials credential
    WHERE credential.tenant_id=c.tenant_id AND credential.credential_id=c.credential_id
      AND credential.principal_id=c.principal_id AND credential.status='active';
    IF EXISTS(SELECT 1 FROM memoriesql.access_scopes scope WHERE scope.tenant_id=c.tenant_id
      AND scope.access_scope_id=s.access_scope_id AND scope.mode='explicit') THEN
        -- Several grants may independently permit the read: the latest revision
        -- of any currently valid grant suffices, including a non-expiring grant.
        SELECT max(COALESCE(revision.expires_at,'infinity'::timestamptz)) INTO grant_until
        FROM memoriesql.access_grants root
        JOIN LATERAL (SELECT * FROM memoriesql.access_grant_revisions revision
          WHERE revision.tenant_id=root.tenant_id AND revision.grant_id=root.grant_id
          ORDER BY revision.revision DESC LIMIT 1) revision ON true
        WHERE root.tenant_id=c.tenant_id AND root.workspace_id=c.workspace_id
          AND root.access_scope_id=s.access_scope_id AND root.target_principal_id=c.principal_id
          AND revision.status='active' AND revision.valid_from<=clock_timestamp()
          AND 'read'=ANY(revision.permission_keys);
        IF grant_until IS NULL THEN RAISE EXCEPTION 'evidence_unavailable' USING ERRCODE='42501'; END IF;
        authority_until:=LEAST(authority_until,grant_until);
    END IF;
    IF clock_timestamp()-started-waited>interval '2 seconds' THEN RAISE EXCEPTION 'fold_recovery_work_timeout' USING ERRCODE='57014'; END IF;
    IF authority_until IS NULL OR authority_until<=clock_timestamp() THEN
        RAISE EXCEPTION 'evidence_unavailable' USING ERRCODE='42501';
    END IF;
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.materialize_logical_unit_v2(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE
    started timestamptz:=clock_timestamp(); waited interval:=interval '0'; lock_at timestamptz; c memoriesql.authorization_contexts%ROWTYPE;
    p memoriesql.evidence_packages%ROWTYPE; s memoriesql.source_objects%ROWTYPE;
    policy memoriesql.evidence_producer_policies%ROWTYPE; old_receipt memoriesql.idempotency_receipts%ROWTYPE;
    frame memoriesql.source_event_materializations%ROWTYPE; binding memoriesql.logical_unit_materializations%ROWTYPE;
    parent memoriesql.source_units%ROWTYPE; e jsonb:=request->'event'; n jsonb; pin jsonb; input jsonb; result jsonb;
    stable_hash text; content_hash text; identity_namespace text; req_hash text; event_hash text; declaration_hash text; unit_identity text;
    receipt_id uuid:=uuidv7(); eid uuid:=uuidv7(); uid uuid:=uuidv7(); bid uuid:=uuidv7(); tid uuid:=uuidv7(); enqueue_receipt uuid;
    parent_id uuid:=(request->>'parent_source_unit_id')::uuid; manifest_id text; existing boolean:=false;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN
        RAISE EXCEPTION 'materialization_requires_read_committed' USING ERRCODE='25000';
    END IF;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR pg_column_size(request)>16384 OR octet_length(request::text)>16384 OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','package_id','expected_inventory_sha256','producer_policy_id','expected_source_object_schema_version','event','parent_source_unit_id']<>'{}'::jsonb OR
       (SELECT count(*) FROM jsonb_object_keys(request))<>9 OR request->'contract_version' IS DISTINCT FROM '2'::jsonb OR request->'expected_schema_version' IS DISTINCT FROM '21'::jsonb OR
       jsonb_typeof(request->'idempotency_key') IS DISTINCT FROM 'string' OR COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR
       COALESCE(request->>'expected_inventory_sha256','')!~'^[a-f0-9]{64}$' OR
       jsonb_typeof(e) IS DISTINCT FROM 'object' OR e-ARRAY['event_key','identity_basis','source_type','native','expected_units']<>'{}'::jsonb OR (SELECT count(*) FROM jsonb_object_keys(e))<>5 OR
       octet_length(memoriesql.canonical_semantic_json_text(e))>8192 OR jsonb_typeof(e->'event_key') IS DISTINCT FROM 'string' OR COALESCE(length(e->>'event_key'),0) NOT BETWEEN 1 AND 256 OR
       COALESCE(e->>'identity_basis','') NOT IN ('native','producer_assigned') OR COALESCE(e->>'source_type','') NOT IN ('transcript','document','media','relational','operational') OR
       NOT memoriesql.evidence_native_facts_valid(e->'native') OR (e->>'identity_basis'='native' AND e#>>'{native,native_id}' IS NULL) OR
       (e->>'expected_units' IS NOT NULL AND (e->>'expected_units')::integer<1) THEN
        RAISE EXCEPTION 'invalid_materialization_contract' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
    lock_at:=clock_timestamp();
    s:=memoriesql.source_stable_authorize(p.source_object_id);
    waited:=waited+(clock_timestamp()-lock_at);
    -- The authenticated producer must own the attestation. A caller cannot
    -- adopt another principal's package merely by supplying a qualification ref.
    IF p.producer_principal_id IS DISTINCT FROM c.principal_id OR s.schema_version IS DISTINCT FROM (request->>'expected_source_object_schema_version')::integer THEN
        RAISE EXCEPTION 'materialization_authority_unavailable' USING ERRCODE='42501'; END IF;
    IF p.sealed_receipt_id IS NULL OR p.inventory_hash IS DISTINCT FROM request->>'expected_inventory_sha256' OR
       p.declaration#>>'{qualification,boundary}' IS DISTINCT FROM 'qualified_native_unit' OR p.declaration#>>'{qualification,physical_records}' IS DISTINCT FROM 'complete' OR
       p.declaration#>>'{qualification,normalized_input}' IS DISTINCT FROM 'complete' OR p.declaration#>>'{qualification,source_completeness}' IS DISTINCT FROM 'producer_attested' OR p.declaration#>'{qualification,unresolved_coverage}' IS DISTINCT FROM '[]'::jsonb THEN
        RAISE EXCEPTION 'materialization_input_pending_or_conflicting' USING ERRCODE='55000'; END IF;
    SELECT * INTO policy FROM memoriesql.evidence_producer_policies WHERE tenant_id=c.tenant_id AND producer_policy_id=(request->>'producer_policy_id')::uuid AND workspace_id=c.workspace_id AND access_scope_id=s.access_scope_id AND source_object_id=s.source_object_id AND producer_principal_id=c.principal_id AND status='active' AND created_at<=clock_timestamp() AND expires_at>clock_timestamp() AND qualification_ref=p.declaration#>>'{qualification,qualification_ref}' AND normalization_policy_version=p.declaration->>'normalization_policy_version';
    IF policy.producer_policy_id IS NULL THEN RAISE EXCEPTION 'trusted_producer_policy_unavailable' USING ERRCODE='42501'; END IF;
    identity_namespace:=policy.source_stable_namespace;
    IF identity_namespace IS NULL THEN RAISE EXCEPTION 'source_stable_policy_unavailable' USING ERRCODE='42501'; END IF;
    IF e->>'identity_basis'<>'native' OR COALESCE(length(e#>>'{native,native_id}'),0)=0 OR p.declaration->>'occurrence_identity_basis'<>'native' THEN
        RAISE EXCEPTION 'source_stable_identity_unqualified' USING ERRCODE='55000'; END IF;
    lock_at:=clock_timestamp();
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialization-source:'||s.source_object_id::text,0));
    waited:=waited+(clock_timestamp()-lock_at);
    IF memoriesql.source_identity_mode_conflicts(c.tenant_id,s.source_object_id,identity_namespace) THEN
        RAISE EXCEPTION 'source_stable_transition_unsupported' USING ERRCODE='55000'; END IF;
    stable_hash:=encode(sha256(convert_to(jsonb_build_array('source-stable-occurrence.v1',s.source_object_id,identity_namespace,p.declaration->>'occurrence_key')::text,'UTF8')),'hex');
    content_hash:=memoriesql.source_stable_content_hash(c.tenant_id,p.package_id);
    req_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    event_hash:=encode(sha256(convert_to(jsonb_build_array('source-stable-event.v1',s.source_object_id,identity_namespace,e->>'event_key')::text,'UTF8')),'hex');
    declaration_hash:=encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(e),'UTF8')),'hex');
    n:=p.declaration->'native';
    unit_identity:=encode(sha256(convert_to(jsonb_build_array(event_hash,p.declaration->>'occurrence_identity_basis',n,parent_id)::text,'UTF8')),'hex');
    -- Same authority fence as storage and queue; deterministic keyed locks only.
    lock_at:=clock_timestamp();
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialize-operation:'||(request->>'idempotency_key'),0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-event:'||event_hash,0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-occurrence:'||p.occurrence_hash,0));
    PERFORM memoriesql.source_stable_authorize(s.source_object_id);
    waited:=waited+(clock_timestamp()-lock_at);
    IF clock_timestamp()-waited>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    SELECT * INTO old_receipt FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='logical_unit.materialize.v2' AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old_receipt.request_hash<>req_hash OR old_receipt.workspace_id<>c.workspace_id THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
        RETURN old_receipt.response_receipt||jsonb_build_object('replayed',true);
    END IF;
    SELECT * INTO frame FROM memoriesql.source_event_materializations WHERE tenant_id=c.tenant_id AND event_identity_hash=event_hash;
    IF FOUND THEN
        IF frame.declaration_hash<>declaration_hash THEN RAISE EXCEPTION 'logical_event_identity_conflict' USING ERRCODE='23505'; END IF;
        eid:=frame.event_id;
    END IF;
    SELECT * INTO binding FROM memoriesql.logical_unit_materializations WHERE tenant_id=c.tenant_id AND stable_identity_hash=stable_hash;
    IF FOUND THEN
        IF binding.identity_hash<>unit_identity OR binding.stable_content_hash<>content_hash THEN RAISE EXCEPTION 'logical_occurrence_identity_conflict' USING ERRCODE='23505'; END IF;
        existing:=true; eid:=binding.event_id; uid:=binding.source_unit_id; bid:=binding.bead_id; tid:=binding.task_id; pin:=binding.package_pin;
    ELSE
        pin:=jsonb_build_object('package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,'inventory_sha256',p.inventory_hash,'required_parts',p.part_count,'required_characters',p.character_count,'required_utf8_bytes',p.utf8_byte_count,'coverage','entire_sealed_inventory');
        IF parent_id IS NOT NULL THEN
            SELECT * INTO parent FROM memoriesql.source_units WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND access_scope_id=s.access_scope_id AND source_unit_id=parent_id AND event_id=eid AND materialization_version=1;
            IF parent.source_unit_id IS NULL OR (n->>'parent_native_id' IS NOT NULL AND parent.external_unit_id IS NOT NULL AND n->>'parent_native_id'<>parent.external_unit_id) THEN RAISE EXCEPTION 'logical_parent_identity_conflict' USING ERRCODE='23505'; END IF;
        END IF;
    END IF;
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,receipt_id,'logical_unit.materialize.v2',request->>'idempotency_key',req_hash,'in_progress','source',s.source_object_id,1,clock_timestamp(),clock_timestamp());
    IF NOT existing THEN
        IF frame.event_id IS NULL THEN
            INSERT INTO memoriesql.source_events(tenant_id,workspace_id,access_scope_id,event_id,source_object_id,source_type,source_system,installation_id,external_event_id,external_id_scope,source_identity_key,session_id,actor_id,actor_kind,source_occurred_at,source_occurred_at_raw,source_sequence,source_revision_key,parser_contract_version,observation_unit_policy_version,observation_unit_count,captured_at,source_ref,content_hash,metadata,materialization_version)
            VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,eid,s.source_object_id,e->>'source_type',s.source_system,s.installation_id,e#>>'{native,native_id}','logical-events.v1:'||s.source_object_id::text,'logical-event.v1:'||event_hash,e#>>'{native,session_native_id}',e#>>'{native,participant_native_id}',e#>>'{native,role}',(e#>>'{native,occurred_at}')::timestamptz,e#>>'{native,occurred_at_raw}',(e#>>'{native,source_order}')::bigint,p.declaration->>'source_revision_key','logical-materialization.v1','logical-materialization.v1',(e->>'expected_units')::integer,clock_timestamp(),'logical-event.v1:'||event_hash,declaration_hash,jsonb_build_object('source_stable_namespace',identity_namespace,'declaration',e,'content_hash_kind','event_declaration_sha256','independently_proven_source_complete',false),1);
            INSERT INTO memoriesql.source_event_materializations(tenant_id,event_id,event_identity_hash,declaration,declaration_hash,initial_receipt_id) VALUES(c.tenant_id,eid,event_hash,e,declaration_hash,receipt_id);
        END IF;
        INSERT INTO memoriesql.source_units(tenant_id,workspace_id,access_scope_id,source_unit_id,event_id,parent_unit_id,unit_kind,is_observation,external_unit_id,unit_ordinal,unit_source_occurred_at,content_hash,structure,hydration_ref,schema_version,created_at,processing_receipt_id,materialization_version)
        VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uid,eid,parent_id,'logical_unit',true,n->>'native_id',(n->>'source_order')::bigint,(n->>'occurred_at')::timestamptz,p.inventory_hash,jsonb_build_object('native',n,'occurrence_identity_basis',p.declaration->>'occurrence_identity_basis','parent_resolution',CASE WHEN parent_id IS NOT NULL THEN 'canonical' WHEN n->>'parent_native_id' IS NOT NULL THEN 'native_identity_only' ELSE 'unknown' END,'content_hash_kind','sealed_inventory_sha256'),'evidence-package.'||p.package_id::text,17,clock_timestamp(),receipt_id,1);
        INSERT INTO memoriesql.beads(tenant_id,workspace_id,access_scope_id,bead_id,event_id,source_unit_id,created_at) VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,bid,eid,uid,clock_timestamp());
        manifest_id:='evidence-package.'||p.package_id::text;
        input:=jsonb_build_object('task_id',tid,'task_kind','memory.semantic.author-complete-unit','contract_revision',1,'target_reference',uid,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
            'evidence_manifest',jsonb_build_object('manifest_id',manifest_id,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',manifest_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
            'payload',jsonb_build_object('source_object_id',s.source_object_id,'event_id',eid,'source_unit_id',uid,'bead_id',bid,'materialization_receipt_id',receipt_id,'producer_policy_id',policy.producer_policy_id,'package',pin,'required_execution','trusted_complete_input_exposure_validation','execution_availability','unavailable'));
        SELECT q.idempotency_receipt_id INTO enqueue_receipt FROM memoriesql.enqueue_semantic_task(tid,'complete-unit.v1:'||p.occurrence_hash,'memoriesql.kernel','memory.semantic.author-complete-unit',1,'8013803a5ad67b0fe530b05c1c5badd533ae118d5105de756588a282bf6d3d3c','complete-unit-bindings-v1:8013803a5ad67b0fe530b05c1c5badd533ae118d5105de756588a282bf6d3d3c',uid::text,0,input,manifest_id,s.access_scope_id,started,NULL,started) q;
        INSERT INTO memoriesql.logical_unit_materializations VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,p.occurrence_hash,unit_identity,eid,uid,bid,tid,p.package_id,policy.producer_policy_id,pin,receipt_id,identity_namespace,stable_hash,content_hash) RETURNING * INTO binding;
        INSERT INTO memoriesql.outbox_events(tenant_id,workspace_id,access_scope_id,outbox_event_id,idempotency_receipt_id,aggregate_kind,aggregate_id,event_kind,payload,headers,recorded_at,available_at)
        VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uuidv7(),receipt_id,'source_unit',uid,'source_unit.materialized',jsonb_build_object('event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'package_id',p.package_id),'{}',clock_timestamp(),clock_timestamp());
    ELSE
        SELECT idempotency_receipt_id INTO enqueue_receipt FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=tid;
    END IF;
    lock_at:=clock_timestamp();
    PERFORM memoriesql.source_stable_authorize(s.source_object_id);
    waited:=waited+(clock_timestamp()-lock_at);
    IF clock_timestamp()-waited>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    result:=jsonb_build_object('contract_version',2,'idempotency_receipt_id',receipt_id,'initial_materialization_receipt_id',binding.initial_receipt_id,'task_enqueue_receipt_id',enqueue_receipt,'submitted_package_id',p.package_id,'bound_package',pin,'event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'status',CASE WHEN existing THEN 'already_exists' ELSE 'materialized' END,'replayed',false,'execution_availability','unavailable','execution_reason','complete_input_executor_unavailable','event_progress_at_commit',memoriesql.logical_event_progress_v1(c.tenant_id,eid));
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=receipt_id;
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.materialize_declared_scope_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE
    started timestamptz:=clock_timestamp(); waited interval:=interval '0'; lock_at timestamptz; c memoriesql.authorization_contexts%ROWTYPE;
    p memoriesql.evidence_packages%ROWTYPE; s memoriesql.source_objects%ROWTYPE;
    policy memoriesql.evidence_producer_policies%ROWTYPE; old_receipt memoriesql.idempotency_receipts%ROWTYPE;
    frame memoriesql.source_event_materializations%ROWTYPE; binding memoriesql.logical_unit_materializations%ROWTYPE;
    e jsonb; scope jsonb:=request->'scope'; carry jsonb:=request->'carry_forward'; original memoriesql.evidence_packages%ROWTYPE; n jsonb; pin jsonb; input jsonb; result jsonb;
    stable_hash text; content_hash text; identity_namespace text; req_hash text; event_hash text; declaration_hash text; unit_identity text;
    receipt_id uuid:=uuidv7(); eid uuid:=uuidv7(); uid uuid:=uuidv7(); bid uuid:=uuidv7(); tid uuid:=uuidv7(); enqueue_receipt uuid;
    parent_id uuid:=NULL; manifest_id text; existing boolean:=false;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN
        RAISE EXCEPTION 'materialization_requires_read_committed' USING ERRCODE='25000';
    END IF;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR pg_column_size(request)>16384 OR octet_length(request::text)>16384 OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','package_id','expected_inventory_sha256','producer_policy_id','expected_source_object_schema_version','source_type','scope','carry_forward']<>'{}'::jsonb OR
       (SELECT count(*) FROM jsonb_object_keys(request))<>10 OR request->'contract_version' IS DISTINCT FROM '3'::jsonb OR request->'expected_schema_version' IS DISTINCT FROM '25'::jsonb OR
       jsonb_typeof(request->'idempotency_key') IS DISTINCT FROM 'string' OR COALESCE(length(request->>'idempotency_key'),0) NOT BETWEEN 1 AND 512 OR
       COALESCE(request->>'expected_inventory_sha256','')!~'^[a-f0-9]{64}$' OR
       COALESCE(request->>'source_type','') NOT IN ('transcript','document','media','relational','operational') OR
       NOT memoriesql.declared_scope_valid(scope) OR
       (carry<>'null'::jsonb AND (jsonb_typeof(carry) IS DISTINCT FROM 'object' OR
         carry-ARRAY['original_package_id','correspondence_basis']<>'{}'::jsonb OR
         (SELECT count(*) FROM jsonb_object_keys(carry))<>2 OR
         COALESCE(carry->>'original_package_id','')!~'^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' OR
         jsonb_typeof(carry->'correspondence_basis') IS DISTINCT FROM 'string' OR
         COALESCE(length(btrim(carry->>'correspondence_basis')),0) NOT BETWEEN 1 AND 512)) THEN
        RAISE EXCEPTION 'invalid_declared_scope_contract' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
    lock_at:=clock_timestamp();
    s:=memoriesql.source_stable_authorize(p.source_object_id);
    waited:=waited+(clock_timestamp()-lock_at);
    -- The authenticated producer must own the attestation. A caller cannot
    -- adopt another principal's package merely by supplying a qualification ref.
    IF p.producer_principal_id IS DISTINCT FROM c.principal_id OR s.schema_version IS DISTINCT FROM (request->>'expected_source_object_schema_version')::integer THEN
        RAISE EXCEPTION 'materialization_authority_unavailable' USING ERRCODE='42501'; END IF;
    IF p.sealed_receipt_id IS NULL OR p.inventory_hash IS DISTINCT FROM request->>'expected_inventory_sha256' OR
       NOT memoriesql.declared_scope_package_ready(p.declaration) THEN
        RAISE EXCEPTION 'materialization_input_pending_or_conflicting' USING ERRCODE='55000'; END IF;
    SELECT * INTO policy FROM memoriesql.evidence_producer_policies WHERE tenant_id=c.tenant_id AND producer_policy_id=(request->>'producer_policy_id')::uuid AND workspace_id=c.workspace_id AND access_scope_id=s.access_scope_id AND source_object_id=s.source_object_id AND producer_principal_id=c.principal_id AND status='active' AND created_at<=clock_timestamp() AND expires_at>clock_timestamp() AND qualification_ref=p.declaration#>>'{qualification,qualification_ref}' AND normalization_policy_version=p.declaration->>'normalization_policy_version';
    IF policy.producer_policy_id IS NULL OR NOT policy.allow_declared_scopes THEN RAISE EXCEPTION 'trusted_producer_policy_unavailable' USING ERRCODE='42501'; END IF;
    identity_namespace:=policy.source_stable_namespace;
    IF identity_namespace IS NULL THEN RAISE EXCEPTION 'source_stable_policy_unavailable' USING ERRCODE='42501'; END IF;
    IF p.declaration->>'occurrence_key' IS DISTINCT FROM 'declared-scope.'||(scope->>'scope_id') OR
       p.declaration#>>'{qualification,boundary_basis}' IS DISTINCT FROM scope->>'boundary_basis' THEN
        RAISE EXCEPTION 'declared_scope_package_mismatch' USING ERRCODE='55000'; END IF;
    e:=jsonb_build_object('event_key',p.declaration->>'occurrence_key','identity_basis','producer_assigned',
        'source_type',request->>'source_type','native',p.declaration->'native','expected_units',1);
    lock_at:=clock_timestamp();
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialization-source:'||s.source_object_id::text,0));
    waited:=waited+(clock_timestamp()-lock_at);
    IF memoriesql.source_identity_mode_conflicts(c.tenant_id,s.source_object_id,identity_namespace) THEN
        RAISE EXCEPTION 'source_stable_transition_unsupported' USING ERRCODE='55000'; END IF;
    stable_hash:=encode(sha256(convert_to(jsonb_build_array('declared-scope-occurrence.v1',s.source_object_id,identity_namespace,p.declaration->>'occurrence_key')::text,'UTF8')),'hex');
    content_hash:=memoriesql.source_stable_content_hash(c.tenant_id,p.package_id);
    req_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    event_hash:=encode(sha256(convert_to(jsonb_build_array('declared-scope-event.v1',s.source_object_id,identity_namespace,e->>'event_key')::text,'UTF8')),'hex');
    declaration_hash:=encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(e),'UTF8')),'hex');
    n:=p.declaration->'native';
    unit_identity:=encode(sha256(convert_to(jsonb_build_array(event_hash,p.declaration->>'occurrence_identity_basis',n,parent_id)::text,'UTF8')),'hex');
    -- Same authority fence as storage and queue; deterministic keyed locks only.
    lock_at:=clock_timestamp();
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialize-operation:'||(request->>'idempotency_key'),0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-event:'||event_hash,0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-occurrence:'||p.occurrence_hash,0));
    PERFORM memoriesql.source_stable_authorize(s.source_object_id);
    waited:=waited+(clock_timestamp()-lock_at);
    IF clock_timestamp()-waited>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    SELECT * INTO old_receipt FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='logical_unit.materialize.v3' AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old_receipt.request_hash<>req_hash OR old_receipt.workspace_id<>c.workspace_id THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
        RETURN old_receipt.response_receipt||jsonb_build_object('replayed',true);
    END IF;
    SELECT * INTO frame FROM memoriesql.source_event_materializations WHERE tenant_id=c.tenant_id AND event_identity_hash=event_hash;
    IF FOUND THEN
        IF frame.declaration_hash<>declaration_hash THEN RAISE EXCEPTION 'logical_event_identity_conflict' USING ERRCODE='23505'; END IF;
        eid:=frame.event_id;
    END IF;
    SELECT * INTO binding FROM memoriesql.logical_unit_materializations WHERE tenant_id=c.tenant_id AND stable_identity_hash=stable_hash;
    IF FOUND THEN
        IF binding.scope_declaration IS DISTINCT FROM scope THEN
            RAISE EXCEPTION 'declared_scope_identity_conflict' USING ERRCODE='23505'; END IF;
        SELECT * INTO original FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=binding.package_id;
        IF original.declaration->>'source_revision_key'=p.declaration->>'source_revision_key' THEN
            IF memoriesql.declared_scope_membership_hash(c.tenant_id,original.package_id) IS DISTINCT FROM memoriesql.declared_scope_membership_hash(c.tenant_id,p.package_id) OR carry<>'null'::jsonb THEN
                RAISE EXCEPTION 'declared_scope_membership_conflict' USING ERRCODE='23505'; END IF;
        ELSIF carry='null'::jsonb OR carry->>'original_package_id' IS DISTINCT FROM binding.package_id::text THEN
            RAISE EXCEPTION 'declared_scope_correspondence_required' USING ERRCODE='55000';
        END IF;
        IF binding.identity_hash<>unit_identity OR binding.stable_content_hash<>content_hash THEN RAISE EXCEPTION 'logical_occurrence_identity_conflict' USING ERRCODE='23505'; END IF;
        existing:=true; eid:=binding.event_id; uid:=binding.source_unit_id; bid:=binding.bead_id; tid:=binding.task_id; pin:=binding.package_pin;
    ELSE
        IF carry<>'null'::jsonb THEN RAISE EXCEPTION 'declared_scope_carry_forward_without_binding' USING ERRCODE='55000'; END IF;
        IF scope->>'amends_source_unit_id' IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM memoriesql.logical_unit_materializations previous
            JOIN memoriesql.evidence_packages previous_package ON previous_package.tenant_id=previous.tenant_id AND previous_package.package_id=previous.package_id
            WHERE previous.tenant_id=c.tenant_id AND previous.workspace_id=c.workspace_id AND previous.access_scope_id=s.access_scope_id
              AND previous.source_unit_id=(scope->>'amends_source_unit_id')::uuid AND previous.scope_declaration IS NOT NULL
              AND previous_package.source_object_id=s.source_object_id AND previous.stable_identity_namespace=identity_namespace
        ) THEN RAISE EXCEPTION 'declared_scope_amendment_unavailable' USING ERRCODE='42501'; END IF;
        pin:=jsonb_build_object('package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,'inventory_sha256',p.inventory_hash,'required_parts',p.part_count,'required_characters',p.character_count,'required_utf8_bytes',p.utf8_byte_count,'coverage','entire_sealed_inventory');
    END IF;
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,receipt_id,'logical_unit.materialize.v3',request->>'idempotency_key',req_hash,'in_progress','source',s.source_object_id,1,clock_timestamp(),clock_timestamp());
    IF NOT existing THEN
        IF frame.event_id IS NULL THEN
            INSERT INTO memoriesql.source_events(tenant_id,workspace_id,access_scope_id,event_id,source_object_id,source_type,source_system,installation_id,external_event_id,external_id_scope,source_identity_key,session_id,actor_id,actor_kind,source_occurred_at,source_occurred_at_raw,source_sequence,source_revision_key,parser_contract_version,observation_unit_policy_version,observation_unit_count,captured_at,source_ref,content_hash,metadata,materialization_version)
            VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,eid,s.source_object_id,e->>'source_type',s.source_system,s.installation_id,e#>>'{native,native_id}','logical-events.v1:'||s.source_object_id::text,'logical-event.v1:'||event_hash,e#>>'{native,session_native_id}',e#>>'{native,participant_native_id}',e#>>'{native,role}',(e#>>'{native,occurred_at}')::timestamptz,e#>>'{native,occurred_at_raw}',(e#>>'{native,source_order}')::bigint,p.declaration->>'source_revision_key','logical-materialization.v1','logical-materialization.v1',(e->>'expected_units')::integer,clock_timestamp(),'logical-event.v1:'||event_hash,declaration_hash,jsonb_build_object('source_stable_namespace',identity_namespace,'declared_evidence_scope',scope,'declaration',e,'content_hash_kind','event_declaration_sha256','independently_proven_source_complete',false),1);
            INSERT INTO memoriesql.source_event_materializations(tenant_id,event_id,event_identity_hash,declaration,declaration_hash,initial_receipt_id) VALUES(c.tenant_id,eid,event_hash,e,declaration_hash,receipt_id);
        END IF;
        INSERT INTO memoriesql.source_units(tenant_id,workspace_id,access_scope_id,source_unit_id,event_id,parent_unit_id,unit_kind,is_observation,external_unit_id,unit_ordinal,unit_source_occurred_at,content_hash,structure,hydration_ref,schema_version,created_at,processing_receipt_id,materialization_version)
        VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uid,eid,parent_id,'logical_unit',true,n->>'native_id',(n->>'source_order')::bigint,(n->>'occurred_at')::timestamptz,p.inventory_hash,jsonb_build_object('native',n,'occurrence_identity_basis',p.declaration->>'occurrence_identity_basis','parent_resolution',CASE WHEN parent_id IS NOT NULL THEN 'canonical' WHEN n->>'parent_native_id' IS NOT NULL THEN 'native_identity_only' ELSE 'unknown' END,'content_hash_kind','sealed_inventory_sha256'),'evidence-package.'||p.package_id::text,17,clock_timestamp(),receipt_id,1);
        INSERT INTO memoriesql.beads(tenant_id,workspace_id,access_scope_id,bead_id,event_id,source_unit_id,created_at) VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,bid,eid,uid,clock_timestamp());
        manifest_id:='evidence-package.'||p.package_id::text;
        input:=jsonb_build_object('task_id',tid,'task_kind','memory.semantic.author-complete-unit','contract_revision',1,'target_reference',uid,'expected_target_revision',0,'requested_effort_key',NULL,'requested_budget',NULL,
            'evidence_manifest',jsonb_build_object('manifest_id',manifest_id,'revision',1,'references',jsonb_build_array(jsonb_build_object('reference_id',manifest_id,'content_hash',p.inventory_hash,'declared_characters',p.character_count))),
            'payload',jsonb_build_object('source_object_id',s.source_object_id,'event_id',eid,'source_unit_id',uid,'bead_id',bid,'materialization_receipt_id',receipt_id,'producer_policy_id',policy.producer_policy_id,'package',pin,'required_execution','trusted_complete_input_exposure_validation','execution_availability','unavailable'));
        SELECT q.idempotency_receipt_id INTO enqueue_receipt FROM memoriesql.enqueue_semantic_task(tid,'complete-unit.v1:'||p.occurrence_hash,'memoriesql.kernel','memory.semantic.author-complete-unit',1,'8013803a5ad67b0fe530b05c1c5badd533ae118d5105de756588a282bf6d3d3c','complete-unit-bindings-v1:8013803a5ad67b0fe530b05c1c5badd533ae118d5105de756588a282bf6d3d3c',uid::text,0,input,manifest_id,s.access_scope_id,started,NULL,started) q;
        INSERT INTO memoriesql.logical_unit_materializations VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,p.occurrence_hash,unit_identity,eid,uid,bid,tid,p.package_id,policy.producer_policy_id,pin,receipt_id,identity_namespace,stable_hash,content_hash,scope) RETURNING * INTO binding;
        INSERT INTO memoriesql.outbox_events(tenant_id,workspace_id,access_scope_id,outbox_event_id,idempotency_receipt_id,aggregate_kind,aggregate_id,event_kind,payload,headers,recorded_at,available_at)
        VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uuidv7(),receipt_id,'source_unit',uid,'source_unit.materialized',jsonb_build_object('event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'package_id',p.package_id),'{}',clock_timestamp(),clock_timestamp());
    ELSE
        SELECT idempotency_receipt_id INTO enqueue_receipt FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=tid;
    END IF;
    lock_at:=clock_timestamp();
    PERFORM memoriesql.source_stable_authorize(s.source_object_id);
    waited:=waited+(clock_timestamp()-lock_at);
    IF clock_timestamp()-waited>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    result:=jsonb_build_object('contract_version',3,'scope',scope,'carry_forward',carry,'idempotency_receipt_id',receipt_id,'initial_materialization_receipt_id',binding.initial_receipt_id,'task_enqueue_receipt_id',enqueue_receipt,'submitted_package_id',p.package_id,'bound_package',pin,'event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'status',CASE WHEN existing THEN 'already_exists' ELSE 'materialized' END,'replayed',false,'execution_availability','unavailable','execution_reason','complete_input_executor_unavailable','event_progress_at_commit',memoriesql.logical_event_progress_v1(c.tenant_id,eid));
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=receipt_id;
    RETURN result;
END; $$;

CREATE OR REPLACE FUNCTION memoriesql.inspect_stored_bead_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,memoriesql SET row_security=off SET lock_timeout='500ms'
AS $$
DECLARE
 c memoriesql.authorization_contexts%ROWTYPE;
 b memoriesql.beads%ROWTYPE; v memoriesql.bead_versions%ROWTYPE;
 u memoriesql.source_units%ROWTYPE; ev memoriesql.source_events%ROWTYPE;
 r memoriesql.semantic_task_receipts%ROWTYPE; ir memoriesql.idempotency_receipts%ROWTYPE;
 binding memoriesql.logical_unit_materializations%ROWTYPE;
 execution memoriesql.complete_input_executions%ROWTYPE;
 intent memoriesql.model_provider_request_intents%ROWTYPE;
 m memoriesql.entity_mentions%ROWTYPE; resolution memoriesql.entity_mention_resolutions%ROWTYPE;
 s memoriesql.bead_semantic_statements%ROWTYPE;
 support memoriesql.bead_semantic_statement_evidence%ROWTYPE;
 p memoriesql.evidence_packages%ROWTYPE;
 item jsonb; mentions jsonb:=NULL; statements jsonb:='[]'; evidence jsonb;
 decision jsonb; entities jsonb; meaning jsonb:=NULL; provenance jsonb:=NULL;
 result jsonb; bead_type jsonb; status text; lifecycle text:='thin';
 sources uuid[]:='{}'; source uuid; watermark bigint; amount integer;
 candidate_id uuid; candidates_authorized boolean;
 started timestamptz:=clock_timestamp(); waited interval:=interval '0'; lock_at timestamptz;
 unavailable constant jsonb:='{"contract_version":1,"outcome":"unavailable","bead":null}';
 budget constant jsonb:='{"contract_version":1,"outcome":"budget_exhausted","bead":null}';
BEGIN
 IF jsonb_typeof(request) IS DISTINCT FROM 'object'
 OR request-ARRAY['contract_version','bead_id']<>'{}'::jsonb
 OR request->>'contract_version' IS DISTINCT FROM '1'
 OR octet_length(request::text)>1024 OR request->>'bead_id' IS NULL THEN
  RAISE EXCEPTION 'invalid_stored_bead_inspection' USING ERRCODE='22023';
 END IF;
 SELECT * INTO c FROM memoriesql.current_authorization_context();
 SELECT * INTO b FROM memoriesql.beads WHERE tenant_id=c.tenant_id
  AND workspace_id=c.workspace_id AND bead_id=(request->>'bead_id')::uuid;
 IF b.bead_id IS NULL OR NOT memoriesql.current_context_event_authorized(
  b.access_scope_id,b.event_id,'memory.query','read') THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
 SELECT * INTO ev FROM memoriesql.source_events WHERE tenant_id=b.tenant_id AND event_id=b.event_id;
 SELECT * INTO u FROM memoriesql.source_units WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id;
 sources:=array_append(sources,ev.source_object_id);
 lock_at:=clock_timestamp(); PERFORM memoriesql.revisiting_source_authorize(ev.source_object_id); waited:=waited+(clock_timestamp()-lock_at);
 -- Immutable accepted identity, never newest visible version.
 SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics a
 JOIN memoriesql.bead_versions bv USING(tenant_id,bead_version_id)
 WHERE a.tenant_id=b.tenant_id AND a.bead_id=b.bead_id;
 -- Owner decision 8 of 2026-10-06: inspection withholds exactly what the query
 -- population withholds. An accepted note's family, the note and every
 -- supersession neighbour of its accepted version, must pass the population's
 -- own reads at this instant; otherwise the note reads as one that does not
 -- exist.
 IF v.bead_version_id IS NOT NULL THEN
  PERFORM memoriesql.query_bead_records_v1(b.tenant_id,b.bead_id,clock_timestamp());
  FOR candidate_id IN SELECT CASE WHEN n.bead_id=b.bead_id THEN n.superseded_bead_id ELSE n.bead_id END
    FROM memoriesql.bead_supersessions n
    JOIN memoriesql.bead_versions nv ON nv.tenant_id=n.tenant_id AND nv.bead_version_id=n.bead_version_id
    WHERE n.tenant_id=b.tenant_id AND nv.authored_at<=clock_timestamp()
     AND ((n.superseded_bead_id=b.bead_id AND n.superseded_bead_version_id=v.bead_version_id)
       OR (n.bead_id=b.bead_id AND n.bead_version_id=v.bead_version_id)) LOOP
   PERFORM memoriesql.query_bead_records_v1(b.tenant_id,candidate_id,clock_timestamp());
  END LOOP;
 END IF;
 SELECT * INTO binding FROM memoriesql.logical_unit_materializations
 WHERE tenant_id=b.tenant_id AND source_unit_id=b.source_unit_id;
 SELECT * INTO execution FROM memoriesql.complete_input_executions
 WHERE tenant_id=b.tenant_id AND binding_task_id=binding.task_id;
 IF execution.execution_task_id IS NOT NULL THEN
  SELECT q.status INTO status FROM memoriesql.semantic_tasks q
  WHERE q.tenant_id=b.tenant_id AND q.task_id=execution.execution_task_id;
  lifecycle:=CASE WHEN status IN ('failed_terminal','dead_letter','cancelled','policy_paused','superseded')
   THEN 'failed' ELSE 'pending' END;
 END IF;
 IF v.bead_version_id IS NOT NULL THEN
  IF NOT memoriesql.current_context_bead_version_authorized(v.tenant_id,v.workspace_id,v.access_scope_id,v.bead_version_id)
  THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  SELECT * INTO r FROM memoriesql.semantic_task_receipts WHERE tenant_id=v.tenant_id AND semantic_task_receipt_id=v.semantic_task_receipt_id;
  SELECT * INTO ir FROM memoriesql.idempotency_receipts WHERE tenant_id=r.tenant_id AND idempotency_receipt_id=r.idempotency_receipt_id;
  IF r.semantic_task_receipt_id IS NULL OR ir.status IS DISTINCT FROM 'succeeded' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  -- Accepted provenance is bound to its own receipt, never the newest source task.
  SELECT * INTO execution FROM memoriesql.complete_input_executions
   WHERE tenant_id=r.tenant_id AND execution_task_id=ir.resource_id;
  IF ir.operation_kind IN ('complete_input.apply.v1','complete_input.apply.v2','complete_input.apply.v3','complete_input.apply.v4','complete_input.apply.v5','complete_input.apply.v6','complete_input.apply.v7')
   AND execution.execution_task_id IS NULL THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  SELECT q.status INTO status FROM memoriesql.semantic_tasks q
   WHERE q.tenant_id=r.tenant_id AND q.task_id=execution.execution_task_id;
  -- Every accepted author can depend on optional context, including revisions 3/4
  -- without a classifier. Conservatively authorize every pinned source.
  FOR item IN SELECT value FROM jsonb_array_elements(execution.authorized_context) LOOP
   source:=(item->>'source_object_id')::uuid;
   lock_at:=clock_timestamp(); PERFORM memoriesql.revisiting_source_authorize(source); waited:=waited+(clock_timestamp()-lock_at); sources:=array_append(sources,source);
  END LOOP;
  SELECT statement_watermark INTO watermark FROM memoriesql.bead_statement_revisions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id;
  -- Owner decision 6 of 2026-10-06 (pair 4): authorize every statement under
  -- the watermark, and every source its evidence and context name, before any
  -- budget, so a note the reader cannot read never reports its size.
  IF EXISTS(SELECT 1 FROM memoriesql.bead_semantic_statements q WHERE q.tenant_id=v.tenant_id
    AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
    AND NOT memoriesql.current_context_semantic_statement_authorized(q.tenant_id,q.workspace_id,q.access_scope_id,q.statement_id))
   THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  IF EXISTS(SELECT 1 FROM memoriesql.bead_semantic_statements q, unnest(q.context_source_ids) cs(unit_id)
    WHERE q.tenant_id=v.tenant_id AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
     AND NOT EXISTS(SELECT 1 FROM memoriesql.source_units cu WHERE cu.tenant_id=q.tenant_id AND cu.source_unit_id=cs.unit_id))
   THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
  FOR source IN SELECT DISTINCT o.source_object_id FROM memoriesql.bead_semantic_statements q
    JOIN LATERAL (
     SELECT e.source_object_id FROM memoriesql.bead_semantic_statement_evidence x
      JOIN memoriesql.source_events e ON e.tenant_id=x.tenant_id AND e.event_id=x.evidence_event_id
      WHERE x.tenant_id=q.tenant_id AND x.statement_id=q.statement_id
     UNION ALL
     SELECT e.source_object_id FROM unnest(q.context_source_ids) cs(unit_id)
      JOIN memoriesql.source_units cu ON cu.tenant_id=q.tenant_id AND cu.source_unit_id=cs.unit_id
      JOIN memoriesql.source_events e ON e.tenant_id=cu.tenant_id AND e.event_id=cu.event_id) o ON true
    WHERE q.tenant_id=v.tenant_id AND q.bead_id=v.bead_id AND q.statement_sequence<=watermark
    ORDER BY 1 LOOP
   lock_at:=clock_timestamp(); PERFORM memoriesql.revisiting_source_authorize(source); waited:=waited+(clock_timestamp()-lock_at);
  END LOOP;
  -- Owner decision 6 of 2026-10-06 (pair 4): the classification contribution's
  -- checks, moved unchanged from after the mentions, deny before any budget.
  IF v.classification_contribution IS NOT NULL THEN
   IF r.task_contract_version<>5 OR r.task_contract_key<>'memory.semantic.author-complete-unit'
    OR ir.operation_kind<>'complete_input.apply.v4' OR ir.resource_id IS DISTINCT FROM execution.execution_task_id THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   SELECT * INTO intent FROM memoriesql.model_provider_request_intents WHERE tenant_id=v.tenant_id
    AND request_id=(v.classification_contribution->>'request_id')::uuid AND task_id=ir.resource_id;
   IF intent.request_id IS NULL OR intent.workspace_id<>c.workspace_id OR
    NOT memoriesql.current_context_scope_authorized(intent.access_scope_id,'memory.query','read') THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   FOR item IN SELECT value FROM jsonb_array_elements(v.classification_contribution->'evidence') LOOP
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=v.tenant_id AND package_id=(item#>>'{selection,package_id}')::uuid;
    IF p.package_id IS NULL OR p.inventory_hash IS DISTINCT FROM item#>>'{selection,inventory_sha256}' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
    lock_at:=clock_timestamp(); PERFORM memoriesql.revisiting_source_authorize(p.source_object_id); waited:=waited+(clock_timestamp()-lock_at); sources:=array_append(sources,p.source_object_id);
   END LOOP;
   provenance:=jsonb_build_object('request_id',intent.request_id,'task_id',intent.task_id,'attempt_id',intent.attempt_id,
    'model_id',intent.model_id,'model_profile_key',intent.model_profile_key,'model_profile_revision',intent.model_profile_revision,
    'authorization_policy_revision',intent.authorization_policy_revision,'quality_policy_revision_id',intent.quality_policy_revision_id,'pricing_revision_id',intent.pricing_revision_id);
  ELSIF r.task_contract_version=5 AND r.task_contract_key='memory.semantic.author-complete-unit' THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501';
  END IF;
  SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statements
   WHERE tenant_id=v.tenant_id AND bead_id=v.bead_id AND statement_sequence<=watermark LIMIT 33) bounded;
  IF amount>32 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
  FOR s IN SELECT * FROM memoriesql.bead_semantic_statements WHERE tenant_id=v.tenant_id
   AND bead_id=v.bead_id AND statement_sequence<=watermark ORDER BY statement_sequence LOOP
   IF NOT memoriesql.current_context_semantic_statement_authorized(s.tenant_id,s.workspace_id,s.access_scope_id,s.statement_id)
    THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
   -- Context is provenance, not support; still requires authorization before disclosure.
   FOREACH source IN ARRAY s.context_source_ids LOOP
    -- A context source is a unit of the statement's own evidence. Authorize
    -- its event's source object, as evidence is; missing or unreadable reads
    -- exactly as an unknown note.
    SELECT e.source_object_id INTO source FROM memoriesql.source_units cu
     JOIN memoriesql.source_events e ON e.tenant_id=cu.tenant_id AND e.event_id=cu.event_id
     WHERE cu.tenant_id=s.tenant_id AND cu.source_unit_id=source;
    IF NOT FOUND THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
    lock_at:=clock_timestamp(); PERFORM memoriesql.revisiting_source_authorize(source); waited:=waited+(clock_timestamp()-lock_at); sources:=array_append(sources,source);
   END LOOP;
   evidence:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statement_evidence
    WHERE tenant_id=s.tenant_id AND statement_id=s.statement_id LIMIT 65) bounded;
   IF amount>64 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
   FOR support IN SELECT * FROM memoriesql.bead_semantic_statement_evidence
    WHERE tenant_id=s.tenant_id AND statement_id=s.statement_id ORDER BY evidence_source_unit_id LOOP
    SELECT source_object_id INTO source FROM memoriesql.source_events WHERE tenant_id=s.tenant_id AND event_id=support.evidence_event_id;
    lock_at:=clock_timestamp(); PERFORM memoriesql.revisiting_source_authorize(source); waited:=waited+(clock_timestamp()-lock_at); sources:=array_append(sources,source);
    evidence:=evidence||jsonb_build_array(jsonb_build_object('event_id',support.evidence_event_id,
     'source_unit_id',support.evidence_source_unit_id,'content_sha256',support.evidence_content_hash));
   END LOOP;
   statements:=statements||jsonb_build_array(jsonb_build_object('statement_id',s.statement_id,
    'sequence',s.statement_sequence,'kind',s.statement_kind,'text',s.statement_text,
    'supersedes_statement_id',s.supersedes_statement_id,'correction_reason',s.correction_reason,'evidence',evidence));
  END LOOP;
  -- Capability derives ONLY from this accepted result's own successful apply receipt.
  IF r.task_contract_key='memory.semantic.author-complete-unit' AND
    ((r.task_contract_version=4 AND ir.operation_kind='complete_input.apply.v3') OR
     (r.task_contract_version=5 AND ir.operation_kind='complete_input.apply.v4') OR
     (r.task_contract_version=6 AND ir.operation_kind='complete_input.apply.v5') OR
     (r.task_contract_version=7 AND ir.operation_kind='complete_input.apply.v6')) THEN
   mentions:='[]';
   SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.entity_mentions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id LIMIT 33) bounded;
   IF amount>32 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
   FOR m IN SELECT * FROM memoriesql.entity_mentions WHERE tenant_id=v.tenant_id AND bead_version_id=v.bead_version_id ORDER BY entity_mention_id LOOP
    SELECT * INTO resolution FROM memoriesql.entity_mention_resolutions WHERE tenant_id=m.tenant_id AND entity_mention_id=m.entity_mention_id
     ORDER BY decision_sequence DESC LIMIT 1;
    -- Both absent and protected global decisions are unavailable. Local authored identity is separate.
    decision:=jsonb_build_object('availability','unavailable','resolution_id',NULL,'status',NULL,'entity_ids',NULL);
    IF resolution.entity_mention_resolution_id IS NOT NULL AND
      memoriesql.current_context_bead_version_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,resolution.evidence_bead_version_id)
      AND (resolution.resolved_entity_id IS NULL OR memoriesql.current_context_entity_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,resolution.resolved_entity_id)) THEN
     -- Materialize at most 65 candidates once. Never invoke the unbounded aggregate
     -- predicate or re-query the full set after the bound (including under insert races).
     SELECT COALESCE(jsonb_agg(candidate_entity_id ORDER BY candidate_ordinal),'[]') INTO entities
      FROM (SELECT candidate_entity_id,candidate_ordinal FROM memoriesql.entity_resolution_candidates
       WHERE tenant_id=m.tenant_id AND entity_mention_resolution_id=resolution.entity_mention_resolution_id
       ORDER BY candidate_ordinal LIMIT 65) bounded;
     amount:=jsonb_array_length(entities);
     -- An oversized decision stays indistinguishable from a protected/absent decision.
     IF amount<=64 THEN
      candidates_authorized:=resolution.resolution_status<>'ambiguous' OR amount>=2;
      FOR candidate_id IN SELECT value::uuid FROM jsonb_array_elements_text(entities) LOOP
       IF NOT memoriesql.current_context_entity_authorized(resolution.tenant_id,resolution.workspace_id,resolution.access_scope_id,candidate_id) THEN
        candidates_authorized:=false; EXIT;
       END IF;
      END LOOP;
      IF candidates_authorized THEN
       IF resolution.resolved_entity_id IS NOT NULL THEN entities:=jsonb_build_array(resolution.resolved_entity_id); END IF;
       decision:=jsonb_build_object('availability','available','resolution_id',resolution.entity_mention_resolution_id,'status',resolution.resolution_status,'entity_ids',entities);
      END IF;
     END IF;
    END IF;
    mentions:=mentions||jsonb_build_array(jsonb_build_object('entity_mention_id',m.entity_mention_id,'surface_text',m.surface_text,
     'local_identity_state',m.local_identity_state,'local_identity_reason',m.local_identity_reason,'resolution',decision));
   END LOOP;
  END IF;
  SELECT jsonb_build_object('key',t.stable_key,'revision',tr.revision,'definition',tr.definition) INTO bead_type
   FROM memoriesql.bead_types t JOIN memoriesql.bead_type_revisions tr USING(bead_type_id) WHERE tr.bead_type_revision_id=v.bead_type_revision_id;
  meaning:=jsonb_build_object('bead_version_id',v.bead_version_id,'receipt_id',r.semantic_task_receipt_id,
   'task_contract_key',r.task_contract_key,'task_contract_version',r.task_contract_version,'bead_type',bead_type,
   'render_contract_revision',v.render_contract_revision,'render',CASE WHEN v.render_contract_revision=2 THEN v.render_payload END,'statements',statements,
   'mentions',mentions,'classification',v.classification_contribution,'classification_vocabulary',CASE WHEN v.classification_contribution IS NOT NULL THEN execution.classification_vocabulary END,'classification_provenance',provenance);
  lifecycle:='accepted';
 END IF;
 result:=jsonb_build_object('contract_version',1,'outcome','available','bead',jsonb_build_object(
  'bead_id',b.bead_id,'event_id',b.event_id,'source_object_id',ev.source_object_id,'source_unit_id',b.source_unit_id,
  'parent_source_unit_id',u.parent_unit_id,'parent_resolution',COALESCE(u.structure->>'parent_resolution','unknown'),
  'package',binding.package_pin,'binding_task_id',binding.task_id,'execution_task_id',execution.execution_task_id,
  'execution_status',status,'lifecycle',lifecycle,'meaning',meaning));
 -- Revalidate all disclosed dependencies at the return boundary; audit writes only.
 FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
  lock_at:=clock_timestamp(); PERFORM memoriesql.revisiting_source_authorize(source); waited:=waited+(clock_timestamp()-lock_at);
 END LOOP;
 -- Owner decision 6 of 2026-10-06 (pair 4): budgets only after every
 -- authorization.
 IF octet_length(memoriesql.canonical_semantic_json_text(result))>262144 THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
 IF clock_timestamp()-started-waited>interval '2 seconds' THEN RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000'; END IF;
 RETURN result;
EXCEPTION WHEN insufficient_privilege THEN RETURN unavailable;
 WHEN program_limit_exceeded THEN RETURN budget;
END;
$$;
