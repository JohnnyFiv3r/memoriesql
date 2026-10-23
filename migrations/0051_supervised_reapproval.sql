-- Owner re-approval of a supervised task after its approval failed.
-- Schema 26 allowed one immutable approval per task. Every committed intent
-- consumes its one managed turn, including a crash before dispatch, a failure
-- or an ambiguous completion, and only revocation was permitted, so a task
-- whose turn failed could never dispatch again. An administrator may now
-- provision a replacement approval for the same task. It names the approval it
-- supersedes, which must already be revoked; no attempt of the task may be
-- claimed or running, and the task must be waiting (queued, retry_wait or
-- policy_paused), never settled. The approvals of a task form one linear chain
-- with at most one active link. Each intent stays charged to the approval it
-- consumed: nothing is refunded and no earlier row changes. Dispatch, open and
-- acceptance read only the head of the chain, so a turn under a superseded
-- approval can never authorize meaning. Forward-only; this file's number is
-- provisional and is assigned at landing.

ALTER TABLE memoriesql.model_supervised_qualifications
 DROP CONSTRAINT model_supervised_qualifications_tenant_id_task_id_key,
 ADD COLUMN supersedes_qualification_id uuid,
 ADD FOREIGN KEY(tenant_id,supersedes_qualification_id)
  REFERENCES memoriesql.model_supervised_qualifications(tenant_id,qualification_id),
 ADD CONSTRAINT model_supervised_qualifications_linear_chain UNIQUE(tenant_id,supersedes_qualification_id),
 ADD CONSTRAINT model_supervised_qualifications_not_self CHECK(supersedes_qualification_id IS DISTINCT FROM qualification_id);
CREATE UNIQUE INDEX model_supervised_qualifications_one_active
 ON memoriesql.model_supervised_qualifications(tenant_id,task_id) WHERE status='active';

-- The latest approval of a task: the one no other approval supersedes.
CREATE FUNCTION memoriesql.supervised_qualification_head(t uuid,task uuid)
RETURNS SETOF memoriesql.model_supervised_qualifications
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,memoriesql ROWS 1 AS $$
 SELECT q.* FROM memoriesql.model_supervised_qualifications q
 WHERE q.tenant_id=t AND q.task_id=task
   AND NOT EXISTS(SELECT 1 FROM memoriesql.model_supervised_qualifications later
                  WHERE later.tenant_id=q.tenant_id AND later.supersedes_qualification_id=q.qualification_id)
$$;
REVOKE ALL ON FUNCTION memoriesql.supervised_qualification_head(uuid,uuid) FROM PUBLIC;

-- Runs after the outcome-authority fence (trigger names order), so no dispatch
-- of the tenant can consume an approval while a replacement is checked.
CREATE FUNCTION memoriesql.guard_supervised_reapproval() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
DECLARE prior memoriesql.model_supervised_qualifications%ROWTYPE;
BEGIN
 SELECT * INTO prior FROM memoriesql.supervised_qualification_head(NEW.tenant_id,NEW.task_id);
 IF NOT FOUND THEN
  IF NEW.supersedes_qualification_id IS NOT NULL THEN
   RAISE EXCEPTION 'supervised_reapproval_unavailable' USING ERRCODE='55000'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.supersedes_qualification_id IS DISTINCT FROM prior.qualification_id
  OR prior.status<>'revoked' OR NEW.status<>'active'
  OR NEW.workspace_id IS DISTINCT FROM prior.workspace_id
  OR NEW.access_scope_id IS DISTINCT FROM prior.access_scope_id
  OR NEW.origin_principal_id IS DISTINCT FROM prior.origin_principal_id
  OR NOT EXISTS(SELECT 1 FROM memoriesql.semantic_tasks t WHERE t.tenant_id=NEW.tenant_id AND t.task_id=NEW.task_id
                AND t.status IN ('queued','retry_wait','policy_paused') AND t.result_attempt_id IS NULL)
  OR EXISTS(SELECT 1 FROM memoriesql.semantic_task_attempts a WHERE a.tenant_id=NEW.tenant_id AND a.task_id=NEW.task_id
            AND a.status IN ('claimed','running')) THEN
  RAISE EXCEPTION 'supervised_reapproval_unavailable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.guard_supervised_reapproval() FROM PUBLIC;
CREATE TRIGGER supervised_reapproval_guard BEFORE INSERT ON memoriesql.model_supervised_qualifications
 FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_supervised_reapproval();

-- From 0026_supervised_managed_dispatch.sql, reading the head of the chain.
CREATE OR REPLACE FUNCTION memoriesql.supervised_task_open(t uuid,task uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE q memoriesql.model_supervised_qualifications%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(t::text||':semantic_outcome_authority:',0));
 SELECT * INTO q FROM memoriesql.supervised_qualification_head(t,task);
 RETURN NOT FOUND OR (q.status='active' AND q.created_at<=clock_timestamp() AND (q.configuration->>'deadline')::timestamptz>clock_timestamp());
END; $$;

-- From 0026_supervised_managed_dispatch.sql, reading the head of the chain.
CREATE OR REPLACE FUNCTION memoriesql.supervised_acceptance_valid(t uuid,task uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
DECLARE q memoriesql.model_supervised_qualifications%ROWTYPE; inputs numeric; outputs numeric; cash numeric;
BEGIN
 SELECT * INTO q FROM memoriesql.supervised_qualification_head(t,task);
 IF NOT FOUND THEN RETURN true; END IF;
 IF NOT memoriesql.supervised_task_open(t,task) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM memoriesql.model_dispatch_usage_fold_v2 f WHERE f.tenant_id=t AND f.supervised_qualification_id=q.qualification_id)
 OR EXISTS(SELECT 1 FROM memoriesql.model_dispatch_usage_fold_v2 f WHERE f.tenant_id=t AND f.supervised_qualification_id=q.qualification_id
  AND (f.outcome<>'succeeded' OR f.usage_state<>'reported' OR f.usage_provenance<>'provider_reported'
   OR (f.dispatch_boundary='managed_turn' AND f.managed_observation->>'turn_completion' IS DISTINCT FROM 'completed')
   OR (q.configuration->>'reported_cash_stop_microunits' IS NOT NULL AND f.incremental_cash_exposure_microunits IS NULL))) THEN RETURN false; END IF;
 SELECT sum(input_tokens),sum(output_tokens),sum(incremental_cash_exposure_microunits) INTO inputs,outputs,cash
 FROM memoriesql.model_dispatch_usage_fold_v2 WHERE tenant_id=t AND supervised_qualification_id=q.qualification_id;
 RETURN inputs<(q.configuration->>'reported_input_token_stop')::bigint
 AND outputs<(q.configuration->>'reported_generated_token_stop')::bigint
 AND (q.configuration->>'reported_cash_stop_microunits' IS NULL OR cash<=(q.configuration->>'reported_cash_stop_microunits')::bigint);
END; $$;

-- From 0041_authored_statement_kinds.sql: the head of the chain, and earlier approvals' intents.
CREATE OR REPLACE FUNCTION memoriesql.consume_supervised_dispatch(i jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE q memoriesql.model_supervised_qualifications%ROWTYPE; route jsonb; ordinal bigint; prior_count bigint; c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended((i->>'tenant_id')||':semantic_outcome_authority:',0));
 SELECT * INTO q FROM memoriesql.supervised_qualification_head((i->>'tenant_id')::uuid,(i->>'semantic_task_id')::uuid);
 IF NOT FOUND THEN
  IF i ? 'supervision' OR COALESCE(i->>'dispatch_boundary','single_inference')<>'single_inference' THEN RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
  RETURN;
 END IF;
 IF q.configuration IS DISTINCT FROM i->'supervision' OR q.origin_principal_id IS DISTINCT FROM (i->>'origin_principal_id')::uuid
 OR q.workspace_id IS DISTINCT FROM (i->>'workspace_id')::uuid OR q.access_scope_id IS DISTINCT FROM (i->>'access_scope_id')::uuid
 OR NOT memoriesql.supervised_task_open(q.tenant_id,q.task_id)
 OR NOT COALESCE((i->>'task_kind'='memory.semantic.author-complete-unit' AND (i->>'task_contract_revision')::integer IN (2,3,4,5,6,7))
   OR (i->>'task_kind'='memory.semantic.assess-relations' AND (i->>'task_contract_revision')::integer=1),false)
 THEN RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'supervised_requires_read_committed'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(q.tenant_id::text||':supervised-dispatch:'||q.qualification_id::text,0));
 SELECT r.value,r.ordinality INTO route,ordinal FROM jsonb_array_elements(q.configuration->'routes') WITH ORDINALITY r
 WHERE r.value#>>'{reference,profile_key}'=i->>'model_profile_key'
 AND r.value#>'{reference,revision}'=i->'model_profile_revision';
 IF route IS NULL OR route->'target' IS DISTINCT FROM i->'target'
 OR route->>'qualification_revision' IS DISTINCT FROM i->>'qualification_revision'
 OR route->'observable_dispatch_allowance' IS DISTINCT FROM '1'::jsonb
 -- A relation task may qualify a second managed turn for its specialist.
 OR COALESCE(i->>'dispatch_boundary','single_inference') IS DISTINCT FROM (CASE WHEN ordinal=1 THEN 'managed_turn'
     WHEN i->>'task_kind'='memory.semantic.assess-relations' AND route#>>'{target,boundary_kind}'='managed_turn' THEN 'managed_turn'
     ELSE 'single_inference' END)
 OR (ordinal=1 AND route#>>'{target,boundary_kind}' IS DISTINCT FROM 'managed_turn')
 OR (ordinal=2 AND COALESCE(i->>'dispatch_boundary','single_inference')='single_inference'
     AND route#>'{target,transport_retries_disabled}' IS DISTINCT FROM 'true'::jsonb)
 THEN RAISE EXCEPTION 'supervised_route_mismatch' USING ERRCODE='42501'; END IF;
 -- Count all attempts, including uncertain/unstarted/failed intents. Never refund.
 SELECT count(*) INTO prior_count FROM memoriesql.model_provider_request_intents
 WHERE tenant_id=q.tenant_id AND supervised_qualification_id=q.qualification_id;
 IF prior_count<>ordinal-1 OR EXISTS(SELECT 1 FROM memoriesql.model_provider_request_intents WHERE request_id=(i->>'request_id')::uuid)
 THEN RAISE EXCEPTION 'supervised_dispatch_allowance_consumed' USING ERRCODE='55000'; END IF;
 IF prior_count>0 AND NOT memoriesql.supervised_acceptance_valid(q.tenant_id,q.task_id)
 THEN RAISE EXCEPTION 'supervised_prior_dispatch_unsettled_or_stop_reached' USING ERRCODE='55000'; END IF;
 -- Intents under an approval this one superseded stay charged there; any other is outside approval.
 IF EXISTS(SELECT 1 FROM memoriesql.model_provider_request_intents r WHERE r.tenant_id=q.tenant_id AND r.task_id=q.task_id AND r.supervised_qualification_id IS DISTINCT FROM q.qualification_id
  AND NOT EXISTS(SELECT 1 FROM memoriesql.model_supervised_qualifications earlier WHERE earlier.tenant_id=r.tenant_id AND earlier.task_id=r.task_id
   AND earlier.qualification_id=r.supervised_qualification_id AND earlier.status='revoked'))
 THEN RAISE EXCEPTION 'supervised_prior_dispatch_outside_approval' USING ERRCODE='55000'; END IF;
 -- Hard-bounded follow-up reservations must also fit the remaining reported stop allowance.
 IF ordinal=2 AND EXISTS(SELECT 1 FROM memoriesql.model_dispatch_usage_fold_v2 f WHERE f.tenant_id=q.tenant_id AND f.supervised_qualification_id=q.qualification_id
  AND (f.input_tokens+(i->>'max_input_tokens')::bigint>(q.configuration->>'reported_input_token_stop')::bigint
   OR f.output_tokens+(i->>'max_output_tokens')::bigint>(q.configuration->>'reported_generated_token_stop')::bigint
   OR (q.configuration->>'reported_cash_stop_microunits' IS NOT NULL AND f.incremental_cash_exposure_microunits+(i->>'conservative_reservation_microunits')::bigint>(q.configuration->>'reported_cash_stop_microunits')::bigint)))
 THEN RAISE EXCEPTION 'supervised_followup_exceeds_remaining_allowance' USING ERRCODE='55000'; END IF;
END; $$;
