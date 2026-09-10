-- Public-owned forward transition. Published migrations and v1/v2 payloads unchanged.
-- One qualified occurrence per transaction; no authoring executor is admitted.
ALTER TABLE memoriesql.source_events ADD COLUMN materialization_version integer CHECK (materialization_version = 1);
ALTER TABLE memoriesql.source_events ALTER COLUMN actor_id DROP NOT NULL;
ALTER TABLE memoriesql.source_events ALTER COLUMN actor_kind DROP NOT NULL;
ALTER TABLE memoriesql.source_events ALTER COLUMN observation_unit_count DROP NOT NULL;
ALTER TABLE memoriesql.source_events ADD CONSTRAINT source_events_legacy_required CHECK (
    materialization_version IS NOT NULL OR (actor_id IS NOT NULL AND actor_kind IS NOT NULL AND observation_unit_count IS NOT NULL));
ALTER TABLE memoriesql.source_units ADD COLUMN materialization_version integer CHECK (materialization_version = 1);
ALTER TABLE memoriesql.source_units ALTER COLUMN external_unit_id DROP NOT NULL;
ALTER TABLE memoriesql.source_units ALTER COLUMN unit_ordinal DROP NOT NULL;
ALTER TABLE memoriesql.source_units DROP CONSTRAINT source_units_kind_supported;
ALTER TABLE memoriesql.source_units ADD CONSTRAINT source_units_kind_supported CHECK (
    (materialization_version IS NULL AND unit_kind IN ('turn','section','chunk','element','media_window','record_change') AND external_unit_id IS NOT NULL AND unit_ordinal IS NOT NULL)
    OR (materialization_version IS NOT NULL AND materialization_version = 1 AND unit_kind = 'logical_unit' AND is_observation AND content_text IS NULL));
-- A source revision may contain multiple genuine events. Native ordering facts
-- in the new path are evidence, not an invented globally unique sequence.
DROP INDEX memoriesql.source_events_revision_uq;
CREATE UNIQUE INDEX source_events_revision_uq ON memoriesql.source_events(tenant_id,source_object_id,source_revision_key) WHERE source_revision_key IS NOT NULL AND materialization_version IS NULL;
DROP INDEX memoriesql.source_events_sequence_uq;
CREATE UNIQUE INDEX source_events_sequence_uq ON memoriesql.source_events(tenant_id,source_object_id,source_sequence) WHERE source_sequence IS NOT NULL AND materialization_version IS NULL;
DROP INDEX memoriesql.source_units_external_id_uq;
CREATE UNIQUE INDEX source_units_external_id_uq ON memoriesql.source_units(tenant_id,event_id,external_unit_id) WHERE materialization_version IS NULL;
DROP INDEX memoriesql.source_units_root_ordinal_uq;
CREATE UNIQUE INDEX source_units_root_ordinal_uq ON memoriesql.source_units(tenant_id,event_id,unit_ordinal) WHERE parent_unit_id IS NULL AND materialization_version IS NULL;
DROP INDEX memoriesql.source_units_child_ordinal_uq;
CREATE UNIQUE INDEX source_units_child_ordinal_uq ON memoriesql.source_units(tenant_id,event_id,parent_unit_id,unit_ordinal) WHERE parent_unit_id IS NOT NULL AND materialization_version IS NULL;

-- Installation-administrator provisioned, default deny. These rows identify a
-- reviewed trust decision; neither a producer string nor an artifact hash proves
-- source completeness. No application write grant or production policy is seeded.
CREATE TABLE memoriesql.evidence_producer_policies (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    producer_policy_id uuid NOT NULL,
    source_object_id uuid NOT NULL,
    producer_principal_id uuid NOT NULL,
    qualification_ref text NOT NULL CHECK (length(qualification_ref) BETWEEN 1 AND 256),
    normalization_policy_version text NOT NULL CHECK (length(normalization_policy_version) BETWEEN 1 AND 128),
    qualification_evidence_sha256 text NOT NULL CHECK (qualification_evidence_sha256 ~ '^[a-f0-9]{64}$'),
    approved_by_principal_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
    status text NOT NULL CHECK (status IN ('active','revoked')),
    PRIMARY KEY (tenant_id,producer_policy_id),
    FOREIGN KEY (tenant_id,workspace_id,access_scope_id,source_object_id) REFERENCES memoriesql.source_objects(tenant_id,workspace_id,access_scope_id,source_object_id),
    FOREIGN KEY (tenant_id,producer_principal_id) REFERENCES memoriesql.principals(tenant_id,principal_id),
    FOREIGN KEY (tenant_id,approved_by_principal_id) REFERENCES memoriesql.principals(tenant_id,principal_id)
);
CREATE TABLE memoriesql.source_event_materializations (
    tenant_id uuid NOT NULL,
    event_id uuid NOT NULL,
    event_identity_hash text NOT NULL,
    declaration jsonb NOT NULL,
    declaration_hash text NOT NULL,
    initial_receipt_id uuid NOT NULL,
    materialized_unit_count integer NOT NULL DEFAULT 0 CHECK (materialized_unit_count >= 0),
    PRIMARY KEY (tenant_id,event_id),
    UNIQUE (tenant_id,event_identity_hash),
    FOREIGN KEY (tenant_id,event_id) REFERENCES memoriesql.source_events(tenant_id,event_id),
    FOREIGN KEY (tenant_id,initial_receipt_id) REFERENCES memoriesql.idempotency_receipts(tenant_id,idempotency_receipt_id),
    CHECK (octet_length(memoriesql.canonical_semantic_json_text(declaration)) <= 8192),
    CHECK (declaration_hash = encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(declaration),'UTF8')),'hex')),
    CHECK (declaration->>'expected_units' IS NULL OR materialized_unit_count <= (declaration->>'expected_units')::integer)
);
CREATE TABLE memoriesql.logical_unit_materializations (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    occurrence_hash text NOT NULL,
    identity_hash text NOT NULL,
    event_id uuid NOT NULL,
    source_unit_id uuid NOT NULL,
    bead_id uuid NOT NULL,
    task_id uuid NOT NULL,
    package_id uuid NOT NULL,
    producer_policy_id uuid NOT NULL,
    package_pin jsonb NOT NULL,
    initial_receipt_id uuid NOT NULL,
    PRIMARY KEY (tenant_id,occurrence_hash),
    UNIQUE (tenant_id,source_unit_id),
    UNIQUE (tenant_id,task_id),
    FOREIGN KEY (tenant_id,workspace_id,access_scope_id,event_id,source_unit_id,bead_id) REFERENCES memoriesql.beads(tenant_id,workspace_id,access_scope_id,event_id,source_unit_id,bead_id),
    FOREIGN KEY (tenant_id,task_id) REFERENCES memoriesql.semantic_tasks(tenant_id,task_id),
    FOREIGN KEY (tenant_id,package_id) REFERENCES memoriesql.evidence_packages(tenant_id,package_id),
    FOREIGN KEY (tenant_id,producer_policy_id) REFERENCES memoriesql.evidence_producer_policies(tenant_id,producer_policy_id),
    FOREIGN KEY (tenant_id,initial_receipt_id) REFERENCES memoriesql.idempotency_receipts(tenant_id,idempotency_receipt_id),
    FOREIGN KEY (tenant_id,event_id) REFERENCES memoriesql.source_event_materializations(tenant_id,event_id)
);
ALTER TABLE memoriesql.evidence_producer_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.evidence_producer_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_event_materializations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.source_event_materializations FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.logical_unit_materializations ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.logical_unit_materializations FORCE ROW LEVEL SECURITY;
CREATE FUNCTION memoriesql.guard_materialization_identity() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
BEGIN
    IF TG_OP='UPDATE' AND TG_TABLE_NAME='evidence_producer_policies' THEN
        IF OLD.status='active' AND NEW.status='revoked' AND (to_jsonb(OLD)-'status')=(to_jsonb(NEW)-'status') THEN RETURN NEW; END IF;
    END IF;
    IF TG_OP='UPDATE' AND TG_TABLE_NAME='source_event_materializations' THEN
        IF NEW.materialized_unit_count=OLD.materialized_unit_count+1 AND (to_jsonb(OLD)-'materialized_unit_count')=(to_jsonb(NEW)-'materialized_unit_count') THEN RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'materialization_identity_immutable' USING ERRCODE='55000';
END; $$;
CREATE TRIGGER producer_policy_immutable BEFORE UPDATE OR DELETE ON memoriesql.evidence_producer_policies FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_materialization_identity();
CREATE TRIGGER producer_policy_authority_fence BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.evidence_producer_policies FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();
CREATE TRIGGER logical_event_identity_immutable BEFORE UPDATE OR DELETE ON memoriesql.source_event_materializations FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_materialization_identity();
CREATE TRIGGER logical_unit_binding_immutable BEFORE UPDATE OR DELETE ON memoriesql.logical_unit_materializations FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_materialization_identity();

CREATE FUNCTION memoriesql.complete_unit_input_valid(candidate jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,memoriesql AS $$
DECLARE payload jsonb:=candidate->'payload'; pin jsonb:=payload->'package'; manifest jsonb:=candidate->'evidence_manifest'; k text;
BEGIN
    IF octet_length(candidate::text)>16384 OR jsonb_typeof(candidate) IS DISTINCT FROM 'object' OR
       candidate-ARRAY['task_id','task_kind','contract_revision','target_reference','expected_target_revision','evidence_manifest','requested_effort_key','requested_budget','payload']<>'{}'::jsonb OR (SELECT count(*) FROM jsonb_object_keys(candidate))<>9 OR
       candidate->>'task_kind' IS DISTINCT FROM 'memory.semantic.author-complete-unit' OR candidate->'contract_revision' IS DISTINCT FROM '1'::jsonb OR candidate->'expected_target_revision' IS DISTINCT FROM '0'::jsonb OR
       candidate->'requested_effort_key' IS DISTINCT FROM 'null'::jsonb OR candidate->'requested_budget' IS DISTINCT FROM 'null'::jsonb OR
       jsonb_typeof(payload) IS DISTINCT FROM 'object' OR payload-ARRAY['source_object_id','event_id','source_unit_id','bead_id','materialization_receipt_id','producer_policy_id','package','required_execution','execution_availability']<>'{}'::jsonb OR (SELECT count(*) FROM jsonb_object_keys(payload))<>9 OR
       payload->>'required_execution' IS DISTINCT FROM 'trusted_complete_input_exposure_validation' OR payload->>'execution_availability' IS DISTINCT FROM 'unavailable' OR
       jsonb_typeof(pin) IS DISTINCT FROM 'object' OR pin-ARRAY['package_id','sealed_receipt_id','inventory_sha256','required_parts','required_characters','required_utf8_bytes','coverage']<>'{}'::jsonb OR (SELECT count(*) FROM jsonb_object_keys(pin))<>7 OR
       pin->>'coverage' IS DISTINCT FROM 'entire_sealed_inventory' OR COALESCE(pin->>'inventory_sha256','')!~'^[a-f0-9]{64}$' OR
       COALESCE((pin->>'required_parts')::integer,0) NOT BETWEEN 1 AND 256 OR COALESCE((pin->>'required_characters')::integer,0) NOT BETWEEN 1 AND 16777216 OR COALESCE((pin->>'required_utf8_bytes')::integer,0) NOT BETWEEN 1 AND 16777216 OR
       candidate->>'target_reference' IS DISTINCT FROM payload->>'source_unit_id' OR
       jsonb_typeof(manifest) IS DISTINCT FROM 'object' OR manifest-ARRAY['manifest_id','references','revision']<>'{}'::jsonb OR (SELECT count(*) FROM jsonb_object_keys(manifest))<>3 OR manifest->'revision' IS DISTINCT FROM '1'::jsonb OR
       COALESCE(length(manifest->>'manifest_id'),0) NOT BETWEEN 1 AND 256 OR
       manifest->'references' IS DISTINCT FROM jsonb_build_array(jsonb_build_object('reference_id','evidence-package.'||(pin->>'package_id'),'content_hash',pin->>'inventory_sha256','declared_characters',(pin->>'required_characters')::integer)) THEN RETURN false; END IF;
    IF (candidate->>'task_id')::uuid IS NULL OR (pin->>'package_id')::uuid IS NULL OR (pin->>'sealed_receipt_id')::uuid IS NULL THEN RETURN false; END IF;
    FOREACH k IN ARRAY ARRAY['source_object_id','event_id','source_unit_id','bead_id','materialization_receipt_id','producer_policy_id'] LOOP
        IF (payload->>k)::uuid IS NULL THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END; $$;

-- Unavailable execution is an enforced queue state, not a scheduling hint.
CREATE FUNCTION memoriesql.guard_complete_unit_execution() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
DECLARE complete boolean;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' THEN RETURN NEW; END IF;
        IF TG_OP='INSERT' THEN NEW.status:='policy_paused'; NEW.pause_reason_code:='complete_input_executor_unavailable'; END IF;
        IF NEW.status NOT IN ('policy_paused','cancelled','failed_terminal') OR NEW.attempt_count<>0 OR NEW.lease_generation<>0 OR
           (NEW.status='policy_paused' AND NEW.pause_reason_code IS DISTINCT FROM 'complete_input_executor_unavailable') THEN
            RAISE EXCEPTION 'complete_input_executor_unavailable' USING ERRCODE='55000'; END IF;
    ELSIF TG_TABLE_NAME='semantic_task_attempts' THEN
        SELECT task_kind='memory.semantic.author-complete-unit' INTO complete FROM memoriesql.semantic_tasks WHERE tenant_id=NEW.tenant_id AND task_id=NEW.task_id;
        IF complete THEN RAISE EXCEPTION 'complete_input_executor_unavailable' USING ERRCODE='55000'; END IF;
    ELSE
        SELECT u.materialization_version=1 INTO complete FROM memoriesql.beads b JOIN memoriesql.source_units u USING(tenant_id,source_unit_id) WHERE b.tenant_id=NEW.tenant_id AND b.bead_id=NEW.bead_id;
        IF complete THEN RAISE EXCEPTION 'complete_input_exposure_validation_unavailable' USING ERRCODE='55000'; END IF;
    END IF;
    RETURN NEW;
END; $$;
CREATE TRIGGER complete_unit_execution_unavailable BEFORE INSERT OR UPDATE ON memoriesql.semantic_tasks FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_complete_unit_execution();
CREATE TRIGGER complete_unit_attempt_unavailable BEFORE INSERT ON memoriesql.semantic_task_attempts FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_complete_unit_execution();
CREATE TRIGGER complete_unit_semantics_unavailable BEFORE INSERT OR UPDATE ON memoriesql.bead_versions FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_complete_unit_execution();

CREATE FUNCTION memoriesql.assert_complete_unit_binding() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
DECLARE binding memoriesql.logical_unit_materializations%ROWTYPE; task memoriesql.semantic_tasks%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE; u memoriesql.source_units%ROWTYPE; pin jsonb;
BEGIN
    IF TG_TABLE_NAME='semantic_tasks' THEN
        IF NEW.task_kind<>'memory.semantic.author-complete-unit' THEN RETURN NULL; END IF;
        SELECT * INTO binding FROM memoriesql.logical_unit_materializations WHERE tenant_id=NEW.tenant_id AND task_id=NEW.task_id;
    ELSE binding:=NEW; END IF;
    IF binding.source_unit_id IS NULL THEN RAISE EXCEPTION 'complete_unit_binding_required' USING ERRCODE='23514'; END IF;
    SELECT * INTO task FROM memoriesql.semantic_tasks WHERE tenant_id=binding.tenant_id AND task_id=binding.task_id;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=binding.tenant_id AND package_id=binding.package_id;
    SELECT * INTO u FROM memoriesql.source_units WHERE tenant_id=binding.tenant_id AND source_unit_id=binding.source_unit_id;
    pin:=jsonb_build_object('package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,'inventory_sha256',p.inventory_hash,'required_parts',p.part_count,'required_characters',p.character_count,'required_utf8_bytes',p.utf8_byte_count,'coverage','entire_sealed_inventory');
    IF p.sealed_receipt_id IS NULL OR binding.package_pin IS DISTINCT FROM pin OR binding.occurrence_hash IS DISTINCT FROM p.occurrence_hash OR u.materialization_version IS DISTINCT FROM 1 OR u.event_id IS DISTINCT FROM binding.event_id OR u.content_hash IS DISTINCT FROM p.inventory_hash OR u.processing_receipt_id IS DISTINCT FROM binding.initial_receipt_id OR
       task.task_kind IS DISTINCT FROM 'memory.semantic.author-complete-unit' OR task.workspace_id IS DISTINCT FROM binding.workspace_id OR task.access_scope_id IS DISTINCT FROM binding.access_scope_id OR NOT memoriesql.complete_unit_input_valid(task.input_payload) OR
       task.input_payload->'payload' IS DISTINCT FROM jsonb_build_object('source_object_id',p.source_object_id,'event_id',binding.event_id,'source_unit_id',binding.source_unit_id,'bead_id',binding.bead_id,'materialization_receipt_id',binding.initial_receipt_id,'producer_policy_id',binding.producer_policy_id,'package',pin,'required_execution','trusted_complete_input_exposure_validation','execution_availability','unavailable') THEN
        RAISE EXCEPTION 'complete_unit_binding_conflict' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END; $$;
CREATE CONSTRAINT TRIGGER complete_unit_task_binding AFTER INSERT ON memoriesql.semantic_tasks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_complete_unit_binding();
CREATE CONSTRAINT TRIGGER complete_unit_binding_ready AFTER INSERT ON memoriesql.logical_unit_materializations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION memoriesql.assert_complete_unit_binding();
CREATE FUNCTION memoriesql.count_materialized_unit() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
BEGIN
    UPDATE memoriesql.source_event_materializations SET materialized_unit_count=materialized_unit_count+1 WHERE tenant_id=NEW.tenant_id AND event_id=NEW.event_id;
    RETURN NULL;
END; $$;
CREATE TRIGGER logical_event_progress AFTER INSERT ON memoriesql.logical_unit_materializations FOR EACH ROW EXECUTE FUNCTION memoriesql.count_materialized_unit();

CREATE FUNCTION memoriesql.logical_event_progress_v1(t uuid, e uuid) RETURNS jsonb LANGUAGE sql STABLE SET search_path=pg_catalog,memoriesql AS $$
    SELECT jsonb_build_object('event_id',event_id,'declared_units',declaration->'expected_units','materialized_units',materialized_unit_count,'remaining_declared_units',(declaration->>'expected_units')::integer-materialized_unit_count,'inventory_state',CASE WHEN declaration->>'expected_units' IS NULL THEN 'total_unknown' WHEN materialized_unit_count=(declaration->>'expected_units')::integer THEN 'declared_inventory_materialized' ELSE 'partial' END,'independently_proven_source_complete',false) FROM memoriesql.source_event_materializations WHERE tenant_id=t AND event_id=e;
$$;

CREATE FUNCTION memoriesql.materialize_logical_unit_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE
    started timestamptz:=clock_timestamp(); c memoriesql.authorization_contexts%ROWTYPE;
    p memoriesql.evidence_packages%ROWTYPE; s memoriesql.source_objects%ROWTYPE;
    policy memoriesql.evidence_producer_policies%ROWTYPE; old_receipt memoriesql.idempotency_receipts%ROWTYPE;
    frame memoriesql.source_event_materializations%ROWTYPE; binding memoriesql.logical_unit_materializations%ROWTYPE;
    parent memoriesql.source_units%ROWTYPE; e jsonb:=request->'event'; n jsonb; pin jsonb; input jsonb; result jsonb;
    req_hash text; event_hash text; declaration_hash text; unit_identity text;
    receipt_id uuid:=uuidv7(); eid uuid:=uuidv7(); uid uuid:=uuidv7(); bid uuid:=uuidv7(); tid uuid:=uuidv7(); enqueue_receipt uuid;
    parent_id uuid:=(request->>'parent_source_unit_id')::uuid; manifest_id text; existing boolean:=false;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN
        RAISE EXCEPTION 'materialization_requires_read_committed' USING ERRCODE='25000';
    END IF;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR pg_column_size(request)>16384 OR octet_length(request::text)>16384 OR
       request-ARRAY['contract_version','expected_schema_version','idempotency_key','package_id','expected_inventory_sha256','producer_policy_id','expected_source_object_schema_version','event','parent_source_unit_id']<>'{}'::jsonb OR
       (SELECT count(*) FROM jsonb_object_keys(request))<>9 OR request->'contract_version' IS DISTINCT FROM '1'::jsonb OR request->'expected_schema_version' IS DISTINCT FROM '17'::jsonb OR
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
    s:=memoriesql.evidence_package_authorize(p.source_object_id,true);
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
    req_hash:=encode(sha256(convert_to(request::text,'UTF8')),'hex');
    event_hash:=encode(sha256(convert_to(jsonb_build_array(s.source_object_id,p.declaration->>'source_revision_key',e->>'event_key')::text,'UTF8')),'hex');
    declaration_hash:=encode(sha256(convert_to(memoriesql.canonical_semantic_json_text(e),'UTF8')),'hex');
    n:=p.declaration->'native';
    unit_identity:=encode(sha256(convert_to(jsonb_build_array(event_hash,p.declaration->>'occurrence_identity_basis',n,parent_id)::text,'UTF8')),'hex');
    -- Same authority fence as storage and queue; deterministic keyed locks only.
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':materialize-operation:'||(request->>'idempotency_key'),0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-event:'||event_hash,0));
    PERFORM pg_advisory_xact_lock(hashtextextended(c.tenant_id::text||':logical-occurrence:'||p.occurrence_hash,0));
    PERFORM memoriesql.evidence_package_authorize(s.source_object_id,true);
    IF clock_timestamp()>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    SELECT * INTO old_receipt FROM memoriesql.idempotency_receipts WHERE tenant_id=c.tenant_id AND operation_kind='logical_unit.materialize.v1' AND idempotency_key=request->>'idempotency_key';
    IF FOUND THEN
        IF old_receipt.request_hash<>req_hash OR old_receipt.workspace_id<>c.workspace_id THEN RAISE EXCEPTION 'idempotency_conflict' USING ERRCODE='23505'; END IF;
        RETURN old_receipt.response_receipt||jsonb_build_object('replayed',true);
    END IF;
    SELECT * INTO frame FROM memoriesql.source_event_materializations WHERE tenant_id=c.tenant_id AND event_identity_hash=event_hash;
    IF FOUND THEN
        IF frame.declaration_hash<>declaration_hash THEN RAISE EXCEPTION 'logical_event_identity_conflict' USING ERRCODE='23505'; END IF;
        eid:=frame.event_id;
    END IF;
    SELECT * INTO binding FROM memoriesql.logical_unit_materializations WHERE tenant_id=c.tenant_id AND occurrence_hash=p.occurrence_hash;
    IF FOUND THEN
        IF binding.identity_hash<>unit_identity THEN RAISE EXCEPTION 'logical_occurrence_identity_conflict' USING ERRCODE='23505'; END IF;
        existing:=true; eid:=binding.event_id; uid:=binding.source_unit_id; bid:=binding.bead_id; tid:=binding.task_id; pin:=binding.package_pin;
    ELSE
        pin:=jsonb_build_object('package_id',p.package_id,'sealed_receipt_id',p.sealed_receipt_id,'inventory_sha256',p.inventory_hash,'required_parts',p.part_count,'required_characters',p.character_count,'required_utf8_bytes',p.utf8_byte_count,'coverage','entire_sealed_inventory');
        IF parent_id IS NOT NULL THEN
            SELECT * INTO parent FROM memoriesql.source_units WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND access_scope_id=s.access_scope_id AND source_unit_id=parent_id AND event_id=eid AND materialization_version=1;
            IF parent.source_unit_id IS NULL OR (n->>'parent_native_id' IS NOT NULL AND parent.external_unit_id IS NOT NULL AND n->>'parent_native_id'<>parent.external_unit_id) THEN RAISE EXCEPTION 'logical_parent_identity_conflict' USING ERRCODE='23505'; END IF;
        END IF;
    END IF;
    INSERT INTO memoriesql.idempotency_receipts(tenant_id,workspace_id,access_scope_id,idempotency_receipt_id,operation_kind,idempotency_key,request_hash,status,resource_kind,resource_id,attempt_count,created_at,updated_at)
    VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,receipt_id,'logical_unit.materialize.v1',request->>'idempotency_key',req_hash,'in_progress','source',s.source_object_id,1,clock_timestamp(),clock_timestamp());
    IF NOT existing THEN
        IF frame.event_id IS NULL THEN
            INSERT INTO memoriesql.source_events(tenant_id,workspace_id,access_scope_id,event_id,source_object_id,source_type,source_system,installation_id,external_event_id,external_id_scope,source_identity_key,session_id,actor_id,actor_kind,source_occurred_at,source_occurred_at_raw,source_sequence,source_revision_key,parser_contract_version,observation_unit_policy_version,observation_unit_count,captured_at,source_ref,content_hash,metadata,materialization_version)
            VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,eid,s.source_object_id,e->>'source_type',s.source_system,s.installation_id,e#>>'{native,native_id}','logical-events.v1:'||s.source_object_id::text,'logical-event.v1:'||event_hash,e#>>'{native,session_native_id}',e#>>'{native,participant_native_id}',e#>>'{native,role}',(e#>>'{native,occurred_at}')::timestamptz,e#>>'{native,occurred_at_raw}',(e#>>'{native,source_order}')::bigint,p.declaration->>'source_revision_key','logical-materialization.v1','logical-materialization.v1',(e->>'expected_units')::integer,clock_timestamp(),'logical-event.v1:'||event_hash,declaration_hash,jsonb_build_object('declaration',e,'content_hash_kind','event_declaration_sha256','independently_proven_source_complete',false),1);
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
        INSERT INTO memoriesql.logical_unit_materializations VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,p.occurrence_hash,unit_identity,eid,uid,bid,tid,p.package_id,policy.producer_policy_id,pin,receipt_id) RETURNING * INTO binding;
        INSERT INTO memoriesql.outbox_events(tenant_id,workspace_id,access_scope_id,outbox_event_id,idempotency_receipt_id,aggregate_kind,aggregate_id,event_kind,payload,headers,recorded_at,available_at)
        VALUES(c.tenant_id,c.workspace_id,s.access_scope_id,uuidv7(),receipt_id,'source_unit',uid,'source_unit.materialized',jsonb_build_object('event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'package_id',p.package_id),'{}',clock_timestamp(),clock_timestamp());
    ELSE
        SELECT idempotency_receipt_id INTO enqueue_receipt FROM memoriesql.semantic_tasks WHERE tenant_id=c.tenant_id AND task_id=tid;
    END IF;
    PERFORM memoriesql.evidence_package_authorize(s.source_object_id,true);
    IF clock_timestamp()>started+interval '2 seconds' OR policy.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'materialization_operation_expired' USING ERRCODE='57014'; END IF;
    result:=jsonb_build_object('contract_version',1,'idempotency_receipt_id',receipt_id,'initial_materialization_receipt_id',binding.initial_receipt_id,'task_enqueue_receipt_id',enqueue_receipt,'submitted_package_id',p.package_id,'bound_package',pin,'event_id',eid,'source_unit_id',uid,'bead_id',bid,'task_id',tid,'status',CASE WHEN existing THEN 'already_exists' ELSE 'materialized' END,'replayed',false,'execution_availability','unavailable','execution_reason','complete_input_executor_unavailable','event_progress_at_commit',memoriesql.logical_event_progress_v1(c.tenant_id,eid));
    UPDATE memoriesql.idempotency_receipts SET status='succeeded',response_receipt=result,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=c.tenant_id AND idempotency_receipt_id=receipt_id;
    RETURN result;
END; $$;
CREATE FUNCTION memoriesql.inspect_logical_event_v1(request jsonb) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE c memoriesql.authorization_contexts%ROWTYPE; source_id uuid; result jsonb;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN
        RAISE EXCEPTION 'materialization_requires_read_committed' USING ERRCODE='25000';
    END IF;
    IF jsonb_typeof(request) IS DISTINCT FROM 'object' OR octet_length(request::text)>1024 OR request-ARRAY['contract_version','event_id']<>'{}'::jsonb OR request->'contract_version' IS DISTINCT FROM '1'::jsonb THEN RAISE EXCEPTION 'invalid_materialization_contract' USING ERRCODE='22023'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT source_object_id INTO source_id FROM memoriesql.source_events WHERE tenant_id=c.tenant_id AND workspace_id=c.workspace_id AND event_id=(request->>'event_id')::uuid AND materialization_version=1;
    PERFORM memoriesql.evidence_package_authorize(source_id,false);
    result:=memoriesql.logical_event_progress_v1(c.tenant_id,(request->>'event_id')::uuid);
    RETURN result;
END; $$;

INSERT INTO memoriesql.semantic_task_admission_policies (semantic_registry_hash,task_kind,contract_revision,owning_module,task_contract_hash,target_kind,required_capability,queue_name,base_priority,max_attempts,concurrency_key,concurrency_limit) VALUES (
'complete-unit-bindings-v1:8013803a5ad67b0fe530b05c1c5badd533ae118d5105de756588a282bf6d3d3c','memory.semantic.author-complete-unit',1,'memoriesql.kernel','8013803a5ad67b0fe530b05c1c5badd533ae118d5105de756588a282bf6d3d3c','complete_unit_semantics','memory.capture','capture',50,3,NULL,NULL);

CREATE OR REPLACE FUNCTION memoriesql.semantic_task_input_reference_safe(
    candidate jsonb,
    maximum_bytes integer
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    manifest jsonb;
    evidence_references jsonb;
    reference_item jsonb;
    budget jsonb;
    budget_item record;
    payload jsonb;
    effective_maximum_bytes integer;
BEGIN
    IF candidate->>'task_kind'='memory.semantic.author-complete-unit' THEN RETURN maximum_bytes>0 AND octet_length(candidate::text)<=maximum_bytes AND memoriesql.complete_unit_input_valid(candidate); END IF;
    effective_maximum_bytes := maximum_bytes;
    IF candidate IS NULL
       OR maximum_bytes <= 0
       OR octet_length(candidate::text) > effective_maximum_bytes
       OR jsonb_typeof(candidate) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(candidate)) <> 9
       OR EXISTS (
           SELECT 1
           FROM jsonb_object_keys(candidate) AS input_key(key)
           WHERE input_key.key <> ALL (ARRAY[
               'task_id', 'task_kind', 'contract_revision',
               'target_reference', 'expected_target_revision',
               'evidence_manifest', 'requested_effort_key',
               'requested_budget', 'payload'
           ]::text[])
       ) THEN
        RETURN false;
    END IF;

    IF NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'task_id', 36
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'task_kind', 128
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           candidate ->> 'target_reference', 512
       )
       OR jsonb_typeof(candidate -> 'contract_revision') <> 'number'
       OR candidate ->> 'contract_revision' !~ '^[0-9]+$'
       OR (candidate ->> 'contract_revision')::numeric < 1
       OR jsonb_typeof(candidate -> 'expected_target_revision') <> 'number'
       OR candidate ->> 'expected_target_revision' !~ '^[0-9]+$'
       OR jsonb_typeof(candidate -> 'requested_effort_key')
            NOT IN ('null', 'string')
       OR (
           jsonb_typeof(candidate -> 'requested_effort_key') = 'string'
           AND NOT memoriesql.semantic_queue_reference_text_safe(
               candidate ->> 'requested_effort_key', 128
           )
       ) THEN
        RETURN false;
    END IF;

    manifest := candidate -> 'evidence_manifest';
    IF jsonb_typeof(manifest) IS DISTINCT FROM 'object' THEN
        RETURN false;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(manifest)) <> 3
       OR EXISTS (
           SELECT 1
           FROM jsonb_object_keys(manifest) AS manifest_key(key)
           WHERE manifest_key.key <> ALL (
               ARRAY['manifest_id', 'revision', 'references']::text[]
           )
       )
       OR NOT memoriesql.semantic_queue_reference_text_safe(
           manifest ->> 'manifest_id', 512
       )
       OR jsonb_typeof(manifest -> 'revision') <> 'number'
       OR manifest ->> 'revision' !~ '^[0-9]+$'
       OR (manifest ->> 'revision')::numeric < 1
       OR jsonb_typeof(manifest -> 'references') <> 'array'
       OR jsonb_array_length(manifest -> 'references') > 256 THEN
        RETURN false;
    END IF;
    evidence_references := manifest -> 'references';
    FOR reference_item IN
        SELECT value FROM jsonb_array_elements(evidence_references)
    LOOP
        IF jsonb_typeof(reference_item) <> 'object' THEN
            RETURN false;
        END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(reference_item)) <> 3
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(reference_item) AS reference_key(key)
               WHERE reference_key.key <> ALL (
                   ARRAY[
                       'reference_id', 'content_hash', 'declared_characters'
                   ]::text[]
               )
           )
           OR NOT memoriesql.semantic_queue_reference_text_safe(
               reference_item ->> 'reference_id', 512
           )
           OR reference_item ->> 'content_hash' !~ '^[a-f0-9]{64}$'
           OR jsonb_typeof(reference_item -> 'declared_characters') <> 'number'
           OR reference_item ->> 'declared_characters' !~ '^[0-9]+$' THEN
            RETURN false;
        END IF;
    END LOOP;
    IF (
        SELECT count(*) <> count(DISTINCT item ->> 'reference_id')
        FROM jsonb_array_elements(evidence_references) AS item
    ) THEN
        RETURN false;
    END IF;

    budget := candidate -> 'requested_budget';
    IF jsonb_typeof(budget) NOT IN ('null', 'object') THEN
        RETURN false;
    END IF;
    IF jsonb_typeof(budget) = 'object' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(budget)) <> 13
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(budget) AS budget_key(key)
               WHERE budget_key.key <> ALL (ARRAY[
                   'wall_clock_seconds', 'request_limit',
                   'input_token_limit', 'output_token_limit',
                   'total_token_limit', 'tool_call_limit',
                   'cost_safety_limit_microusd', 'max_delegate_calls',
                   'max_parallel_delegates', 'evidence_item_limit',
                   'hydrated_character_limit', 'output_retries', 'tool_retries'
               ]::text[])
           ) THEN
            RETURN false;
        END IF;
        FOR budget_item IN SELECT key, value FROM jsonb_each(budget)
        LOOP
            IF budget_item.key = 'cost_safety_limit_microusd'
               AND jsonb_typeof(budget_item.value) = 'null' THEN
                CONTINUE;
            END IF;
            IF jsonb_typeof(budget_item.value) <> 'number'
               OR budget_item.value #>> '{}' !~ '^[0-9]+$' THEN
                RETURN false;
            END IF;
        END LOOP;
        IF (budget ->> 'wall_clock_seconds')::numeric < 1
           OR (budget ->> 'request_limit')::numeric < 1
           OR (budget ->> 'input_token_limit')::numeric < 1
           OR (budget ->> 'output_token_limit')::numeric < 1
           OR (budget ->> 'total_token_limit')::numeric < 1
           OR (budget ->> 'evidence_item_limit')::numeric < 1
           OR (budget ->> 'hydrated_character_limit')::numeric < 1
           OR (budget ->> 'total_token_limit')::numeric
                < (budget ->> 'input_token_limit')::numeric
           OR (budget ->> 'total_token_limit')::numeric
                < (budget ->> 'output_token_limit')::numeric
           OR (budget ->> 'max_parallel_delegates')::numeric
                > (budget ->> 'max_delegate_calls')::numeric THEN
            RETURN false;
        END IF;
    END IF;

    IF candidate ->> 'contract_revision' = '2'
       AND candidate ->> 'task_kind' IN ('memory.semantic.author-observations', 'memory.semantic.correct-observation')
       AND (jsonb_array_length(evidence_references) NOT BETWEEN 1 AND 8
            OR (SELECT COALESCE(sum((value ->> 'declared_characters')::numeric), 0)
                FROM jsonb_array_elements(evidence_references)) > 4096) THEN
        RETURN false;
    END IF;
    payload := candidate -> 'payload';
    IF candidate ->> 'task_kind' = 'memory.semantic.author-observations'
       AND candidate ->> 'contract_revision' IN ('1', '2') THEN
        IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(payload)) <> 3
           OR EXISTS (
               SELECT 1
               FROM jsonb_object_keys(payload) AS payload_key(key)
               WHERE payload_key.key <> ALL (
                   ARRAY['event_id', 'bead_ids', 'source_unit_ids']::text[]
               )
           )
           OR payload ->> 'event_id' !~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           OR jsonb_typeof(payload -> 'bead_ids') <> 'array'
           OR jsonb_typeof(payload -> 'source_unit_ids') <> 'array'
           OR jsonb_array_length(payload -> 'bead_ids') NOT BETWEEN 1 AND 8
           OR jsonb_array_length(payload -> 'bead_ids') <>
                jsonb_array_length(payload -> 'source_unit_ids')
           OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements(payload -> 'bead_ids') AS item(value)
               WHERE jsonb_typeof(item.value) <> 'string'
                  OR item.value #>> '{}' !~
                     '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           )
           OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements(payload -> 'source_unit_ids')
                    AS item(value)
               WHERE jsonb_typeof(item.value) <> 'string'
                  OR item.value #>> '{}' !~
                     '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
           )
           OR (
               SELECT count(*) <> count(DISTINCT item.value #>> '{}')
               FROM jsonb_array_elements(payload -> 'bead_ids') AS item(value)
           )
           OR (
               SELECT count(*) <> count(DISTINCT item.value #>> '{}')
               FROM jsonb_array_elements(payload -> 'source_unit_ids')
                    AS item(value)
           ) THEN
            RETURN false;
        END IF;
        RETURN true;
    END IF;
    IF candidate ->> 'task_kind' = 'memory.semantic.correct-observation'
       AND candidate ->> 'contract_revision' = '2' THEN
        IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
           OR (SELECT count(*) FROM jsonb_object_keys(payload)) <> 4
           OR NOT memoriesql.semantic_task_input_reference_safe(
                jsonb_set(jsonb_set(candidate, '{task_kind}',
                    '"memory.semantic.author-observations"'::jsonb),
                    '{payload}', payload - 'supersedes'), maximum_bytes)
           OR jsonb_array_length(payload -> 'bead_ids') <> 1
           OR jsonb_typeof(payload -> 'supersedes') IS DISTINCT FROM 'array'
           OR jsonb_array_length(payload -> 'supersedes') NOT BETWEEN 1 AND 8 THEN
            RETURN false;
        END IF;
        FOR reference_item IN SELECT value FROM jsonb_array_elements(payload -> 'supersedes')
        LOOP
            IF jsonb_typeof(reference_item) IS DISTINCT FROM 'object'
               OR (SELECT count(*) FROM jsonb_object_keys(reference_item)) <> 2
               OR COALESCE(reference_item ->> 'bead_id', '') !~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               OR COALESCE(reference_item ->> 'bead_version_id', '') !~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               OR reference_item ->> 'bead_id' = payload #>> '{bead_ids,0}' THEN
                RETURN false;
            END IF;
        END LOOP;
        RETURN (SELECT count(*) = count(DISTINCT value ->> 'bead_id')
                FROM jsonb_array_elements(payload -> 'supersedes'));
    END IF;
    RETURN memoriesql.semantic_queue_payload_reference_scalar_safe(payload);
END;
$$;

CREATE OR REPLACE FUNCTION memoriesql.assert_source_event_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    actual_observation_count integer;
BEGIN
    IF NEW.materialization_version=1 THEN
        IF NOT EXISTS (SELECT 1 FROM memoriesql.source_event_materializations WHERE tenant_id=NEW.tenant_id AND event_id=NEW.event_id AND materialized_unit_count>0) THEN RAISE EXCEPTION 'logical_event_binding_required' USING ERRCODE='23514'; END IF;
        RETURN NULL;
    END IF;
    SELECT count(*)::integer
      INTO actual_observation_count
      FROM memoriesql.source_units AS unit_record
     WHERE unit_record.tenant_id = NEW.tenant_id
       AND unit_record.event_id = NEW.event_id
       AND unit_record.is_observation;

    IF actual_observation_count <> NEW.observation_unit_count THEN
        RAISE EXCEPTION 'source event % declared % observation units but has %',
            NEW.event_id,
            NEW.observation_unit_count,
            actual_observation_count
            USING ERRCODE = '23514';
    END IF;

    IF NEW.source_type = 'document' AND NOT EXISTS (
        SELECT 1
        FROM memoriesql.document_revisions AS revision
        WHERE revision.tenant_id = NEW.tenant_id
          AND revision.event_id = NEW.event_id
    ) THEN
        RAISE EXCEPTION 'document event % requires a document revision', NEW.event_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION memoriesql.assert_observation_unit_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    event_source_type text;
    expected_observation_count integer;
    actual_observation_count integer;
BEGIN
    IF NEW.materialization_version=1 THEN
        IF NOT EXISTS (SELECT 1 FROM memoriesql.logical_unit_materializations WHERE tenant_id=NEW.tenant_id AND source_unit_id=NEW.source_unit_id) THEN RAISE EXCEPTION 'logical_unit_binding_required' USING ERRCODE='23514'; END IF;
        RETURN NULL;
    END IF;
    IF NOT NEW.is_observation THEN
        RETURN NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM memoriesql.beads AS bead
        WHERE bead.tenant_id = NEW.tenant_id
          AND bead.source_unit_id = NEW.source_unit_id
          AND bead.origin_kind = 'initial'
    ) THEN
        RAISE EXCEPTION 'observation unit % requires exactly one bead', NEW.source_unit_id
            USING ERRCODE = '23514';
    END IF;

    SELECT event_record.source_type, event_record.observation_unit_count
      INTO STRICT event_source_type, expected_observation_count
      FROM memoriesql.source_events AS event_record
     WHERE event_record.tenant_id = NEW.tenant_id
       AND event_record.event_id = NEW.event_id;

    SELECT count(*)::integer
      INTO actual_observation_count
      FROM memoriesql.source_units AS unit_record
     WHERE unit_record.tenant_id = NEW.tenant_id
       AND unit_record.event_id = NEW.event_id
       AND unit_record.is_observation;

    IF actual_observation_count <> expected_observation_count THEN
        RAISE EXCEPTION 'event % observation unit count is sealed at %',
            NEW.event_id,
            expected_observation_count
            USING ERRCODE = '23514';
    END IF;

    IF event_source_type = 'transcript' THEN
        IF NEW.unit_kind <> 'turn' OR NOT EXISTS (
            SELECT 1 FROM memoriesql.conversation_turns AS detail
            WHERE detail.tenant_id = NEW.tenant_id
              AND detail.source_unit_id = NEW.source_unit_id
        ) THEN
            RAISE EXCEPTION 'transcript observation unit % requires turn detail', NEW.source_unit_id
                USING ERRCODE = '23514';
        END IF;
    ELSIF event_source_type = 'document' THEN
        IF NEW.unit_kind NOT IN ('section', 'chunk') OR NOT EXISTS (
            SELECT 1 FROM memoriesql.document_segments AS detail
            WHERE detail.tenant_id = NEW.tenant_id
              AND detail.source_unit_id = NEW.source_unit_id
        ) THEN
            RAISE EXCEPTION 'document observation unit % requires segment detail', NEW.source_unit_id
                USING ERRCODE = '23514';
        END IF;
    ELSIF event_source_type = 'media' THEN
        IF NEW.unit_kind <> 'media_window' OR NOT EXISTS (
            SELECT 1 FROM memoriesql.media_segments AS detail
            WHERE detail.tenant_id = NEW.tenant_id
              AND detail.source_unit_id = NEW.source_unit_id
        ) THEN
            RAISE EXCEPTION 'media observation unit % requires media-window detail', NEW.source_unit_id
                USING ERRCODE = '23514';
        END IF;
    ELSIF event_source_type IN ('relational', 'operational') THEN
        IF NEW.unit_kind <> 'record_change' OR NOT EXISTS (
            SELECT 1 FROM memoriesql.record_events AS detail
            WHERE detail.tenant_id = NEW.tenant_id
              AND detail.source_unit_id = NEW.source_unit_id
        ) THEN
            RAISE EXCEPTION 'record observation unit % requires record-event detail', NEW.source_unit_id
                USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'source type % has no PR-01 typed detail contract', event_source_type
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION memoriesql.guard_materialization_identity() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.complete_unit_input_valid(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.guard_complete_unit_execution() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.assert_complete_unit_binding() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.count_materialized_unit() FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.logical_event_progress_v1(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.materialize_logical_unit_v1(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION memoriesql.inspect_logical_event_v1(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION memoriesql.materialize_logical_unit_v1(jsonb), memoriesql.inspect_logical_event_v1(jsonb) TO memoriesql_application;
