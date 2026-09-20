-- Explicit supervised qualification extends the existing accounting ledger.
-- No installed profile, credentials, providers or production trust are provisioned.
CREATE TABLE memoriesql.model_supervised_qualifications (
 tenant_id uuid NOT NULL, workspace_id uuid NOT NULL, access_scope_id uuid NOT NULL,
 qualification_id uuid NOT NULL, task_id uuid NOT NULL, origin_principal_id uuid NOT NULL,
 configuration jsonb NOT NULL, approved_by_principal_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), status text NOT NULL DEFAULT 'active',
 PRIMARY KEY(tenant_id,qualification_id), UNIQUE(tenant_id,task_id),
 FOREIGN KEY(tenant_id,task_id) REFERENCES memoriesql.semantic_tasks(tenant_id,task_id),
 FOREIGN KEY(tenant_id,origin_principal_id) REFERENCES memoriesql.principals(tenant_id,principal_id),
 FOREIGN KEY(tenant_id,approved_by_principal_id) REFERENCES memoriesql.principals(tenant_id,principal_id),
 CHECK(status IN ('active','revoked')),
 CHECK(COALESCE(jsonb_typeof(configuration)='object' AND octet_length(configuration::text)<=8192
   AND configuration->>'qualification_id'=qualification_id::text
   AND configuration->>'semantic_task_id'=task_id::text
   AND configuration->'contract_version'='1'::jsonb
   AND configuration->'serial'='true'::jsonb
   AND configuration->>'usage_limits'='reported_stop_triggers_not_remote_ceilings'
   AND jsonb_array_length(configuration->'routes') BETWEEN 1 AND 2
   AND (configuration->>'reported_input_token_stop')::bigint>0
   AND (configuration->>'reported_generated_token_stop')::bigint>0,false))
);
ALTER TABLE memoriesql.model_supervised_qualifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.model_supervised_qualifications FORCE ROW LEVEL SECURITY;
REVOKE ALL ON memoriesql.model_supervised_qualifications FROM PUBLIC,memoriesql_application,memoriesql_worker;
CREATE FUNCTION memoriesql.guard_supervised_qualification() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,memoriesql AS $$
BEGIN
 IF TG_OP<>'UPDATE' OR to_jsonb(NEW)-'status' IS DISTINCT FROM to_jsonb(OLD)-'status'
    OR OLD.status<>'active' OR NEW.status<>'revoked' THEN
  RAISE EXCEPTION 'supervised_qualification_immutable' USING ERRCODE='55000'; END IF;
 RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.guard_supervised_qualification() FROM PUBLIC;
CREATE TRIGGER supervised_qualification_immutable BEFORE UPDATE OR DELETE ON memoriesql.model_supervised_qualifications
 FOR EACH ROW EXECUTE FUNCTION memoriesql.guard_supervised_qualification();
CREATE TRIGGER supervised_qualification_authority_fence BEFORE INSERT OR UPDATE OR DELETE ON memoriesql.model_supervised_qualifications
 FOR EACH ROW EXECUTE FUNCTION memoriesql.fence_semantic_outcome_authority_mutation();

ALTER TABLE memoriesql.model_provider_request_intents
 ADD COLUMN dispatch_boundary text NOT NULL DEFAULT 'single_inference',
 ADD COLUMN supervised_qualification_id uuid,
 ADD COLUMN qualification_revision text,
 ADD COLUMN supply_attestation text NOT NULL DEFAULT 'host_visible_only',
 ALTER COLUMN max_input_tokens DROP NOT NULL,
 ALTER COLUMN max_output_tokens DROP NOT NULL,
 ADD FOREIGN KEY(tenant_id,supervised_qualification_id) REFERENCES memoriesql.model_supervised_qualifications(tenant_id,qualification_id);
ALTER TABLE memoriesql.model_provider_request_intents ADD CONSTRAINT supervised_dispatch_shape CHECK (
 supply_attestation='host_visible_only' AND
 ((dispatch_boundary='single_inference' AND max_input_tokens IS NOT NULL AND max_output_tokens IS NOT NULL)
 OR (dispatch_boundary='managed_turn' AND supervised_qualification_id IS NOT NULL
   AND max_input_tokens IS NULL AND max_output_tokens IS NULL AND safety_ceiling_microunits IS NULL
   AND conservative_reservation_microunits=0))
 AND ((supervised_qualification_id IS NULL AND qualification_revision IS NULL)
 OR (supervised_qualification_id IS NOT NULL AND qualification_revision ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$')));
ALTER TABLE memoriesql.model_provider_request_intents DROP CONSTRAINT model_provider_request_intents_billing_shape;
ALTER TABLE memoriesql.model_provider_request_intents ADD CONSTRAINT model_provider_request_intents_billing_shape CHECK (
 dispatch_boundary='managed_turn' OR
 (billing_basis='metered' AND pricing_revision_id IS NOT NULL AND conservative_reservation_microunits>0)
 OR (billing_basis='metered' AND pricing_revision_id IS NULL AND safety_ceiling_microunits IS NULL AND conservative_reservation_microunits=0)
 OR billing_basis<>'metered');
CREATE INDEX supervised_dispatch_allowance ON memoriesql.model_provider_request_intents(tenant_id,supervised_qualification_id,model_profile_key,model_profile_revision)
 WHERE supervised_qualification_id IS NOT NULL;
ALTER TABLE memoriesql.model_usage_events ADD COLUMN managed_observation jsonb;
ALTER TABLE memoriesql.model_usage_events ADD CONSTRAINT managed_observation_bound CHECK(managed_observation IS NULL OR octet_length(managed_observation::text)<=32768);

CREATE FUNCTION memoriesql.model_usage_units_valid(u jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_typeof(u)='object' AND
 u-ARRAY['input_tokens','output_tokens','total_tokens','cached_input_tokens','cache_write_tokens','reasoning_tokens','tool_calls']='{}'::jsonb
 AND (SELECT count(*)=7 AND bool_and(value ~ '^[0-9]+$') FROM jsonb_each_text(u))
 AND (u->>'total_tokens')::numeric=(u->>'input_tokens')::numeric+(u->>'output_tokens')::numeric
 AND (u->>'cached_input_tokens')::numeric+(u->>'cache_write_tokens')::numeric<=(u->>'input_tokens')::numeric
 AND (u->>'reasoning_tokens')::numeric<=(u->>'output_tokens')::numeric,false);
$$;
REVOKE ALL ON FUNCTION memoriesql.model_usage_units_valid(jsonb) FROM PUBLIC;

CREATE FUNCTION memoriesql.managed_observation_valid(o jsonb,u jsonb,provenance text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,memoriesql AS $$
DECLARE r jsonb; expected jsonb:='null'; basis text:='unavailable'; parts jsonb:=o->'request_observations'; n integer;
BEGIN
 IF jsonb_typeof(o) IS DISTINCT FROM 'object' OR o-ARRAY['turn_completion','underlying_inference_count','inference_count_basis','reported_total','estimated_total','request_observations','request_observations_complete']<>'{}'::jsonb
  OR (SELECT count(*) FROM jsonb_object_keys(o))<>7
  OR COALESCE(o->>'turn_completion','') NOT IN ('completed','ambiguous')
  OR COALESCE(o->>'inference_count_basis','') NOT IN ('provider_reported','host_observed','unknown')
  OR jsonb_typeof(parts) IS DISTINCT FROM 'array' OR jsonb_array_length(parts)>64
  OR jsonb_typeof(o->'request_observations_complete') IS DISTINCT FROM 'boolean'
  OR ((o->'underlying_inference_count'='null'::jsonb) IS DISTINCT FROM (o->>'inference_count_basis'='unknown'))
  OR (o->'underlying_inference_count'<>'null'::jsonb AND COALESCE(o->>'underlying_inference_count','')!~'^[0-9]+$')
 THEN RETURN false; END IF;
 n:=jsonb_array_length(parts);
 IF (o->>'underlying_inference_count')::bigint<n OR (SELECT count(DISTINCT value->>'request_id_hash') FROM jsonb_array_elements(parts))<>n THEN RETURN false; END IF;
 FOREACH r IN ARRAY ARRAY[o->'reported_total',o->'estimated_total'] LOOP
  IF r IS DISTINCT FROM 'null'::jsonb AND NOT memoriesql.model_usage_units_valid(r) THEN RETURN false; END IF;
 END LOOP;
 FOR r IN SELECT value FROM jsonb_array_elements(parts) LOOP
  IF jsonb_typeof(r) IS DISTINCT FROM 'object' OR r-ARRAY['request_id_hash','usage','provenance']<>'{}'::jsonb
   OR (SELECT count(*) FROM jsonb_object_keys(r))<>3
   OR COALESCE(r->>'request_id_hash','')!~'^[a-f0-9]{64}$'
   OR COALESCE(r->>'provenance','') NOT IN ('provider_reported','framework_estimated','unavailable')
   OR ((r->'usage'='null'::jsonb) IS DISTINCT FROM (r->>'provenance'='unavailable'))
   OR (r->'usage'<>'null'::jsonb AND NOT memoriesql.model_usage_units_valid(r->'usage')) THEN RETURN false; END IF;
 END LOOP;
 IF o->'request_observations_complete'='true'::jsonb THEN
  IF o->>'inference_count_basis' IS DISTINCT FROM 'host_observed' OR (o->>'underlying_inference_count')::bigint IS DISTINCT FROM n THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(parts) p WHERE p->'usage'='null'::jsonb) THEN
   SELECT jsonb_object_agg(k,COALESCE((SELECT sum((p->'usage'->>k)::numeric) FROM jsonb_array_elements(parts) p),0)) INTO expected
    FROM unnest(ARRAY['input_tokens','output_tokens','total_tokens','cached_input_tokens','cache_write_tokens','reasoning_tokens','tool_calls']) k;
   basis:=CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(parts) p WHERE p->>'provenance'<>'provider_reported') THEN 'framework_estimated' ELSE 'provider_reported' END;
   IF o->'reported_total'<>'null'::jsonb AND basis='provider_reported' AND o->'reported_total'<>expected THEN RETURN false; END IF;
  END IF;
 END IF;
 IF o->'reported_total'<>'null'::jsonb THEN expected:=o->'reported_total';basis:='provider_reported';
 ELSIF expected='null'::jsonb AND o->'estimated_total'<>'null'::jsonb THEN expected:=o->'estimated_total';basis:='framework_estimated'; END IF;
 RETURN expected IS NOT DISTINCT FROM u AND basis IS NOT DISTINCT FROM provenance;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.managed_observation_valid(jsonb,jsonb,text) FROM PUBLIC;

CREATE OR REPLACE VIEW memoriesql.model_request_usage_fold
WITH (security_invoker = true)
AS
SELECT
    intent.tenant_id,
    intent.workspace_id,
    intent.access_scope_id,
    intent.request_id,
    intent.task_id,
    intent.attempt_id,
    intent.run_id,
    intent.parent_run_id,
    intent.module_key,
    intent.operation_key,
    intent.task_kind,
    intent.model_profile_key,
    intent.model_profile_revision,
    intent.provider_key,
    intent.model_id,
    intent.billing_basis,
    intent.allowance_state,
    intent.pricing_revision_id,
    intent.quality_policy_revision_id,
    intent.quality_tier,
    intent.recorded_at AS request_recorded_at,
    CASE
        WHEN usage_event.event_id IS NOT NULL THEN usage_event.usage_state
        WHEN outcome_event.event_id IS NOT NULL THEN outcome_event.usage_state
        ELSE 'uncertain'
    END AS usage_state,
    COALESCE(
        usage_event.usage_provenance,
        outcome_event.usage_provenance,
        'unavailable'
    )
        AS usage_provenance,
    COALESCE(outcome_event.outcome, 'unknown') AS outcome,
    usage_event.input_tokens,
    usage_event.output_tokens,
    usage_event.total_tokens,
    usage_event.cached_input_tokens,
    usage_event.cache_write_tokens,
    usage_event.reasoning_tokens,
    usage_event.tool_calls,
    cash_event.provider_reported_cost_microunits,
    pricing.currency AS shadow_currency,
    CASE WHEN usage_event.usage_state = 'reported'
              AND pricing.pricing_revision_id IS NOT NULL
         THEN memoriesql.catalog_model_cost_microunits(
            usage_event.input_tokens, usage_event.cached_input_tokens,
            usage_event.cache_write_tokens,
            usage_event.output_tokens,
            pricing.input_microunits_per_million_tokens,
            pricing.cached_input_microunits_per_million_tokens,
            pricing.cache_write_microunits_per_million_tokens,
            pricing.output_microunits_per_million_tokens
         ) ELSE NULL END AS shadow_catalog_cost_microunits,
    CASE
        WHEN cash_event.provider_reported_cost_microunits IS NOT NULL
            THEN cash_event.provider_reported_cost_microunits
        WHEN intent.billing_basis = 'subscription'
             AND intent.allowance_state = 'covered' THEN 0
        WHEN intent.billing_basis = 'metered'
             AND usage_event.usage_state = 'reported'
             AND pricing.pricing_revision_id IS NOT NULL
            THEN memoriesql.catalog_model_cost_microunits(
                usage_event.input_tokens, usage_event.cached_input_tokens,
                usage_event.cache_write_tokens,
                usage_event.output_tokens,
                pricing.input_microunits_per_million_tokens,
                pricing.cached_input_microunits_per_million_tokens,
                pricing.cache_write_microunits_per_million_tokens,
                pricing.output_microunits_per_million_tokens
            )
        ELSE NULL
    END AS incremental_cash_exposure_microunits,
    CASE
        WHEN cash_event.provider_reported_cost_microunits IS NOT NULL
            THEN 'provider_reported'
        WHEN intent.billing_basis = 'subscription'
             AND intent.allowance_state = 'covered'
            THEN 'subscription_covered'
        WHEN intent.billing_basis = 'local' THEN 'local'
        WHEN intent.billing_basis = 'metered'
             AND usage_event.usage_state = 'reported'
             AND pricing.pricing_revision_id IS NOT NULL
            THEN 'catalog_estimate'
        ELSE 'unknown'
    END AS incremental_cash_cost_basis,
    intent.conservative_reservation_microunits,
    CASE WHEN outcome_event.event_id IS NULL THEN 'held_uncertain'
         ELSE 'event_observed' END AS reservation_state,
    COALESCE(event_ids.contributing_event_ids, ARRAY[]::uuid[])
        AS contributing_event_ids,
    1 AS fold_version
FROM memoriesql.model_provider_request_intents AS intent
LEFT JOIN memoriesql.model_pricing_revisions AS pricing
  ON pricing.pricing_revision_id = intent.pricing_revision_id
LEFT JOIN LATERAL (
    SELECT event.*
    FROM memoriesql.model_usage_events AS event
    WHERE event.tenant_id = intent.tenant_id
      AND event.request_id = intent.request_id
      AND event.usage_state = 'reported'
    ORDER BY event.recorded_at DESC, event.event_id DESC
    LIMIT 1
) AS usage_event ON true
LEFT JOIN LATERAL (
    SELECT event.provider_reported_cost_microunits
    FROM memoriesql.model_usage_events AS event
    WHERE event.tenant_id = intent.tenant_id
      AND event.request_id = intent.request_id
      AND event.provider_reported_cost_microunits IS NOT NULL
    ORDER BY event.recorded_at DESC, event.event_id DESC
    LIMIT 1
) AS cash_event ON true
LEFT JOIN LATERAL (
    SELECT event.event_id, event.outcome, event.usage_state,
        event.usage_provenance
    FROM memoriesql.model_usage_events AS event
    WHERE event.tenant_id = intent.tenant_id
      AND event.request_id = intent.request_id
    ORDER BY event.recorded_at DESC, event.event_id DESC
    LIMIT 1
) AS outcome_event ON true
LEFT JOIN LATERAL (
    SELECT array_agg(event.event_id ORDER BY event.recorded_at, event.event_id)
        AS contributing_event_ids
    FROM memoriesql.model_usage_events AS event
    WHERE event.tenant_id = intent.tenant_id
      AND event.request_id = intent.request_id
) AS event_ids ON true
WHERE intent.dispatch_boundary='single_inference';
CREATE VIEW memoriesql.model_dispatch_usage_fold_v2
WITH (security_invoker = true)
AS
SELECT
    intent.tenant_id,
    intent.workspace_id,
    intent.access_scope_id,
    intent.request_id,
    intent.task_id,
    intent.attempt_id,
    intent.run_id,
    intent.parent_run_id,
    intent.module_key,
    intent.operation_key,
    intent.task_kind,
    intent.model_profile_key,
    intent.model_profile_revision,
    intent.provider_key,
    intent.model_id,
    intent.billing_basis,
    intent.allowance_state,
    intent.pricing_revision_id,
    intent.quality_policy_revision_id,
    intent.quality_tier,
    intent.recorded_at AS request_recorded_at,
    CASE
        WHEN usage_event.event_id IS NOT NULL THEN usage_event.usage_state
        WHEN outcome_event.event_id IS NOT NULL THEN outcome_event.usage_state
        ELSE 'uncertain'
    END AS usage_state,
    COALESCE(
        usage_event.usage_provenance,
        outcome_event.usage_provenance,
        'unavailable'
    )
        AS usage_provenance,
    COALESCE(outcome_event.outcome, 'unknown') AS outcome,
    usage_event.input_tokens,
    usage_event.output_tokens,
    usage_event.total_tokens,
    usage_event.cached_input_tokens,
    usage_event.cache_write_tokens,
    usage_event.reasoning_tokens,
    usage_event.tool_calls,
    cash_event.provider_reported_cost_microunits,
    pricing.currency AS shadow_currency,
    CASE WHEN usage_event.usage_state = 'reported'
              AND pricing.pricing_revision_id IS NOT NULL
         THEN memoriesql.catalog_model_cost_microunits(
            usage_event.input_tokens, usage_event.cached_input_tokens,
            usage_event.cache_write_tokens,
            usage_event.output_tokens,
            pricing.input_microunits_per_million_tokens,
            pricing.cached_input_microunits_per_million_tokens,
            pricing.cache_write_microunits_per_million_tokens,
            pricing.output_microunits_per_million_tokens
         ) ELSE NULL END AS shadow_catalog_cost_microunits,
    CASE
        WHEN cash_event.provider_reported_cost_microunits IS NOT NULL
            THEN cash_event.provider_reported_cost_microunits
        WHEN intent.billing_basis = 'subscription'
             AND intent.allowance_state = 'covered' THEN 0
        WHEN intent.billing_basis = 'metered'
             AND usage_event.usage_state = 'reported'
             AND pricing.pricing_revision_id IS NOT NULL
            THEN memoriesql.catalog_model_cost_microunits(
                usage_event.input_tokens, usage_event.cached_input_tokens,
                usage_event.cache_write_tokens,
                usage_event.output_tokens,
                pricing.input_microunits_per_million_tokens,
                pricing.cached_input_microunits_per_million_tokens,
                pricing.cache_write_microunits_per_million_tokens,
                pricing.output_microunits_per_million_tokens
            )
        ELSE NULL
    END AS incremental_cash_exposure_microunits,
    CASE
        WHEN cash_event.provider_reported_cost_microunits IS NOT NULL
            THEN 'provider_reported'
        WHEN intent.billing_basis = 'subscription'
             AND intent.allowance_state = 'covered'
            THEN 'subscription_covered'
        WHEN intent.billing_basis = 'local' THEN 'local'
        WHEN intent.billing_basis = 'metered'
             AND usage_event.usage_state = 'reported'
             AND pricing.pricing_revision_id IS NOT NULL
            THEN 'catalog_estimate'
        ELSE 'unknown'
    END AS incremental_cash_cost_basis,
    intent.conservative_reservation_microunits,
    CASE WHEN outcome_event.event_id IS NULL THEN 'held_uncertain'
         ELSE 'event_observed' END AS reservation_state,
    COALESCE(event_ids.contributing_event_ids, ARRAY[]::uuid[])
        AS contributing_event_ids,
    2 AS fold_version,
    intent.dispatch_boundary, intent.supervised_qualification_id, intent.qualification_revision,
    intent.supply_attestation,
    CASE WHEN intent.dispatch_boundary='single_inference' THEN CASE WHEN outcome_event.outcome='succeeded' THEN 1 END ELSE (COALESCE(usage_event.managed_observation,outcome_event.managed_observation)->>'underlying_inference_count')::bigint END AS underlying_inference_count,
    CASE WHEN intent.dispatch_boundary='single_inference' AND outcome_event.outcome='succeeded' THEN 'qualified_single_inference' ELSE COALESCE(COALESCE(usage_event.managed_observation,outcome_event.managed_observation)->>'inference_count_basis','unknown') END AS inference_count_basis,
    COALESCE(usage_event.managed_observation,outcome_event.managed_observation) AS managed_observation
FROM memoriesql.model_provider_request_intents AS intent
LEFT JOIN memoriesql.model_pricing_revisions AS pricing
  ON pricing.pricing_revision_id = intent.pricing_revision_id
LEFT JOIN LATERAL (
    SELECT event.*
    FROM memoriesql.model_usage_events AS event
    WHERE event.tenant_id = intent.tenant_id
      AND event.request_id = intent.request_id
      AND event.usage_state = 'reported'
    ORDER BY event.recorded_at DESC, event.event_id DESC
    LIMIT 1
) AS usage_event ON true
LEFT JOIN LATERAL (
    SELECT event.provider_reported_cost_microunits
    FROM memoriesql.model_usage_events AS event
    WHERE event.tenant_id = intent.tenant_id
      AND event.request_id = intent.request_id
      AND event.provider_reported_cost_microunits IS NOT NULL
    ORDER BY event.recorded_at DESC, event.event_id DESC
    LIMIT 1
) AS cash_event ON true
LEFT JOIN LATERAL (
    SELECT event.event_id, event.outcome, event.usage_state,
        event.usage_provenance, event.managed_observation
    FROM memoriesql.model_usage_events AS event
    WHERE event.tenant_id = intent.tenant_id
      AND event.request_id = intent.request_id
    ORDER BY event.recorded_at DESC, event.event_id DESC
    LIMIT 1
) AS outcome_event ON true
LEFT JOIN LATERAL (
    SELECT array_agg(event.event_id ORDER BY event.recorded_at, event.event_id)
        AS contributing_event_ids
    FROM memoriesql.model_usage_events AS event
    WHERE event.tenant_id = intent.tenant_id
      AND event.request_id = intent.request_id
) AS event_ids ON true;

GRANT SELECT ON memoriesql.model_dispatch_usage_fold_v2 TO memoriesql_application,memoriesql_worker;

CREATE FUNCTION memoriesql.supervised_task_open(t uuid,task uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE q memoriesql.model_supervised_qualifications%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended(t::text||':semantic_outcome_authority:',0));
 SELECT * INTO q FROM memoriesql.model_supervised_qualifications WHERE tenant_id=t AND task_id=task;
 RETURN NOT FOUND OR (q.status='active' AND q.created_at<=clock_timestamp() AND (q.configuration->>'deadline')::timestamptz>clock_timestamp());
END; $$;
REVOKE ALL ON FUNCTION memoriesql.supervised_task_open(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.supervised_acceptance_valid(t uuid,task uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
DECLARE q memoriesql.model_supervised_qualifications%ROWTYPE; inputs numeric; outputs numeric; cash numeric;
BEGIN
 SELECT * INTO q FROM memoriesql.model_supervised_qualifications WHERE tenant_id=t AND task_id=task;
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
REVOKE ALL ON FUNCTION memoriesql.supervised_acceptance_valid(uuid,uuid) FROM PUBLIC;

CREATE FUNCTION memoriesql.consume_supervised_dispatch(i jsonb) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE q memoriesql.model_supervised_qualifications%ROWTYPE; route jsonb; ordinal bigint; prior_count bigint; c memoriesql.authorization_contexts%ROWTYPE;
BEGIN
 PERFORM pg_advisory_xact_lock_shared(hashtextextended((i->>'tenant_id')||':semantic_outcome_authority:',0));
 SELECT * INTO q FROM memoriesql.model_supervised_qualifications
 WHERE tenant_id=(i->>'tenant_id')::uuid AND task_id=(i->>'semantic_task_id')::uuid;
 IF NOT FOUND THEN
  IF i ? 'supervision' OR COALESCE(i->>'dispatch_boundary','single_inference')<>'single_inference' THEN RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
  RETURN;
 END IF;
 IF q.configuration IS DISTINCT FROM i->'supervision' OR q.origin_principal_id IS DISTINCT FROM (i->>'origin_principal_id')::uuid
 OR q.workspace_id IS DISTINCT FROM (i->>'workspace_id')::uuid OR q.access_scope_id IS DISTINCT FROM (i->>'access_scope_id')::uuid
 OR NOT memoriesql.supervised_task_open(q.tenant_id,q.task_id)
 OR i->>'task_kind' IS DISTINCT FROM 'memory.semantic.author-complete-unit'
 OR (i->>'task_contract_revision')::integer NOT IN (2,3,4,5)
 THEN RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
 IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'supervised_requires_read_committed'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(q.tenant_id::text||':supervised-dispatch:'||q.qualification_id::text,0));
 SELECT r.value,r.ordinality INTO route,ordinal FROM jsonb_array_elements(q.configuration->'routes') WITH ORDINALITY r
 WHERE r.value#>>'{reference,profile_key}'=i->>'model_profile_key'
 AND r.value#>'{reference,revision}'=i->'model_profile_revision';
 IF route IS NULL OR route->'target' IS DISTINCT FROM i->'target'
 OR route->>'qualification_revision' IS DISTINCT FROM i->>'qualification_revision'
 OR route->'observable_dispatch_allowance' IS DISTINCT FROM '1'::jsonb
 OR COALESCE(i->>'dispatch_boundary','single_inference') IS DISTINCT FROM (CASE WHEN ordinal=1 THEN 'managed_turn' ELSE 'single_inference' END)
 OR (ordinal=1 AND route#>>'{target,boundary_kind}' IS DISTINCT FROM 'managed_turn')
 OR (ordinal=2 AND route#>'{target,transport_retries_disabled}' IS DISTINCT FROM 'true'::jsonb)
 THEN RAISE EXCEPTION 'supervised_route_mismatch' USING ERRCODE='42501'; END IF;
 -- Count all attempts, including uncertain/unstarted/failed intents. Never refund.
 SELECT count(*) INTO prior_count FROM memoriesql.model_provider_request_intents
 WHERE tenant_id=q.tenant_id AND supervised_qualification_id=q.qualification_id;
 IF prior_count<>ordinal-1 OR EXISTS(SELECT 1 FROM memoriesql.model_provider_request_intents WHERE request_id=(i->>'request_id')::uuid)
 THEN RAISE EXCEPTION 'supervised_dispatch_allowance_consumed' USING ERRCODE='55000'; END IF;
 IF prior_count>0 AND NOT memoriesql.supervised_acceptance_valid(q.tenant_id,q.task_id)
 THEN RAISE EXCEPTION 'supervised_prior_dispatch_unsettled_or_stop_reached' USING ERRCODE='55000'; END IF;
 IF EXISTS(SELECT 1 FROM memoriesql.model_provider_request_intents WHERE tenant_id=q.tenant_id AND task_id=q.task_id AND supervised_qualification_id IS DISTINCT FROM q.qualification_id)
 THEN RAISE EXCEPTION 'supervised_prior_dispatch_outside_approval' USING ERRCODE='55000'; END IF;
 -- Hard-bounded follow-up reservations must also fit the remaining reported stop allowance.
 IF ordinal=2 AND EXISTS(SELECT 1 FROM memoriesql.model_dispatch_usage_fold_v2 f WHERE f.tenant_id=q.tenant_id AND f.supervised_qualification_id=q.qualification_id
  AND (f.input_tokens+(i->>'max_input_tokens')::bigint>(q.configuration->>'reported_input_token_stop')::bigint
   OR f.output_tokens+(i->>'max_output_tokens')::bigint>(q.configuration->>'reported_generated_token_stop')::bigint
   OR (q.configuration->>'reported_cash_stop_microunits' IS NOT NULL AND f.incremental_cash_exposure_microunits+(i->>'conservative_reservation_microunits')::bigint>(q.configuration->>'reported_cash_stop_microunits')::bigint)))
 THEN RAISE EXCEPTION 'supervised_followup_exceeds_remaining_allowance' USING ERRCODE='55000'; END IF;
END; $$;
REVOKE ALL ON FUNCTION memoriesql.consume_supervised_dispatch(jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION memoriesql.record_model_provider_request_intent(
    requested_intent jsonb,
    requested_worker_id text,
    requested_worker_instance_id text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record record;
    run_record memoriesql.semantic_task_runs%ROWTYPE;
    pricing_record memoriesql.model_pricing_revisions%ROWTYPE;
    request_uuid uuid;
    tenant_uuid uuid;
    workspace_uuid uuid;
    scope_uuid uuid;
    task_uuid uuid;
    attempt_uuid uuid;
    principal_uuid uuid;
    delegation_uuid uuid;
    pricing_uuid uuid;
    quality_policy_uuid uuid;
    database_now timestamp with time zone := clock_timestamp();
    existing_reserved numeric := 0;
    maximum_catalog_reservation bigint := 0;
    requested_reservation bigint;
    requested_ceiling bigint;
    prior_intent memoriesql.model_provider_request_intents%ROWTYPE;
    candidate_intent memoriesql.model_provider_request_intents%ROWTYPE;
BEGIN
    IF jsonb_typeof(requested_intent) <> 'object'
       OR octet_length(requested_intent::text) > 16384 THEN
        RAISE EXCEPTION 'invalid provider request intent';
    END IF;
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    IF context_record.tenant_id IS NULL THEN
        RAISE EXCEPTION 'authorization context is unavailable';
    END IF;
    IF context_record.principal_kind <> 'service'
       OR btrim(requested_worker_id) = ''
       OR btrim(requested_worker_instance_id) = '' THEN
        RAISE EXCEPTION 'provider request requires an exact worker identity';
    END IF;

    request_uuid := (requested_intent->>'request_id')::uuid;
    tenant_uuid := (requested_intent->>'tenant_id')::uuid;
    workspace_uuid := (requested_intent->>'workspace_id')::uuid;
    scope_uuid := (requested_intent->>'access_scope_id')::uuid;
    task_uuid := (requested_intent->>'semantic_task_id')::uuid;
    attempt_uuid := (requested_intent->>'semantic_attempt_id')::uuid;
    principal_uuid := (requested_intent->>'origin_principal_id')::uuid;
    delegation_uuid := NULLIF(
        requested_intent->>'origin_delegation_id', ''
    )::uuid;
    pricing_uuid := NULLIF(
        requested_intent->'target'->>'pricing_revision_id', ''
    )::uuid;
    quality_policy_uuid := (
        requested_intent->'target'->>'quality_policy_revision_id'
    )::uuid;
    requested_reservation := (
        requested_intent->>'conservative_reservation_microunits'
    )::bigint;
    requested_ceiling := NULLIF(
        requested_intent->>'safety_ceiling_microunits', ''
    )::bigint;
    SELECT * INTO candidate_intent
    FROM jsonb_populate_record(
        NULL::memoriesql.model_provider_request_intents,
        jsonb_build_object(
            'tenant_id', tenant_uuid,
            'workspace_id', workspace_uuid,
            'access_scope_id', scope_uuid,
            'request_id', request_uuid,
            'task_id', task_uuid,
            'attempt_id', attempt_uuid,
            'run_id', requested_intent->>'run_id',
            'parent_run_id', NULLIF(requested_intent->>'parent_run_id', ''),
            'run_role', requested_intent->>'run_role',
            'module_key', requested_intent->>'module_key',
            'operation_key', requested_intent->>'operation_key',
            'task_kind', requested_intent->>'task_kind',
            'task_contract_revision',
                (requested_intent->>'task_contract_revision')::integer,
            'origin_principal_id', principal_uuid,
            'origin_delegation_id', delegation_uuid,
            'authorization_policy_revision',
                (requested_intent->>'policy_revision')::integer,
            'model_profile_key', requested_intent->>'model_profile_key',
            'model_profile_revision',
                (requested_intent->>'model_profile_revision')::integer,
            'request_sequence',
                (requested_intent->>'request_sequence')::integer,
            'provider_key', requested_intent->'target'->>'provider_key',
            'credential_id',
                (requested_intent->'target'->>'credential_id')::uuid,
            'model_id', requested_intent->'target'->>'model_id',
            'billing_basis', requested_intent->'target'->>'billing_basis',
            'allowance_state', requested_intent->'target'->>'allowance_state',
            'pricing_revision_id', pricing_uuid,
            'quality_policy_revision_id', quality_policy_uuid,
            'quality_tier', requested_intent->'target'->>'quality_tier',
            'request_boundary_revision',
                requested_intent->'target'->>'request_boundary_revision',
            'transport_retries_disabled',
                COALESCE((requested_intent->'target'->>'transport_retries_disabled')::boolean,(requested_intent->'target'->>'host_retries_disabled')::boolean),
            'usage_provenance_expected',
                requested_intent->'target'->>'usage_provenance',
            'safety_ceiling_microunits', requested_ceiling,
            'conservative_reservation_microunits', requested_reservation,
            'max_input_tokens',
                (requested_intent->>'max_input_tokens')::bigint,
            'max_output_tokens',
                (requested_intent->>'max_output_tokens')::bigint,
            'request_payload_hash', requested_intent->>'request_payload_hash',
            'dispatch_boundary',COALESCE(requested_intent->>'dispatch_boundary','single_inference'),
            'supervised_qualification_id',(requested_intent#>>'{supervision,qualification_id}')::uuid,
            'qualification_revision',requested_intent->>'qualification_revision',
            'supply_attestation','host_visible_only'
        )
    );

    IF tenant_uuid <> context_record.tenant_id
       OR workspace_uuid <> context_record.workspace_id
       OR NOT memoriesql.current_context_scope_authorized(
            scope_uuid, 'memory.maintain', 'write'
       ) THEN
        RAISE EXCEPTION 'provider request intent is outside authorization';
    END IF;
    IF requested_intent->>'retrieval_execution_id' IS NOT NULL THEN
        RAISE EXCEPTION 'PR-01G retrieval execution authority is not registered';
    END IF;
    IF COALESCE(requested_intent->'target'->>'transport_retries_disabled',requested_intent->'target'->>'host_retries_disabled','false') <> 'true' THEN
        RAISE EXCEPTION 'provider transport request boundary is not visible';
    END IF;
    IF requested_intent->'target'->>'billing_basis' = 'subscription'
       AND requested_intent->'target'->>'allowance_state' IN (
            'exhausted', 'unknown'
       ) THEN
        RAISE EXCEPTION
            'accounting.required_model_allowance_unavailable';
    END IF;
    IF COALESCE(requested_intent->>'dispatch_boundary','single_inference')='single_inference' AND ((requested_intent->>'max_input_tokens')::bigint <>
            (requested_intent->'target'->>'max_input_tokens_per_request')::bigint
       OR (requested_intent->>'max_output_tokens')::bigint >
            (requested_intent->'target'->>'max_output_tokens_per_request')::bigint
       OR (requested_intent->>'max_output_tokens')::bigint <= 0) THEN
        RAISE EXCEPTION 'provider request reservation bounds are invalid';
    END IF;
    IF requested_ceiling IS NOT NULL AND NOT (
        (requested_intent->'target'->>'billing_basis' = 'metered'
            AND pricing_uuid IS NOT NULL)
        OR (requested_intent->'target'->>'billing_basis' = 'subscription'
            AND requested_intent->'target'->>'allowance_state' = 'covered')
    ) THEN
        RAISE EXCEPTION 'dollar ceiling has no enforceable cash basis';
    END IF;

    SELECT * INTO run_record
    FROM memoriesql.semantic_task_runs AS run
    WHERE run.tenant_id = tenant_uuid
      AND run.workspace_id = workspace_uuid
      AND run.access_scope_id = scope_uuid
      AND run.task_id = task_uuid
      AND run.attempt_id = attempt_uuid
      AND run.run_id = requested_intent->>'run_id';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'provider request has no exact durable run';
    END IF;
    IF run_record.parent_run_id IS DISTINCT FROM
            NULLIF(requested_intent->>'parent_run_id', '')
       OR run_record.run_role <> requested_intent->>'run_role'
       OR run_record.model_profile_key <>
            requested_intent->>'model_profile_key'
       OR run_record.model_profile_revision <>
            (requested_intent->>'model_profile_revision')::integer
       OR run_record.run_status <> 'running'
       OR run_record.settled THEN
        RAISE EXCEPTION 'provider request run attribution is invalid';
    END IF;
    IF memoriesql.reauthorize_semantic_task(
        tenant_uuid, task_uuid, attempt_uuid, run_record.lease_generation,
        requested_worker_id, requested_worker_instance_id, 'hydrate',
        database_now
    ) <> 'authorized' THEN
        RAISE EXCEPTION 'provider request worker lease is not live';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM memoriesql.semantic_tasks AS task
        JOIN memoriesql.access_scopes AS scope
          ON scope.tenant_id = task.tenant_id
         AND scope.workspace_id = task.workspace_id
         AND scope.access_scope_id = task.access_scope_id
        JOIN memoriesql.access_policy_revisions AS policy
          ON policy.tenant_id = scope.tenant_id
         AND policy.workspace_id = scope.workspace_id
         AND policy.access_scope_id = scope.access_scope_id
         AND policy.policy_revision_id = scope.current_policy_revision_id
        WHERE task.tenant_id = tenant_uuid
          AND task.workspace_id = workspace_uuid
          AND task.access_scope_id = scope_uuid
          AND task.task_id = task_uuid
          AND task.owning_module = requested_intent->>'module_key'
          AND task.task_kind = requested_intent->>'task_kind'
          AND task.contract_revision =
              (requested_intent->>'task_contract_revision')::integer
          AND task.origin_principal_id = principal_uuid
          AND task.origin_pairing_grant_id IS NOT DISTINCT FROM delegation_uuid
          AND policy.revision =
              (requested_intent->>'policy_revision')::integer
    ) THEN
        RAISE EXCEPTION 'provider request origin attribution is invalid';
    END IF;

    PERFORM memoriesql.consume_supervised_dispatch(requested_intent);
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('model-request:' || request_uuid::text, 0)
    );
    SELECT * INTO prior_intent
    FROM memoriesql.model_provider_request_intents AS prior
    WHERE prior.request_id = request_uuid;
    IF FOUND THEN
        candidate_intent.recorded_at := prior_intent.recorded_at;
        IF prior_intent IS DISTINCT FROM candidate_intent THEN
            RAISE EXCEPTION 'provider request idempotency collision';
        END IF;
        RETURN request_uuid;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM memoriesql.model_quality_policy_revisions AS policy
        WHERE policy.quality_policy_revision_id = quality_policy_uuid
          AND policy.default_quality_first
    ) THEN
        RAISE EXCEPTION 'quality-first policy revision is unavailable';
    END IF;
    IF requested_intent->'target'->>'quality_tier' = 'qualified_lower'
       AND NOT EXISTS (
            SELECT 1
            FROM memoriesql.model_task_profile_qualifications AS qualification
            WHERE qualification.task_kind = requested_intent->>'task_kind'
              AND qualification.contract_revision =
                  (requested_intent->>'task_contract_revision')::integer
              AND qualification.model_profile_key =
                  requested_intent->>'model_profile_key'
              AND qualification.model_profile_revision =
                  (requested_intent->>'model_profile_revision')::integer
              AND qualification.quality_policy_revision_id =
                  quality_policy_uuid
       ) THEN
        RAISE EXCEPTION 'lower-tier profile lacks frozen qualification';
    END IF;

    IF COALESCE(requested_intent->>'dispatch_boundary','single_inference')='single_inference' AND requested_intent->'target'->>'billing_basis' = 'metered'
       AND pricing_uuid IS NOT NULL THEN
        SELECT * INTO pricing_record
        FROM memoriesql.model_pricing_revisions AS pricing
        WHERE pricing.pricing_revision_id = pricing_uuid
          AND pricing.provider_key =
              requested_intent->'target'->>'provider_key'
          AND pricing.model_id = requested_intent->'target'->>'model_id'
          AND pricing.effective_from <= database_now
          AND (pricing.effective_until IS NULL
              OR pricing.effective_until > database_now);
        IF NOT FOUND THEN
            RAISE EXCEPTION 'pinned pricing revision is unavailable';
        END IF;
        maximum_catalog_reservation :=
            memoriesql.catalog_model_cost_microunits(
                (requested_intent->>'max_input_tokens')::bigint,
                0,
                0,
                (requested_intent->>'max_output_tokens')::bigint,
                GREATEST(
                    pricing_record.input_microunits_per_million_tokens,
                    pricing_record.cached_input_microunits_per_million_tokens,
                    pricing_record.cache_write_microunits_per_million_tokens
                ),
                pricing_record.cached_input_microunits_per_million_tokens,
                pricing_record.cache_write_microunits_per_million_tokens,
                pricing_record.output_microunits_per_million_tokens
            );
        IF requested_reservation < maximum_catalog_reservation THEN
            RAISE EXCEPTION 'provider reservation is not conservative';
        END IF;
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            tenant_uuid::text || ':' || attempt_uuid::text, 0
        )
    );
    IF requested_ceiling IS NOT NULL THEN
        IF EXISTS (
            SELECT 1
            FROM memoriesql.model_provider_request_intents AS prior
            WHERE prior.tenant_id = tenant_uuid
              AND prior.attempt_id = attempt_uuid
              AND NOT (
                  prior.billing_basis = 'subscription'
                  AND prior.allowance_state = 'covered'
              )
              AND NOT (
                  prior.billing_basis = 'metered'
                  AND prior.pricing_revision_id IS NOT NULL
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM memoriesql.model_usage_events AS event
                  WHERE event.tenant_id = prior.tenant_id
                    AND event.request_id = prior.request_id
                    AND event.provider_reported_cost_microunits IS NOT NULL
              )
        ) THEN
            RAISE EXCEPTION 'prior request has no enforceable cash basis';
        END IF;
        SELECT COALESCE(sum(
            CASE
                WHEN prior.billing_basis = 'subscription'
                     AND prior.allowance_state = 'covered' THEN 0
                ELSE COALESCE(
                    (
                        SELECT event.provider_reported_cost_microunits
                        FROM memoriesql.model_usage_events AS event
                        WHERE event.tenant_id = prior.tenant_id
                          AND event.request_id = prior.request_id
                          AND event.provider_reported_cost_microunits IS NOT NULL
                        ORDER BY event.recorded_at DESC, event.event_id DESC
                        LIMIT 1
                    ),
                    prior.conservative_reservation_microunits
                )
            END
        ), 0) INTO existing_reserved
        FROM memoriesql.model_provider_request_intents AS prior
        WHERE prior.tenant_id = tenant_uuid
          AND prior.attempt_id = attempt_uuid;
        IF existing_reserved + requested_reservation > requested_ceiling THEN
            RAISE EXCEPTION 'accounting.cost_safety_ceiling';
        END IF;
    END IF;

    INSERT INTO memoriesql.model_provider_request_intents (
        tenant_id, workspace_id, access_scope_id, request_id, task_id,
        attempt_id, run_id, parent_run_id, run_role, module_key,
        operation_key, task_kind, task_contract_revision, origin_principal_id,
        origin_delegation_id, authorization_policy_revision,
        model_profile_key, model_profile_revision, request_sequence,
        provider_key, credential_id, model_id, billing_basis,
        allowance_state, pricing_revision_id, quality_policy_revision_id,
        quality_tier, request_boundary_revision,
        transport_retries_disabled, usage_provenance_expected,
        safety_ceiling_microunits,
        conservative_reservation_microunits, max_input_tokens,
        max_output_tokens, request_payload_hash, recorded_at,dispatch_boundary,supervised_qualification_id,qualification_revision,supply_attestation
    ) VALUES (
        tenant_uuid, workspace_uuid, scope_uuid, request_uuid, task_uuid,
        attempt_uuid, requested_intent->>'run_id',
        NULLIF(requested_intent->>'parent_run_id', ''),
        requested_intent->>'run_role', requested_intent->>'module_key',
        requested_intent->>'operation_key', requested_intent->>'task_kind',
        (requested_intent->>'task_contract_revision')::integer,
        principal_uuid, delegation_uuid,
        (requested_intent->>'policy_revision')::integer,
        requested_intent->>'model_profile_key',
        (requested_intent->>'model_profile_revision')::integer,
        (requested_intent->>'request_sequence')::integer,
        requested_intent->'target'->>'provider_key',
        (requested_intent->'target'->>'credential_id')::uuid,
        requested_intent->'target'->>'model_id',
        requested_intent->'target'->>'billing_basis',
        requested_intent->'target'->>'allowance_state', pricing_uuid,
        quality_policy_uuid, requested_intent->'target'->>'quality_tier',
        requested_intent->'target'->>'request_boundary_revision',
        COALESCE((requested_intent->'target'->>'transport_retries_disabled')::boolean,(requested_intent->'target'->>'host_retries_disabled')::boolean),
        requested_intent->'target'->>'usage_provenance',
        requested_ceiling, requested_reservation,
        (requested_intent->>'max_input_tokens')::bigint,
        (requested_intent->>'max_output_tokens')::bigint,
        requested_intent->>'request_payload_hash', database_now,COALESCE(requested_intent->>'dispatch_boundary','single_inference'),(requested_intent#>>'{supervision,qualification_id}')::uuid,requested_intent->>'qualification_revision','host_visible_only'
    );
    RETURN request_uuid;
END;
$$;
CREATE OR REPLACE FUNCTION memoriesql.append_model_usage_event(
    requested_event jsonb,
    requested_worker_id text,
    requested_worker_instance_id text
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record record;
    intent_record memoriesql.model_provider_request_intents%ROWTYPE;
    event_uuid uuid;
    request_uuid uuid;
    database_now timestamp with time zone := clock_timestamp();
    prior_event memoriesql.model_usage_events%ROWTYPE;
    candidate_event memoriesql.model_usage_events%ROWTYPE;
BEGIN
    IF jsonb_typeof(requested_event) <> 'object'
       OR octet_length(requested_event::text) > 49152 THEN
        RAISE EXCEPTION 'invalid model usage event';
    END IF;
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    event_uuid := (requested_event->>'event_id')::uuid;
    request_uuid := (requested_event->>'request_id')::uuid;
    SELECT * INTO intent_record
    FROM memoriesql.model_provider_request_intents AS intent
    WHERE intent.request_id = request_uuid;
    IF NOT FOUND
       OR context_record.principal_kind <> 'service'
       OR context_record.expires_at <= database_now
       OR btrim(requested_worker_id) = ''
       OR btrim(requested_worker_instance_id) = ''
       OR context_record.tenant_id <> intent_record.tenant_id
       OR context_record.workspace_id <> intent_record.workspace_id
       OR NOT EXISTS (
            SELECT 1
            FROM memoriesql.semantic_task_attempts AS attempt
            WHERE attempt.tenant_id = intent_record.tenant_id
              AND attempt.task_id = intent_record.task_id
              AND attempt.attempt_id = intent_record.attempt_id
              AND attempt.claimant_principal_id = context_record.principal_id
              AND attempt.worker_id = requested_worker_id
              AND attempt.worker_instance_id = requested_worker_instance_id
       ) THEN
        RAISE EXCEPTION 'usage event is outside authorization';
    END IF;
    IF (intent_record.dispatch_boundary='managed_turn' AND NOT memoriesql.managed_observation_valid(requested_event->'observation',requested_event->'usage',requested_event->>'usage_provenance'))
       OR (intent_record.dispatch_boundary='single_inference' AND requested_event ? 'observation') THEN
        RAISE EXCEPTION 'invalid_managed_usage_observation' USING ERRCODE='22023'; END IF;
    IF intent_record.dispatch_boundary='single_inference' AND octet_length(requested_event::text)>8192 THEN RAISE EXCEPTION 'invalid model usage event'; END IF;
    IF intent_record.billing_basis = 'subscription'
       AND intent_record.allowance_state = 'covered'
       AND requested_event->>'provider_reported_cost_microunits' IS NOT NULL THEN
        RAISE EXCEPTION 'covered subscription request cannot report cash spend';
    END IF;
    SELECT * INTO candidate_event
    FROM jsonb_populate_record(
        NULL::memoriesql.model_usage_events,
        jsonb_build_object(
            'tenant_id', intent_record.tenant_id,
            'workspace_id', intent_record.workspace_id,
            'access_scope_id', intent_record.access_scope_id,
            'event_id', event_uuid,
            'request_id', request_uuid,
            'dedupe_key', requested_event->>'dedupe_key',
            'event_kind', requested_event->>'event_kind',
            'outcome', requested_event->>'outcome',
            'usage_state', requested_event->>'usage_state',
            'usage_provenance', requested_event->>'usage_provenance',
            'input_tokens',
                (requested_event->'usage'->>'input_tokens')::bigint,
            'output_tokens',
                (requested_event->'usage'->>'output_tokens')::bigint,
            'total_tokens',
                (requested_event->'usage'->>'total_tokens')::bigint,
            'cached_input_tokens',
                (requested_event->'usage'->>'cached_input_tokens')::bigint,
            'cache_write_tokens',
                (requested_event->'usage'->>'cache_write_tokens')::bigint,
            'reasoning_tokens',
                (requested_event->'usage'->>'reasoning_tokens')::bigint,
            'tool_calls',
                (requested_event->'usage'->>'tool_calls')::bigint,
            'provider_reported_cost_microunits', NULLIF(
                requested_event->>'provider_reported_cost_microunits', ''
            )::bigint,
            'provider_reported_currency',
                NULLIF(requested_event->>'provider_reported_currency', ''),
            'provider_request_id_hash',
                NULLIF(requested_event->>'provider_request_id_hash', ''),
            'diagnostic_code',
                NULLIF(requested_event->>'diagnostic_code', ''),
            'managed_observation',requested_event->'observation'
        )
    );
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            intent_record.tenant_id::text || ':model-usage:' ||
            (requested_event->>'dedupe_key'), 0
        )
    );
    SELECT * INTO prior_event
    FROM memoriesql.model_usage_events AS prior
    WHERE prior.tenant_id = intent_record.tenant_id
      AND prior.dedupe_key = requested_event->>'dedupe_key';
    IF FOUND THEN
        candidate_event.recorded_at := prior_event.recorded_at;
        IF prior_event IS DISTINCT FROM candidate_event THEN
            RAISE EXCEPTION 'usage event idempotency collision';
        END IF;
        RETURN prior_event.event_id;
    END IF;

    INSERT INTO memoriesql.model_usage_events (
        tenant_id, workspace_id, access_scope_id, event_id, request_id,
        dedupe_key, event_kind, outcome, usage_state, usage_provenance,
        input_tokens,
        output_tokens, total_tokens, cached_input_tokens, cache_write_tokens,
        reasoning_tokens,
        tool_calls, provider_reported_cost_microunits,
        provider_reported_currency, provider_request_id_hash,
        diagnostic_code, recorded_at,managed_observation
    ) VALUES (
        intent_record.tenant_id, intent_record.workspace_id,
        intent_record.access_scope_id, event_uuid, request_uuid,
        requested_event->>'dedupe_key', requested_event->>'event_kind',
        requested_event->>'outcome', requested_event->>'usage_state',
        requested_event->>'usage_provenance',
        (requested_event->'usage'->>'input_tokens')::bigint,
        (requested_event->'usage'->>'output_tokens')::bigint,
        (requested_event->'usage'->>'total_tokens')::bigint,
        (requested_event->'usage'->>'cached_input_tokens')::bigint,
        (requested_event->'usage'->>'cache_write_tokens')::bigint,
        (requested_event->'usage'->>'reasoning_tokens')::bigint,
        (requested_event->'usage'->>'tool_calls')::bigint,
        NULLIF(
            requested_event->>'provider_reported_cost_microunits', ''
        )::bigint,
        NULLIF(requested_event->>'provider_reported_currency', ''),
        NULLIF(requested_event->>'provider_request_id_hash', ''),
        NULLIF(requested_event->>'diagnostic_code', ''), database_now,requested_event->'observation'
    );
    RETURN event_uuid;
END;
$$;
CREATE OR REPLACE FUNCTION memoriesql.complete_input_authorize(t uuid, binding_id uuid, dispatch_id uuid)
RETURNS memoriesql.logical_unit_materializations LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,memoriesql SET lock_timeout='500ms' AS $$
DECLARE b memoriesql.logical_unit_materializations%ROWTYPE; p memoriesql.evidence_packages%ROWTYPE;
    c memoriesql.authorization_contexts%ROWTYPE; s memoriesql.source_objects%ROWTYPE;
BEGIN
    IF current_setting('transaction_isolation') IS DISTINCT FROM 'read committed' THEN
        RAISE EXCEPTION 'complete_input_requires_read_committed' USING ERRCODE='25000'; END IF;
    SELECT * INTO c FROM memoriesql.current_authorization_context();
    SELECT * INTO b FROM memoriesql.logical_unit_materializations WHERE tenant_id=t AND workspace_id=c.workspace_id AND task_id=binding_id;
    SELECT * INTO p FROM memoriesql.evidence_packages WHERE tenant_id=t AND package_id=b.package_id;
    s:=memoriesql.evidence_package_authorize(p.source_object_id,false);
    IF c.tenant_id IS DISTINCT FROM t OR c.expires_at<=clock_timestamp() OR b.task_id IS NULL OR
       NOT memoriesql.semantic_task_origin_capability_authorized(t,binding_id,'source.raw.read',clock_timestamp()) OR
       NOT memoriesql.semantic_task_origin_authorized(t,binding_id,clock_timestamp()) OR
       NOT memoriesql.current_context_scope_time_authorized(b.access_scope_id,'read',clock_timestamp()) OR
       p.sealed_receipt_id IS NULL OR b.package_pin->>'inventory_sha256' IS DISTINCT FROM p.inventory_hash OR
       ((b.scope_declaration IS NULL AND p.declaration#>>'{qualification,boundary}' IS DISTINCT FROM 'qualified_native_unit') OR (b.scope_declaration IS NOT NULL AND NOT memoriesql.declared_scope_package_ready(p.declaration))) OR
       p.declaration#>>'{qualification,physical_records}' IS DISTINCT FROM 'complete' OR
       p.declaration#>>'{qualification,normalized_input}' IS DISTINCT FROM 'complete' OR
       (b.scope_declaration IS NULL AND p.declaration#>>'{qualification,source_completeness}' IS DISTINCT FROM 'producer_attested') OR
       p.declaration#>'{qualification,unresolved_coverage}' IS DISTINCT FROM '[]'::jsonb OR
       NOT EXISTS(SELECT 1 FROM memoriesql.evidence_producer_policies q WHERE q.tenant_id=t AND q.producer_policy_id=b.producer_policy_id
          AND (b.scope_declaration IS NULL OR q.allow_declared_scopes) AND q.status='active' AND q.created_at<=clock_timestamp() AND q.expires_at>clock_timestamp()
          AND q.source_object_id=p.source_object_id AND q.producer_principal_id=p.producer_principal_id
          AND q.qualification_ref=p.declaration#>>'{qualification,qualification_ref}' AND q.normalization_policy_version=p.declaration->>'normalization_policy_version') OR
       NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_dispatch_policies d WHERE d.tenant_id=t AND d.dispatch_policy_id=dispatch_id
          AND d.workspace_id=b.workspace_id AND d.access_scope_id=b.access_scope_id AND d.source_object_id=p.source_object_id
          AND d.status='active' AND d.created_at<=clock_timestamp() AND d.expires_at>clock_timestamp()) THEN
        RAISE EXCEPTION 'complete_input_authority_unavailable' USING ERRCODE='42501'; END IF;
    IF EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=t AND e.binding_task_id=binding_id AND e.source_schema_version<>s.schema_version) THEN
        RAISE EXCEPTION 'complete_input_source_changed' USING ERRCODE='55000'; END IF;
    IF EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e WHERE e.tenant_id=t AND e.binding_task_id=binding_id
       AND NOT memoriesql.supervised_task_open(t,e.execution_task_id)) THEN
        RAISE EXCEPTION 'supervised_qualification_unavailable' USING ERRCODE='42501'; END IF;
    RETURN b;
END; $$;
CREATE OR REPLACE FUNCTION memoriesql.complete_input_exposure_valid(t uuid, task uuid, attempt uuid, generation bigint) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,memoriesql AS $$
 SELECT memoriesql.supervised_acceptance_valid(t,task) AND EXISTS(SELECT 1 FROM memoriesql.complete_input_executions e
 JOIN memoriesql.logical_unit_materializations b ON b.tenant_id=e.tenant_id AND b.task_id=e.binding_task_id
 WHERE e.tenant_id=t AND e.execution_task_id=task
 AND (e.execution_contract_revision NOT IN (3,4,5) OR (SELECT count(*)=(b.package_pin->>'required_parts')::integer FROM memoriesql.evidence_package_parts p WHERE p.tenant_id=t AND p.package_id=b.package_id))
 AND NOT EXISTS(
  SELECT 1 FROM memoriesql.evidence_package_parts p WHERE p.tenant_id=t AND p.package_id=b.package_id AND
   CASE WHEN e.execution_contract_revision IN (3,4,5) THEN NOT COALESCE(
    (SELECT range_agg(int4range(x.start_character,x.end_character,'[)')) @> int4range(0,(p.inventory->>'characters')::integer,'[)')
     FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=t AND x.task_id=task AND x.attempt_id=attempt AND x.lease_generation=generation
      AND x.package_id=b.package_id AND x.inventory_hash=b.package_pin->>'inventory_sha256' AND x.part_id=p.part_id),false)
   ELSE NOT EXISTS(SELECT 1 FROM memoriesql.complete_input_exposures x WHERE x.tenant_id=t AND x.task_id=task AND x.attempt_id=attempt AND x.lease_generation=generation
    AND x.package_id=b.package_id AND x.inventory_hash=b.package_pin->>'inventory_sha256' AND x.part_id=p.part_id AND x.end_character=(p.inventory->>'characters')::integer) END))
$$;
