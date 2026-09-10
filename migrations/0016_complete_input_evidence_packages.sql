-- Public-owned evidence inventory. No source event, bead, task or model effects.
-- Forward only; migrations 0001-0015 remain byte-for-byte unchanged.
CREATE TABLE memoriesql.evidence_packages (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    package_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    occurrence_hash text NOT NULL,
    declaration jsonb NOT NULL,
    declaration_hash text NOT NULL,
    producer_principal_id uuid NOT NULL,
    created_receipt_id uuid NOT NULL,
    sealed_receipt_id uuid,
    part_count integer NOT NULL DEFAULT 0 CHECK (part_count BETWEEN 0 AND 256),
    character_count integer NOT NULL DEFAULT 0 CHECK (character_count BETWEEN 0 AND 16777216),
    utf8_byte_count integer NOT NULL DEFAULT 0 CHECK (utf8_byte_count BETWEEN 0 AND 16777216),
    inventory_hash text,
    PRIMARY KEY (tenant_id, package_id),
    UNIQUE (tenant_id, occurrence_hash, declaration_hash),
    FOREIGN KEY (tenant_id,workspace_id,access_scope_id,source_object_id)
        REFERENCES memoriesql.source_objects(tenant_id,workspace_id,access_scope_id,source_object_id),
    FOREIGN KEY (tenant_id,created_receipt_id) REFERENCES memoriesql.idempotency_receipts(tenant_id,idempotency_receipt_id),
    FOREIGN KEY (tenant_id,sealed_receipt_id) REFERENCES memoriesql.idempotency_receipts(tenant_id,idempotency_receipt_id),
    CHECK (jsonb_typeof(declaration) = 'object'),
    CHECK (octet_length(memoriesql.canonical_semantic_json_text(declaration)) <= 8192),
    CHECK (declaration_hash = encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(declaration),'UTF8')),'hex')),
    CHECK ((sealed_receipt_id IS NULL) = (inventory_hash IS NULL))
);
CREATE UNIQUE INDEX evidence_package_representation_identity ON memoriesql.evidence_packages (
    tenant_id, occurrence_hash, (declaration ->> 'normalization_policy_version'),
    (declaration ->> 'package_revision')
);
CREATE TABLE memoriesql.evidence_package_parts (
    tenant_id uuid NOT NULL,
    package_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 255),
    part_id uuid NOT NULL,
    inventory jsonb NOT NULL,
    inventory_hash text NOT NULL,
    content text NOT NULL,
    appended_receipt_id uuid NOT NULL,
    PRIMARY KEY (tenant_id,package_id,ordinal),
    UNIQUE (tenant_id,package_id,part_id),
    FOREIGN KEY (tenant_id,package_id) REFERENCES memoriesql.evidence_packages(tenant_id,package_id),
    FOREIGN KEY (tenant_id,appended_receipt_id) REFERENCES memoriesql.idempotency_receipts(tenant_id,idempotency_receipt_id),
    CHECK (octet_length(content) BETWEEN 1 AND 65536),
    CHECK (octet_length(memoriesql.canonical_semantic_json_text(inventory)) <= 8192),
    CHECK (inventory_hash = encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(inventory),'UTF8')),'hex')),
    CHECK (inventory ->> 'content_sha256' = encode(sha256(convert_to(content,'UTF8')),'hex')),
    CHECK ((inventory ->> 'characters')::integer = char_length(content)),
    CHECK ((inventory ->> 'utf8_bytes')::integer = octet_length(content)),
    CHECK ((inventory ->> 'ordinal')::integer = ordinal),
    CHECK ((inventory ->> 'part_id')::uuid = part_id)
);

CREATE FUNCTION memoriesql.guard_evidence_package_immutability() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, memoriesql AS $$
BEGIN
    IF TG_TABLE_NAME = 'evidence_package_parts' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'evidence_package_immutable' USING ERRCODE='55000';
    END IF;
    IF OLD.sealed_receipt_id IS NOT NULL OR
       (to_jsonb(NEW) - ARRAY['part_count','character_count','utf8_byte_count','inventory_hash','sealed_receipt_id'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['part_count','character_count','utf8_byte_count','inventory_hash','sealed_receipt_id']) THEN
        RAISE EXCEPTION 'evidence_package_immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER evidence_package_immutable BEFORE UPDATE OR DELETE ON memoriesql.evidence_packages
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_evidence_package_immutability();
CREATE TRIGGER evidence_part_immutable BEFORE UPDATE OR DELETE ON memoriesql.evidence_package_parts
FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_evidence_package_immutability();
ALTER TABLE memoriesql.evidence_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.evidence_packages FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.evidence_package_parts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.evidence_package_parts FORCE ROW LEVEL SECURITY;
-- No table grants. All content access passes through the audited typed reader.

CREATE FUNCTION memoriesql.evidence_package_authorize(source_id uuid, writing boolean)
RETURNS memoriesql.source_objects LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, memoriesql SET lock_timeout = '500ms' AS $$
DECLARE s memoriesql.source_objects%ROWTYPE; c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO s FROM memoriesql.source_objects
      WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND source_object_id=source_id;
    IF s.source_object_id IS NULL OR NOT memoriesql.current_context_source_authorized(
        s.access_scope_id,source_id,'source.raw.read','read') OR
        (writing AND NOT memoriesql.current_context_source_authorized(s.access_scope_id,source_id,'memory.capture','write')) THEN
        RAISE EXCEPTION 'evidence_unavailable' USING ERRCODE='42501';
    END IF;
    -- Reuse the existing authority mutation fence. Do not create another lock regime.
    PERFORM pg_advisory_xact_lock_shared(hashtextextended(c.tenant_id::text || ':semantic_outcome_authority:',0));
    IF NOT memoriesql.application_authorize_resource('source',source_id,'source.raw.read','read',uuidv7()) OR
       (writing AND NOT memoriesql.application_authorize_resource('source',source_id,'memory.capture','write',uuidv7())) THEN
        RAISE EXCEPTION 'evidence_unavailable' USING ERRCODE='42501';
    END IF;
    RETURN s;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.evidence_package_authorize(uuid,boolean) FROM PUBLIC;


CREATE FUNCTION memoriesql.evidence_native_facts_valid(facts jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE item record;
BEGIN
    IF jsonb_typeof(facts) IS DISTINCT FROM 'object' OR
       facts-ARRAY['native_id','parent_native_id','session_native_id','branch_native_id','participant_native_id','role','source_order','occurred_at','occurred_at_raw','time_precision']<>'{}'::jsonb THEN RETURN false; END IF;
    FOR item IN SELECT * FROM jsonb_each(facts) LOOP
        IF item.value='null'::jsonb THEN CONTINUE; END IF;
        IF item.key='source_order' THEN
            IF jsonb_typeof(item.value)<>'number' OR item.value::text !~ '^[0-9]+$' THEN RETURN false; END IF;
        ELSIF jsonb_typeof(item.value)<>'string' OR char_length(item.value#>>'{}')>(CASE item.key WHEN 'role' THEN 128 WHEN 'time_precision' THEN 64 ELSE 256 END) THEN RETURN false;
        END IF;
        IF item.key='occurred_at' THEN
            IF (item.value#>>'{}') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' THEN RETURN false; END IF;
            PERFORM (item.value#>>'{}')::timestamptz;
        END IF;
    END LOOP;
    RETURN true;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.evidence_native_facts_valid(jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.evidence_qualification_valid(q jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
BEGIN
    IF jsonb_typeof(q) IS DISTINCT FROM 'object' OR
       q-ARRAY['qualification_ref','boundary','boundary_basis','physical_records','topology','normalized_input','source_completeness','exclusions','unresolved_coverage']<>'{}'::jsonb OR
       jsonb_typeof(q->'qualification_ref') IS DISTINCT FROM 'string' OR
       jsonb_typeof(q->'boundary_basis') IS DISTINCT FROM 'string' OR
       COALESCE(length(q->>'qualification_ref'),0) NOT BETWEEN 1 AND 256 OR
       COALESCE(length(q->>'boundary_basis'),0) NOT BETWEEN 1 AND 512 OR
       COALESCE(q->>'boundary','') NOT IN ('qualified_native_unit','unresolved') OR
       COALESCE(q->>'physical_records','') NOT IN ('complete','pending_tail') OR
       COALESCE(q->>'topology','') NOT IN ('known','partially_known','unknown') OR
       COALESCE(q->>'normalized_input','') NOT IN ('complete','incomplete') OR
       COALESCE(q->>'source_completeness','') NOT IN ('producer_attested','unresolved') OR
       jsonb_typeof(q->'exclusions') IS DISTINCT FROM 'array' OR
       jsonb_typeof(q->'unresolved_coverage') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    RETURN jsonb_array_length(q->'exclusions')<=16 AND jsonb_array_length(q->'unresolved_coverage')<=16 AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements((q->'exclusions')||(q->'unresolved_coverage')) AS a(v)
        WHERE jsonb_typeof(v)<>'string' OR char_length(v#>>'{}') NOT BETWEEN 1 AND 256
    );
END; $$;
REVOKE ALL ON FUNCTION memoriesql.evidence_qualification_valid(jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.write_evidence_package_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, memoriesql
SET lock_timeout = '500ms' AS $$
DECLARE
    started timestamptz := clock_timestamp();
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
        s := memoriesql.evidence_package_authorize((d->>'source_object_id')::uuid,true);
    ELSE
        IF request - (CASE op WHEN 'append' THEN ARRAY['contract_version','operation','idempotency_key','package_id','part'] ELSE ARRAY['contract_version','operation','idempotency_key','package_id'] END) <> '{}'::jsonb THEN
            RAISE EXCEPTION 'invalid_evidence_contract' USING ERRCODE='22023';
        END IF;
        SELECT * INTO c FROM memoriesql.current_authorization_context();
        SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND package_id=(request->>'package_id')::uuid;
        s := memoriesql.evidence_package_authorize(p.source_object_id,true);
        d := p.declaration;
    END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    -- Order: authority, operation idempotency, occurrence. Recheck after waits.
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text || ':evidence-operation:' || (request->>'idempotency_key'),0));
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
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text || ':evidence-occurrence:' || occ_hash,0));
    PERFORM memoriesql.evidence_package_authorize(s.source_object_id,true);
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
        SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=c.tenant_id AND package_id=p.package_id FOR UPDATE;
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
                    IF clock_timestamp()-started>interval '2 seconds' THEN RAISE EXCEPTION 'evidence_work_timeout' USING ERRCODE='57014'; END IF;
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
    PERFORM memoriesql.evidence_package_authorize(s.source_object_id,true);
    IF clock_timestamp()-started>interval '2 seconds' THEN RAISE EXCEPTION 'evidence_work_timeout' USING ERRCODE='57014'; END IF;
    result:=jsonb_build_object('contract_version',1,'package_id',p.package_id,'idempotency_receipt_id',new_receipt,'operation',op,'replayed',false,'already_exists',existing,'sealed',p.sealed_receipt_id IS NOT NULL,'appended_parts',p.part_count);
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=new_receipt;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.write_evidence_package_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.write_evidence_package_v1(jsonb) TO memoriesql_application;

CREATE FUNCTION memoriesql.read_evidence_package_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql
SET lock_timeout='500ms' AS $$
DECLARE
    started timestamptz:=clock_timestamp(); c memoriesql.authorization_contexts%ROWTYPE;
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
    PERFORM memoriesql.evidence_package_authorize(p.source_object_id,false);
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
    PERFORM memoriesql.evidence_package_authorize(p.source_object_id,false);
    IF octet_length(memoriesql.canonical_semantic_json_text(result))>65536 THEN RAISE EXCEPTION 'evidence_response_bound' USING ERRCODE='22023'; END IF;
    IF clock_timestamp()-started>interval '2 seconds' THEN RAISE EXCEPTION 'evidence_work_timeout' USING ERRCODE='57014'; END IF;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.read_evidence_package_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.read_evidence_package_v1(jsonb) TO memoriesql_application;
REVOKE ALL ON FUNCTION memoriesql.guard_evidence_package_immutability() FROM PUBLIC;
