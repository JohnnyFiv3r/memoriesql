-- PR-01G: quality-first provider-request accounting, pricing, forecasts, and
-- replayable cost folds. PostgreSQL is the sole durable authority. Prompt,
-- response, evidence, tool arguments, and provider prose are never retained.

CREATE TABLE memoriesql.model_pricing_revisions (
    pricing_revision_id uuid PRIMARY KEY,
    provider_key text NOT NULL,
    model_id text NOT NULL,
    currency text NOT NULL,
    effective_from timestamp with time zone NOT NULL,
    effective_until timestamp with time zone,
    input_microunits_per_million_tokens bigint NOT NULL,
    cached_input_microunits_per_million_tokens bigint NOT NULL,
    cache_write_microunits_per_million_tokens bigint NOT NULL,
    output_microunits_per_million_tokens bigint NOT NULL,
    source_hash text NOT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT model_pricing_revisions_identity_shape CHECK (
        provider_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(provider_key) <= 128
        AND length(model_id) BETWEEN 1 AND 255
        AND currency = 'USD'
        AND source_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT model_pricing_revisions_range CHECK (
        effective_until IS NULL OR effective_until > effective_from
    ),
    CONSTRAINT model_pricing_revisions_integer_money CHECK (
        input_microunits_per_million_tokens >= 0
        AND cached_input_microunits_per_million_tokens >= 0
        AND cache_write_microunits_per_million_tokens >= 0
        AND output_microunits_per_million_tokens >= 0
    )
);

CREATE TABLE memoriesql.model_quality_policy_revisions (
    quality_policy_revision_id uuid PRIMARY KEY,
    policy_key text NOT NULL,
    revision integer NOT NULL,
    default_quality_first boolean NOT NULL,
    lower_tier_requires_frozen_qualification boolean NOT NULL,
    source_hash text NOT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT model_quality_policy_revisions_key_uq UNIQUE (
        policy_key, revision
    ),
    CONSTRAINT model_quality_policy_revisions_shape CHECK (
        policy_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND length(policy_key) <= 128
        AND revision > 0
        AND source_hash ~ '^[a-f0-9]{64}$'
    )
);

INSERT INTO memoriesql.model_quality_policy_revisions (
    quality_policy_revision_id,
    policy_key,
    revision,
    default_quality_first,
    lower_tier_requires_frozen_qualification,
    source_hash
) VALUES (
    '019d0000-0000-7000-8000-000000000001',
    'quality-first',
    1,
    true,
    true,
    '6d64aa8c874b9a7c156e02e5559f289eed280159aa70f56d813f847072e22d14'
);

CREATE TABLE memoriesql.model_task_profile_qualifications (
    qualification_id uuid PRIMARY KEY,
    task_kind text NOT NULL,
    contract_revision integer NOT NULL,
    model_profile_key text NOT NULL,
    model_profile_revision integer NOT NULL,
    quality_policy_revision_id uuid NOT NULL REFERENCES
        memoriesql.model_quality_policy_revisions (
            quality_policy_revision_id
        ),
    evidence_hash text NOT NULL,
    signer_key text NOT NULL,
    signature_hash text NOT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT model_task_profile_qualifications_exact_uq UNIQUE (
        task_kind, contract_revision, model_profile_key,
        model_profile_revision, quality_policy_revision_id
    ),
    CONSTRAINT model_task_profile_qualifications_shape CHECK (
        task_kind ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND model_profile_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND signer_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND contract_revision > 0
        AND model_profile_revision > 0
        AND evidence_hash ~ '^[a-f0-9]{64}$'
        AND signature_hash ~ '^[a-f0-9]{64}$'
    )
);

CREATE TABLE memoriesql.model_optimization_target_revisions (
    optimization_target_id uuid PRIMARY KEY,
    target_key text NOT NULL,
    revision integer NOT NULL,
    task_kind text NOT NULL,
    contract_revision integer NOT NULL,
    baseline_model_profile_key text NOT NULL,
    baseline_model_profile_revision integer NOT NULL,
    candidate_model_profile_key text NOT NULL,
    candidate_model_profile_revision integer NOT NULL,
    baseline_evidence_hash text NOT NULL,
    target_reduction_basis_points integer NOT NULL,
    quality_gate_hash text NOT NULL,
    source_hash text NOT NULL,
    created_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT model_optimization_target_revisions_key_uq UNIQUE (
        target_key, revision
    ),
    CONSTRAINT model_optimization_target_revisions_shape CHECK (
        target_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND task_kind ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND baseline_model_profile_key ~
            '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND candidate_model_profile_key ~
            '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND revision > 0
        AND contract_revision > 0
        AND baseline_model_profile_revision > 0
        AND candidate_model_profile_revision > 0
        AND target_reduction_basis_points BETWEEN 0 AND 10000
        AND baseline_evidence_hash ~ '^[a-f0-9]{64}$'
        AND quality_gate_hash ~ '^[a-f0-9]{64}$'
        AND source_hash ~ '^[a-f0-9]{64}$'
    )
);

CREATE TABLE memoriesql.model_provider_request_intents (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    request_id uuid NOT NULL,
    task_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    run_id text NOT NULL,
    parent_run_id text,
    run_role text NOT NULL,
    module_key text NOT NULL,
    operation_key text NOT NULL,
    task_kind text NOT NULL,
    task_contract_revision integer NOT NULL,
    origin_principal_id uuid NOT NULL,
    origin_delegation_id uuid,
    authorization_policy_revision integer NOT NULL,
    model_profile_key text NOT NULL,
    model_profile_revision integer NOT NULL,
    request_sequence integer NOT NULL,
    provider_key text NOT NULL,
    credential_id uuid NOT NULL,
    model_id text NOT NULL,
    billing_basis text NOT NULL,
    allowance_state text NOT NULL,
    pricing_revision_id uuid REFERENCES memoriesql.model_pricing_revisions (
        pricing_revision_id
    ),
    quality_policy_revision_id uuid NOT NULL REFERENCES
        memoriesql.model_quality_policy_revisions (
            quality_policy_revision_id
        ),
    quality_tier text NOT NULL,
    request_boundary_revision text NOT NULL,
    transport_retries_disabled boolean NOT NULL,
    usage_provenance_expected text NOT NULL,
    safety_ceiling_microunits bigint,
    conservative_reservation_microunits bigint NOT NULL,
    max_input_tokens bigint NOT NULL,
    max_output_tokens bigint NOT NULL,
    request_payload_hash text NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT model_provider_request_intents_pk PRIMARY KEY (
        tenant_id, request_id
    ),
    CONSTRAINT model_provider_request_intents_request_uq UNIQUE (request_id),
    CONSTRAINT model_provider_request_intents_sequence_uq UNIQUE (
        tenant_id, attempt_id, request_sequence
    ),
    CONSTRAINT model_provider_request_intents_run_fk FOREIGN KEY (
        tenant_id, attempt_id, run_id
    ) REFERENCES memoriesql.semantic_task_runs (
        tenant_id, attempt_id, run_id
    ),
    CONSTRAINT model_provider_request_intents_scope_shape CHECK (
        request_sequence > 0
        AND task_contract_revision > 0
        AND authorization_policy_revision > 0
        AND model_profile_revision > 0
        AND max_input_tokens > 0
        AND max_output_tokens > 0
        AND conservative_reservation_microunits >= 0
        AND (safety_ceiling_microunits IS NULL
            OR safety_ceiling_microunits >= 0)
    ),
    CONSTRAINT model_provider_request_intents_identifiers CHECK (
        run_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND (parent_run_id IS NULL OR
            parent_run_id ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$')
        AND module_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND operation_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND task_kind ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND model_profile_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND provider_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND request_boundary_revision ~
            '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND request_payload_hash ~ '^[a-f0-9]{64}$'
        AND length(model_id) BETWEEN 1 AND 255
    ),
    CONSTRAINT model_provider_request_intents_enums CHECK (
        run_role IN ('direct_leaf', 'conductor', 'delegate')
        AND billing_basis IN (
            'metered', 'subscription', 'local', 'discounted',
            'credited', 'indeterminate'
        )
        AND allowance_state IN (
            'covered', 'available', 'exhausted', 'unknown', 'not_applicable'
        )
        AND quality_tier IN ('frontier', 'qualified_lower')
        AND usage_provenance_expected IN (
            'provider_reported', 'framework_estimated', 'unavailable'
        )
        AND transport_retries_disabled
    ),
    CONSTRAINT model_provider_request_intents_billing_shape CHECK (
        (billing_basis = 'metered'
            AND pricing_revision_id IS NOT NULL
            AND conservative_reservation_microunits > 0)
        OR (billing_basis = 'metered'
            AND pricing_revision_id IS NULL
            AND safety_ceiling_microunits IS NULL
            AND conservative_reservation_microunits = 0)
        OR billing_basis <> 'metered'
    ),
    CONSTRAINT model_provider_request_intents_allowance_shape CHECK (
        (billing_basis = 'subscription'
            AND allowance_state <> 'not_applicable')
        OR (billing_basis <> 'subscription'
            AND allowance_state <> 'covered')
    )
);

CREATE TABLE memoriesql.model_usage_events (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    request_id uuid NOT NULL,
    dedupe_key text NOT NULL,
    event_kind text NOT NULL,
    outcome text NOT NULL,
    usage_state text NOT NULL,
    usage_provenance text NOT NULL,
    input_tokens bigint,
    output_tokens bigint,
    total_tokens bigint,
    cached_input_tokens bigint,
    cache_write_tokens bigint,
    reasoning_tokens bigint,
    tool_calls bigint,
    provider_reported_cost_microunits bigint,
    provider_reported_currency text,
    provider_request_id_hash text,
    diagnostic_code text,
    recorded_at timestamp with time zone NOT NULL,
    CONSTRAINT model_usage_events_pk PRIMARY KEY (tenant_id, event_id),
    CONSTRAINT model_usage_events_event_uq UNIQUE (event_id),
    CONSTRAINT model_usage_events_dedupe_uq UNIQUE (tenant_id, dedupe_key),
    CONSTRAINT model_usage_events_request_fk FOREIGN KEY (
        tenant_id, request_id
    ) REFERENCES memoriesql.model_provider_request_intents (
        tenant_id, request_id
    ),
    CONSTRAINT model_usage_events_enum_shape CHECK (
        event_kind IN (
            'usage_reported', 'usage_unavailable', 'request_failed',
            'request_cancelled', 'lease_lost_return'
        )
        AND outcome IN (
            'succeeded', 'failed', 'cancelled', 'lease_lost', 'unknown'
        )
        AND usage_state IN ('reported', 'unavailable')
        AND usage_provenance IN (
            'provider_reported', 'framework_estimated', 'unavailable'
        )
        AND (
            (event_kind IN ('usage_reported', 'usage_unavailable')
                AND outcome = 'succeeded')
            OR (event_kind = 'request_failed' AND outcome = 'failed')
            OR (event_kind = 'request_cancelled' AND outcome = 'cancelled')
            OR (event_kind = 'lease_lost_return' AND outcome = 'lease_lost')
        )
    ),
    CONSTRAINT model_usage_events_usage_atomic CHECK (
        ((usage_state = 'reported'
            AND input_tokens IS NOT NULL AND input_tokens >= 0
            AND output_tokens IS NOT NULL AND output_tokens >= 0
            AND total_tokens = input_tokens + output_tokens
            AND cached_input_tokens BETWEEN 0 AND input_tokens
            AND cache_write_tokens BETWEEN 0 AND input_tokens
            AND cached_input_tokens + cache_write_tokens <= input_tokens
            AND reasoning_tokens BETWEEN 0 AND output_tokens
            AND tool_calls >= 0)
        OR (usage_state = 'unavailable'
            AND input_tokens IS NULL AND output_tokens IS NULL
            AND total_tokens IS NULL AND cached_input_tokens IS NULL
            AND cache_write_tokens IS NULL
            AND reasoning_tokens IS NULL AND tool_calls IS NULL))
        AND (
        (usage_state = 'unavailable' AND usage_provenance = 'unavailable')
        OR (usage_state = 'reported' AND usage_provenance <>
            'unavailable')
        )
    ),
    CONSTRAINT model_usage_events_money_atomic CHECK (
        (provider_reported_cost_microunits IS NULL
            AND provider_reported_currency IS NULL)
        OR (provider_reported_cost_microunits >= 0
            AND provider_reported_currency = 'USD')
    ),
    CONSTRAINT model_usage_events_bounded_metadata CHECK (
        dedupe_key ~ '^[a-f0-9]{64}$'
        AND (provider_request_id_hash IS NULL OR
            provider_request_id_hash ~ '^[a-f0-9]{64}$')
        AND (diagnostic_code IS NULL OR (
            diagnostic_code ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
            AND length(diagnostic_code) <= 128
        ))
    )
);

CREATE TABLE memoriesql.model_bulk_usage_forecasts (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    forecast_id uuid NOT NULL,
    forecast_series_key text NOT NULL,
    forecast_hash text NOT NULL,
    source_scope_hash text NOT NULL,
    quality_policy_revision_id uuid NOT NULL REFERENCES
        memoriesql.model_quality_policy_revisions (
            quality_policy_revision_id
        ),
    model_profile_key text NOT NULL,
    model_profile_revision integer NOT NULL,
    billing_basis text NOT NULL,
    pricing_revision_id uuid REFERENCES memoriesql.model_pricing_revisions (
        pricing_revision_id
    ),
    pricing_observed_at timestamp with time zone NOT NULL,
    item_count bigint NOT NULL,
    low_shadow_cost_microunits bigint,
    expected_shadow_cost_microunits bigint,
    high_shadow_cost_microunits bigint,
    incremental_cash_exposure_microunits bigint,
    safety_ceiling_microunits bigint,
    expected_completion_low_ms bigint NOT NULL,
    expected_completion_high_ms bigint NOT NULL,
    allowance_state text NOT NULL,
    cost_basis text NOT NULL,
    queue_effect_hash text NOT NULL,
    assumptions_hash text NOT NULL,
    forecast_sequence bigint GENERATED ALWAYS AS IDENTITY,
    created_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT model_bulk_usage_forecasts_pk PRIMARY KEY (
        tenant_id, forecast_id
    ),
    CONSTRAINT model_bulk_usage_forecasts_id_uq UNIQUE (forecast_id),
    CONSTRAINT model_bulk_usage_forecasts_series_uq UNIQUE (
        tenant_id, forecast_series_key, forecast_sequence
    ),
    CONSTRAINT model_bulk_usage_forecasts_hashes CHECK (
        forecast_series_key ~ '^[a-f0-9]{64}$'
        AND forecast_hash ~ '^[a-f0-9]{64}$'
        AND source_scope_hash ~ '^[a-f0-9]{64}$'
        AND queue_effect_hash ~ '^[a-f0-9]{64}$'
        AND assumptions_hash ~ '^[a-f0-9]{64}$'
    ),
    CONSTRAINT model_bulk_usage_forecasts_values CHECK (
        item_count > 0
        AND model_profile_revision > 0
        AND billing_basis IN (
            'metered', 'subscription', 'local', 'discounted',
            'credited', 'indeterminate'
        )
        AND (
            (low_shadow_cost_microunits IS NULL
                AND expected_shadow_cost_microunits IS NULL
                AND high_shadow_cost_microunits IS NULL)
            OR (low_shadow_cost_microunits >= 0
                AND low_shadow_cost_microunits <=
                    expected_shadow_cost_microunits
                AND expected_shadow_cost_microunits <=
                    high_shadow_cost_microunits)
        )
        AND (incremental_cash_exposure_microunits IS NULL
            OR incremental_cash_exposure_microunits >= 0)
        AND (safety_ceiling_microunits IS NULL
            OR safety_ceiling_microunits >= 0)
        AND expected_completion_low_ms >= 0
        AND expected_completion_high_ms >= expected_completion_low_ms
        AND allowance_state IN (
            'covered', 'available', 'exhausted', 'unknown', 'not_applicable'
        )
        AND cost_basis IN (
            'provider_reported', 'catalog_estimate',
            'subscription_covered', 'local', 'unknown'
        )
        AND (
            (cost_basis = 'catalog_estimate'
                AND billing_basis = 'metered'
                AND pricing_revision_id IS NOT NULL
                AND incremental_cash_exposure_microunits IS NOT NULL)
            OR (cost_basis = 'subscription_covered'
                AND billing_basis = 'subscription'
                AND allowance_state = 'covered'
                AND incremental_cash_exposure_microunits = 0)
            OR (cost_basis = 'local'
                AND billing_basis = 'local'
                AND incremental_cash_exposure_microunits IS NULL)
            OR (cost_basis = 'provider_reported'
                AND incremental_cash_exposure_microunits IS NOT NULL)
            OR (cost_basis = 'unknown'
                AND incremental_cash_exposure_microunits IS NULL)
        )
    )
);

CREATE TABLE memoriesql.model_bulk_forecast_approvals (
    tenant_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    access_scope_id uuid NOT NULL,
    approval_id uuid NOT NULL,
    forecast_id uuid NOT NULL,
    forecast_hash text NOT NULL,
    source_scope_hash text NOT NULL,
    assumptions_hash text NOT NULL,
    quality_policy_revision_id uuid NOT NULL,
    model_profile_key text NOT NULL,
    model_profile_revision integer NOT NULL,
    safety_ceiling_microunits bigint,
    approved_by_principal_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    approved_at timestamp with time zone NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT model_bulk_forecast_approvals_pk PRIMARY KEY (
        tenant_id, approval_id
    ),
    CONSTRAINT model_bulk_forecast_approvals_idempotency_uq UNIQUE (
        tenant_id, idempotency_key
    ),
    CONSTRAINT model_bulk_forecast_approvals_forecast_fk FOREIGN KEY (
        tenant_id, forecast_id
    ) REFERENCES memoriesql.model_bulk_usage_forecasts (
        tenant_id, forecast_id
    ),
    CONSTRAINT model_bulk_forecast_approvals_hashes CHECK (
        forecast_hash ~ '^[a-f0-9]{64}$'
        AND source_scope_hash ~ '^[a-f0-9]{64}$'
        AND assumptions_hash ~ '^[a-f0-9]{64}$'
        AND idempotency_key ~ '^[a-f0-9]{64}$'
        AND model_profile_key ~
            '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'
        AND model_profile_revision > 0
        AND (safety_ceiling_microunits IS NULL
            OR safety_ceiling_microunits >= 0)
    )
);

CREATE FUNCTION memoriesql.reject_model_accounting_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    RAISE EXCEPTION 'model accounting facts and revisions are immutable';
END;
$$;

CREATE FUNCTION memoriesql.reject_overlapping_model_pricing_revision()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, memoriesql
AS $$
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            NEW.provider_key || ':' || NEW.model_id || ':' || NEW.currency,
            0
        )
    );
    IF EXISTS (
        SELECT 1
        FROM memoriesql.model_pricing_revisions AS prior
        WHERE prior.provider_key = NEW.provider_key
          AND prior.model_id = NEW.model_id
          AND prior.currency = NEW.currency
          AND tstzrange(
              prior.effective_from, prior.effective_until, '[)'
          ) && tstzrange(NEW.effective_from, NEW.effective_until, '[)')
    ) THEN
        RAISE EXCEPTION 'overlapping model pricing revision';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER model_pricing_revisions_no_overlap
BEFORE INSERT ON memoriesql.model_pricing_revisions
FOR EACH ROW EXECUTE FUNCTION
    memoriesql.reject_overlapping_model_pricing_revision();

CREATE TRIGGER model_pricing_revisions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.model_pricing_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_model_accounting_mutation();
CREATE TRIGGER model_quality_policy_revisions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.model_quality_policy_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_model_accounting_mutation();
CREATE TRIGGER model_task_profile_qualifications_immutable
BEFORE UPDATE OR DELETE ON memoriesql.model_task_profile_qualifications
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_model_accounting_mutation();
CREATE TRIGGER model_optimization_target_revisions_immutable
BEFORE UPDATE OR DELETE ON memoriesql.model_optimization_target_revisions
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_model_accounting_mutation();
CREATE TRIGGER model_provider_request_intents_immutable
BEFORE UPDATE OR DELETE ON memoriesql.model_provider_request_intents
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_model_accounting_mutation();
CREATE TRIGGER model_usage_events_immutable
BEFORE UPDATE OR DELETE ON memoriesql.model_usage_events
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_model_accounting_mutation();
CREATE TRIGGER model_bulk_usage_forecasts_immutable
BEFORE UPDATE OR DELETE ON memoriesql.model_bulk_usage_forecasts
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_model_accounting_mutation();
CREATE TRIGGER model_bulk_forecast_approvals_immutable
BEFORE UPDATE OR DELETE ON memoriesql.model_bulk_forecast_approvals
FOR EACH ROW EXECUTE FUNCTION memoriesql.reject_model_accounting_mutation();

CREATE FUNCTION memoriesql.catalog_model_cost_microunits(
    requested_input_tokens bigint,
    requested_cached_input_tokens bigint,
    requested_cache_write_tokens bigint,
    requested_output_tokens bigint,
    input_rate bigint,
    cached_input_rate bigint,
    cache_write_rate bigint,
    output_rate bigint
)
RETURNS bigint
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, memoriesql
AS $$
    SELECT (
        (
            ceil((requested_input_tokens - requested_cached_input_tokens
                - requested_cache_write_tokens)::numeric
                * input_rate::numeric / 1000000::numeric)
        )::bigint
        + (
            ceil(requested_cached_input_tokens::numeric
                * cached_input_rate::numeric / 1000000::numeric)
        )::bigint
        + (
            ceil(requested_cache_write_tokens::numeric
                * cache_write_rate::numeric / 1000000::numeric)
        )::bigint
        + (
            ceil(requested_output_tokens::numeric
                * output_rate::numeric / 1000000::numeric)
        )::bigint
    )
$$;

CREATE FUNCTION memoriesql.reauthorize_semantic_task_for_watch(
    requested_tenant_id uuid,
    requested_task_id uuid,
    requested_attempt_id uuid,
    requested_lease_generation bigint,
    requested_worker_id text,
    requested_worker_instance_id text,
    requested_at timestamp with time zone
)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    authorization_result text;
    cancellation_requested boolean := false;
BEGIN
    authorization_result := memoriesql.reauthorize_semantic_task(
        requested_tenant_id, requested_task_id, requested_attempt_id,
        requested_lease_generation, requested_worker_id,
        requested_worker_instance_id, 'hydrate', requested_at
    );
    IF authorization_result <> 'stale_fence' THEN
        RETURN authorization_result;
    END IF;

    SELECT task.cancel_requested_at IS NOT NULL INTO cancellation_requested
    FROM memoriesql.current_authorization_context() AS context
    JOIN memoriesql.semantic_tasks AS task
      ON task.tenant_id = context.tenant_id
     AND task.workspace_id = context.workspace_id
     AND task.tenant_id = requested_tenant_id
     AND task.task_id = requested_task_id
     AND task.lease_generation = requested_lease_generation
    JOIN memoriesql.semantic_task_attempts AS attempt
      ON attempt.tenant_id = task.tenant_id
     AND attempt.task_id = task.task_id
     AND attempt.attempt_id = requested_attempt_id
     AND attempt.lease_generation = requested_lease_generation
     AND attempt.claimant_principal_id = context.principal_id
     AND attempt.worker_id = requested_worker_id
     AND attempt.worker_instance_id = requested_worker_instance_id
    WHERE context.principal_kind = 'service'
      AND context.expires_at > pg_catalog.clock_timestamp();
    IF COALESCE(cancellation_requested, false) THEN
        RETURN 'cancel_requested';
    END IF;
    RETURN authorization_result;
END;
$$;

CREATE FUNCTION memoriesql.record_model_provider_request_intent(
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
                (requested_intent->'target'->>'transport_retries_disabled')::boolean,
            'usage_provenance_expected',
                requested_intent->'target'->>'usage_provenance',
            'safety_ceiling_microunits', requested_ceiling,
            'conservative_reservation_microunits', requested_reservation,
            'max_input_tokens',
                (requested_intent->>'max_input_tokens')::bigint,
            'max_output_tokens',
                (requested_intent->>'max_output_tokens')::bigint,
            'request_payload_hash', requested_intent->>'request_payload_hash'
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
    IF requested_intent->'target'->>'transport_retries_disabled' <> 'true' THEN
        RAISE EXCEPTION 'provider transport request boundary is not visible';
    END IF;
    IF requested_intent->'target'->>'billing_basis' = 'subscription'
       AND requested_intent->'target'->>'allowance_state' IN (
            'exhausted', 'unknown'
       ) THEN
        RAISE EXCEPTION
            'accounting.required_model_allowance_unavailable';
    END IF;
    IF (requested_intent->>'max_input_tokens')::bigint <>
            (requested_intent->'target'->>'max_input_tokens_per_request')::bigint
       OR (requested_intent->>'max_output_tokens')::bigint >
            (requested_intent->'target'->>'max_output_tokens_per_request')::bigint
       OR (requested_intent->>'max_output_tokens')::bigint <= 0 THEN
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

    IF requested_intent->'target'->>'billing_basis' = 'metered'
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
        max_output_tokens, request_payload_hash, recorded_at
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
        (requested_intent->'target'->>'transport_retries_disabled')::boolean,
        requested_intent->'target'->>'usage_provenance',
        requested_ceiling, requested_reservation,
        (requested_intent->>'max_input_tokens')::bigint,
        (requested_intent->>'max_output_tokens')::bigint,
        requested_intent->>'request_payload_hash', database_now
    );
    RETURN request_uuid;
END;
$$;

CREATE FUNCTION memoriesql.append_model_usage_event(
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
       OR octet_length(requested_event::text) > 8192 THEN
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
                NULLIF(requested_event->>'diagnostic_code', '')
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
        diagnostic_code, recorded_at
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
        NULLIF(requested_event->>'diagnostic_code', ''), database_now
    );
    RETURN event_uuid;
END;
$$;

CREATE FUNCTION memoriesql.record_bulk_usage_forecast(
    requested_forecast jsonb
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record record;
    forecast_uuid uuid;
    scope_uuid uuid;
    prior_forecast memoriesql.model_bulk_usage_forecasts%ROWTYPE;
    candidate_forecast memoriesql.model_bulk_usage_forecasts%ROWTYPE;
BEGIN
    IF jsonb_typeof(requested_forecast) <> 'object'
       OR octet_length(requested_forecast::text) > 8192 THEN
        RAISE EXCEPTION 'invalid bulk usage forecast';
    END IF;
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    forecast_uuid := (requested_forecast->>'forecast_id')::uuid;
    scope_uuid := (requested_forecast->>'access_scope_id')::uuid;
    IF context_record.tenant_id <>
            (requested_forecast->>'tenant_id')::uuid
       OR context_record.workspace_id <>
            (requested_forecast->>'workspace_id')::uuid
       OR NOT memoriesql.current_context_scope_authorized(
            scope_uuid, 'memory.maintain', 'write'
       ) THEN
        RAISE EXCEPTION 'bulk usage forecast is outside authorization';
    END IF;
    SELECT * INTO candidate_forecast
    FROM jsonb_populate_record(
        NULL::memoriesql.model_bulk_usage_forecasts,
        jsonb_build_object(
            'tenant_id', context_record.tenant_id,
            'workspace_id', context_record.workspace_id,
            'access_scope_id', scope_uuid,
            'forecast_id', forecast_uuid,
            'forecast_series_key',
                requested_forecast->>'forecast_series_key',
            'forecast_hash', requested_forecast->>'forecast_hash',
            'source_scope_hash', requested_forecast->>'source_scope_hash',
            'quality_policy_revision_id',
                (requested_forecast->>'quality_policy_revision_id')::uuid,
            'model_profile_key', requested_forecast->>'model_profile_key',
            'model_profile_revision',
                (requested_forecast->>'model_profile_revision')::integer,
            'billing_basis', requested_forecast->>'billing_basis',
            'pricing_revision_id', NULLIF(
                requested_forecast->>'pricing_revision_id', ''
            )::uuid,
            'pricing_observed_at', to_timestamp(
                (requested_forecast->>'pricing_observed_at_epoch_us')::numeric
                / 1000000::numeric
            ),
            'item_count', (requested_forecast->>'item_count')::bigint,
            'low_shadow_cost_microunits', NULLIF(
                requested_forecast->>'low_shadow_cost_microunits', ''
            )::bigint,
            'expected_shadow_cost_microunits', NULLIF(
                requested_forecast->>'expected_shadow_cost_microunits', ''
            )::bigint,
            'high_shadow_cost_microunits', NULLIF(
                requested_forecast->>'high_shadow_cost_microunits', ''
            )::bigint,
            'incremental_cash_exposure_microunits', NULLIF(
                requested_forecast->>'incremental_cash_exposure_microunits', ''
            )::bigint,
            'safety_ceiling_microunits', NULLIF(
                requested_forecast->>'safety_ceiling_microunits', ''
            )::bigint,
            'expected_completion_low_ms',
                (requested_forecast->>'expected_completion_low_ms')::bigint,
            'expected_completion_high_ms',
                (requested_forecast->>'expected_completion_high_ms')::bigint,
            'allowance_state', requested_forecast->>'allowance_state',
            'cost_basis', requested_forecast->>'cost_basis',
            'queue_effect_hash', requested_forecast->>'queue_effect_hash',
            'assumptions_hash', requested_forecast->>'assumptions_hash'
        )
    );
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'bulk-forecast:' || forecast_uuid::text, 0
        )
    );
    SELECT * INTO prior_forecast
    FROM memoriesql.model_bulk_usage_forecasts AS prior
    WHERE prior.forecast_id = forecast_uuid;
    IF FOUND THEN
        candidate_forecast.forecast_sequence :=
            prior_forecast.forecast_sequence;
        candidate_forecast.created_at := prior_forecast.created_at;
        IF prior_forecast IS DISTINCT FROM candidate_forecast THEN
            RAISE EXCEPTION 'bulk forecast idempotency collision';
        END IF;
        RETURN prior_forecast.forecast_id;
    END IF;
    INSERT INTO memoriesql.model_bulk_usage_forecasts (
        tenant_id, workspace_id, access_scope_id, forecast_id,
        forecast_series_key, forecast_hash, source_scope_hash,
        quality_policy_revision_id, model_profile_key,
        model_profile_revision, billing_basis, pricing_revision_id,
        pricing_observed_at, item_count, low_shadow_cost_microunits,
        expected_shadow_cost_microunits, high_shadow_cost_microunits,
        incremental_cash_exposure_microunits, safety_ceiling_microunits,
        expected_completion_low_ms, expected_completion_high_ms,
        allowance_state,
        cost_basis, queue_effect_hash, assumptions_hash
    ) VALUES (
        context_record.tenant_id, context_record.workspace_id, scope_uuid,
        forecast_uuid, requested_forecast->>'forecast_series_key',
        requested_forecast->>'forecast_hash',
        requested_forecast->>'source_scope_hash',
        (requested_forecast->>'quality_policy_revision_id')::uuid,
        requested_forecast->>'model_profile_key',
        (requested_forecast->>'model_profile_revision')::integer,
        requested_forecast->>'billing_basis',
        NULLIF(requested_forecast->>'pricing_revision_id', '')::uuid,
        to_timestamp(
            (requested_forecast->>'pricing_observed_at_epoch_us')::numeric
            / 1000000::numeric
        ),
        (requested_forecast->>'item_count')::bigint,
        NULLIF(requested_forecast->>'low_shadow_cost_microunits', '')::bigint,
        NULLIF(
            requested_forecast->>'expected_shadow_cost_microunits', ''
        )::bigint,
        NULLIF(requested_forecast->>'high_shadow_cost_microunits', '')::bigint,
        NULLIF(
            requested_forecast->>'incremental_cash_exposure_microunits', ''
        )::bigint,
        NULLIF(
            requested_forecast->>'safety_ceiling_microunits', ''
        )::bigint,
        (requested_forecast->>'expected_completion_low_ms')::bigint,
        (requested_forecast->>'expected_completion_high_ms')::bigint,
        requested_forecast->>'allowance_state',
        requested_forecast->>'cost_basis',
        requested_forecast->>'queue_effect_hash',
        requested_forecast->>'assumptions_hash'
    );
    RETURN forecast_uuid;
END;
$$;

CREATE FUNCTION memoriesql.approve_bulk_usage_forecast(
    requested_approval jsonb
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memoriesql
AS $$
DECLARE
    context_record record;
    forecast_record memoriesql.model_bulk_usage_forecasts%ROWTYPE;
    approval_uuid uuid;
    prior_approval memoriesql.model_bulk_forecast_approvals%ROWTYPE;
BEGIN
    IF jsonb_typeof(requested_approval) <> 'object'
       OR octet_length(requested_approval::text) > 8192 THEN
        RAISE EXCEPTION 'invalid bulk forecast approval';
    END IF;
    SELECT * INTO context_record
    FROM memoriesql.current_authorization_context();
    approval_uuid := (requested_approval->>'approval_id')::uuid;
    SELECT * INTO forecast_record
    FROM memoriesql.model_bulk_usage_forecasts AS forecast
    WHERE forecast.tenant_id = context_record.tenant_id
      AND forecast.forecast_id =
          (requested_approval->>'forecast_id')::uuid
    FOR SHARE;
    IF NOT FOUND
       OR context_record.principal_id <>
            (requested_approval->>'approved_by_principal_id')::uuid
       OR forecast_record.forecast_hash <>
            requested_approval->>'forecast_hash'
       OR forecast_record.source_scope_hash <>
            requested_approval->>'source_scope_hash'
       OR forecast_record.assumptions_hash <>
            requested_approval->>'assumptions_hash'
       OR forecast_record.quality_policy_revision_id <>
            (requested_approval->>'quality_policy_revision_id')::uuid
       OR forecast_record.model_profile_key <>
            requested_approval->>'model_profile_key'
       OR forecast_record.model_profile_revision <>
            (requested_approval->>'model_profile_revision')::integer
       OR forecast_record.safety_ceiling_microunits IS DISTINCT FROM
            NULLIF(
                requested_approval->>'safety_ceiling_microunits', ''
            )::bigint
       OR context_record.workspace_id <> forecast_record.workspace_id
       OR NOT memoriesql.current_context_scope_authorized(
            forecast_record.access_scope_id, 'memory.maintain', 'write'
       )
       OR EXISTS (
            SELECT 1
            FROM memoriesql.model_bulk_usage_forecasts AS newer
            WHERE newer.tenant_id = forecast_record.tenant_id
              AND newer.forecast_series_key =
                  forecast_record.forecast_series_key
              AND newer.forecast_sequence > forecast_record.forecast_sequence
       ) THEN
        RAISE EXCEPTION 'bulk forecast approval is stale or mismatched';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            forecast_record.tenant_id::text || ':forecast-approval:' ||
            (requested_approval->>'idempotency_key'), 0
        )
    );
    SELECT * INTO prior_approval
    FROM memoriesql.model_bulk_forecast_approvals AS prior
    WHERE prior.tenant_id = forecast_record.tenant_id
      AND prior.idempotency_key = requested_approval->>'idempotency_key';
    IF FOUND THEN
        IF prior_approval.approval_id <> approval_uuid
           OR prior_approval.forecast_id <> forecast_record.forecast_id
           OR prior_approval.forecast_hash <>
                requested_approval->>'forecast_hash'
           OR prior_approval.source_scope_hash <>
                requested_approval->>'source_scope_hash'
           OR prior_approval.assumptions_hash <>
                requested_approval->>'assumptions_hash'
           OR prior_approval.quality_policy_revision_id <>
                forecast_record.quality_policy_revision_id
           OR prior_approval.model_profile_key <>
                forecast_record.model_profile_key
           OR prior_approval.model_profile_revision <>
                forecast_record.model_profile_revision
           OR prior_approval.safety_ceiling_microunits IS DISTINCT FROM
                forecast_record.safety_ceiling_microunits
           OR prior_approval.approved_by_principal_id <>
                context_record.principal_id THEN
            RAISE EXCEPTION 'bulk forecast approval idempotency collision';
        END IF;
        RETURN prior_approval.approval_id;
    END IF;
    INSERT INTO memoriesql.model_bulk_forecast_approvals (
        tenant_id, workspace_id, access_scope_id, approval_id,
        forecast_id, forecast_hash, source_scope_hash, assumptions_hash,
        quality_policy_revision_id, model_profile_key,
        model_profile_revision, safety_ceiling_microunits,
        approved_by_principal_id,
        idempotency_key
    ) VALUES (
        forecast_record.tenant_id, forecast_record.workspace_id,
        forecast_record.access_scope_id, approval_uuid,
        forecast_record.forecast_id, requested_approval->>'forecast_hash',
        requested_approval->>'source_scope_hash',
        requested_approval->>'assumptions_hash',
        forecast_record.quality_policy_revision_id,
        forecast_record.model_profile_key,
        forecast_record.model_profile_revision,
        forecast_record.safety_ceiling_microunits,
        context_record.principal_id,
        requested_approval->>'idempotency_key'
    );
    RETURN approval_uuid;
END;
$$;

CREATE VIEW memoriesql.model_request_usage_fold
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
) AS event_ids ON true;

CREATE VIEW memoriesql.model_run_usage_rollup
WITH (security_invoker = true)
AS
SELECT tenant_id, workspace_id, access_scope_id, task_id, attempt_id, run_id,
    count(*) AS request_count,
    count(*) FILTER (WHERE usage_state = 'reported') AS reported_request_count,
    count(*) FILTER (WHERE usage_state = 'uncertain') AS uncertain_request_count,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens,
    sum(tool_calls) AS tool_calls,
    sum(shadow_catalog_cost_microunits) AS shadow_catalog_cost_microunits,
    sum(incremental_cash_exposure_microunits)
        AS incremental_cash_exposure_microunits,
    count(incremental_cash_exposure_microunits)
        AS known_incremental_cash_request_count,
    array_agg(request_id ORDER BY request_recorded_at, request_id)
        AS contributing_request_ids,
    jsonb_object_agg(
        request_id::text,
        to_jsonb(contributing_event_ids)
        ORDER BY request_recorded_at, request_id
    ) AS contributing_event_ids_by_request,
    1 AS fold_version
FROM memoriesql.model_request_usage_fold
GROUP BY tenant_id, workspace_id, access_scope_id, task_id, attempt_id, run_id;

CREATE VIEW memoriesql.model_task_attempt_usage_rollup
WITH (security_invoker = true)
AS
SELECT tenant_id, workspace_id, access_scope_id, task_id, attempt_id,
    count(*) AS request_count,
    count(*) FILTER (WHERE usage_state = 'reported') AS reported_request_count,
    count(*) FILTER (WHERE usage_state = 'uncertain') AS uncertain_request_count,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens,
    sum(tool_calls) AS tool_calls,
    sum(shadow_catalog_cost_microunits) AS shadow_catalog_cost_microunits,
    sum(incremental_cash_exposure_microunits)
        AS incremental_cash_exposure_microunits,
    count(incremental_cash_exposure_microunits)
        AS known_incremental_cash_request_count,
    array_agg(request_id ORDER BY request_recorded_at, request_id)
        AS contributing_request_ids,
    jsonb_object_agg(
        request_id::text,
        to_jsonb(contributing_event_ids)
        ORDER BY request_recorded_at, request_id
    ) AS contributing_event_ids_by_request,
    1 AS fold_version
FROM memoriesql.model_request_usage_fold
GROUP BY tenant_id, workspace_id, access_scope_id, task_id, attempt_id;

CREATE VIEW memoriesql.model_task_usage_rollup
WITH (security_invoker = true)
AS
SELECT tenant_id, workspace_id, access_scope_id, task_id,
    count(*) AS request_count,
    count(*) FILTER (WHERE usage_state = 'reported') AS reported_request_count,
    count(*) FILTER (WHERE usage_state = 'uncertain') AS uncertain_request_count,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens,
    sum(tool_calls) AS tool_calls,
    sum(shadow_catalog_cost_microunits) AS shadow_catalog_cost_microunits,
    sum(incremental_cash_exposure_microunits)
        AS incremental_cash_exposure_microunits,
    count(incremental_cash_exposure_microunits)
        AS known_incremental_cash_request_count,
    array_agg(request_id ORDER BY request_recorded_at, request_id)
        AS contributing_request_ids,
    jsonb_object_agg(
        request_id::text,
        to_jsonb(contributing_event_ids)
        ORDER BY request_recorded_at, request_id
    ) AS contributing_event_ids_by_request,
    1 AS fold_version
FROM memoriesql.model_request_usage_fold
GROUP BY tenant_id, workspace_id, access_scope_id, task_id;

CREATE VIEW memoriesql.model_module_daily_usage_rollup
WITH (security_invoker = true)
AS
SELECT tenant_id, workspace_id, access_scope_id, module_key,
    request_recorded_at::date AS usage_day,
    count(*) AS request_count,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens,
    sum(shadow_catalog_cost_microunits) AS shadow_catalog_cost_microunits,
    sum(incremental_cash_exposure_microunits)
        AS incremental_cash_exposure_microunits,
    count(*) FILTER (
        WHERE incremental_cash_exposure_microunits IS NULL
    ) AS unknown_incremental_cash_request_count,
    array_agg(request_id ORDER BY request_recorded_at, request_id)
        AS contributing_request_ids,
    jsonb_object_agg(
        request_id::text,
        to_jsonb(contributing_event_ids)
        ORDER BY request_recorded_at, request_id
    ) AS contributing_event_ids_by_request,
    1 AS fold_version
FROM memoriesql.model_request_usage_fold
GROUP BY tenant_id, workspace_id, access_scope_id, module_key,
    request_recorded_at::date;

CREATE VIEW memoriesql.model_operation_daily_usage_rollup
WITH (security_invoker = true)
AS
SELECT tenant_id, workspace_id, access_scope_id, module_key, operation_key,
    request_recorded_at::date AS usage_day,
    count(*) AS request_count,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens,
    sum(shadow_catalog_cost_microunits) AS shadow_catalog_cost_microunits,
    sum(incremental_cash_exposure_microunits)
        AS incremental_cash_exposure_microunits,
    count(*) FILTER (
        WHERE incremental_cash_exposure_microunits IS NULL
    ) AS unknown_incremental_cash_request_count,
    array_agg(request_id ORDER BY request_recorded_at, request_id)
        AS contributing_request_ids,
    jsonb_object_agg(
        request_id::text,
        to_jsonb(contributing_event_ids)
        ORDER BY request_recorded_at, request_id
    ) AS contributing_event_ids_by_request,
    1 AS fold_version
FROM memoriesql.model_request_usage_fold
GROUP BY tenant_id, workspace_id, access_scope_id, module_key, operation_key,
    request_recorded_at::date;

CREATE VIEW memoriesql.model_workspace_daily_usage_rollup
WITH (security_invoker = true)
AS
SELECT tenant_id, workspace_id, request_recorded_at::date AS usage_day,
    count(*) AS request_count,
    sum(input_tokens) AS input_tokens,
    sum(output_tokens) AS output_tokens,
    sum(total_tokens) AS total_tokens,
    sum(shadow_catalog_cost_microunits) AS shadow_catalog_cost_microunits,
    sum(incremental_cash_exposure_microunits)
        AS incremental_cash_exposure_microunits,
    count(*) FILTER (
        WHERE incremental_cash_exposure_microunits IS NULL
    ) AS unknown_incremental_cash_request_count,
    array_agg(request_id ORDER BY request_recorded_at, request_id)
        AS contributing_request_ids,
    jsonb_object_agg(
        request_id::text,
        to_jsonb(contributing_event_ids)
        ORDER BY request_recorded_at, request_id
    ) AS contributing_event_ids_by_request,
    1 AS fold_version
FROM memoriesql.model_request_usage_fold
GROUP BY tenant_id, workspace_id, request_recorded_at::date;

CREATE VIEW memoriesql.model_usage_fold_reconciliation
WITH (security_invoker = true)
AS
SELECT
    COALESCE(requests.request_count, 0) AS request_count,
    COALESCE(runs.request_count, 0) AS run_rollup_request_count,
    COALESCE(requests.total_tokens, 0) AS request_total_tokens,
    COALESCE(runs.total_tokens, 0) AS run_rollup_total_tokens,
    COALESCE(requests.shadow_cost, 0) AS request_shadow_cost_microunits,
    COALESCE(runs.shadow_cost, 0) AS run_rollup_shadow_cost_microunits,
    COALESCE(attempts.request_count, 0) AS attempt_rollup_request_count,
    COALESCE(attempts.total_tokens, 0) AS attempt_rollup_total_tokens,
    COALESCE(attempts.shadow_cost, 0) AS attempt_rollup_shadow_cost_microunits,
    COALESCE(tasks.request_count, 0) AS task_rollup_request_count,
    COALESCE(tasks.total_tokens, 0) AS task_rollup_total_tokens,
    COALESCE(tasks.shadow_cost, 0) AS task_rollup_shadow_cost_microunits,
    COALESCE(modules.request_count, 0) AS module_rollup_request_count,
    COALESCE(modules.total_tokens, 0) AS module_rollup_total_tokens,
    COALESCE(operations.request_count, 0) AS operation_rollup_request_count,
    COALESCE(operations.total_tokens, 0) AS operation_rollup_total_tokens,
    COALESCE(workspaces.request_count, 0) AS workspace_rollup_request_count,
    COALESCE(workspaces.total_tokens, 0) AS workspace_rollup_total_tokens,
    COALESCE(requests.request_count, 0) = COALESCE(runs.request_count, 0)
      AND COALESCE(requests.total_tokens, 0) = COALESCE(runs.total_tokens, 0)
      AND COALESCE(requests.shadow_cost, 0) = COALESCE(runs.shadow_cost, 0)
      AND COALESCE(requests.request_count, 0) =
          COALESCE(attempts.request_count, 0)
      AND COALESCE(requests.total_tokens, 0) =
          COALESCE(attempts.total_tokens, 0)
      AND COALESCE(requests.shadow_cost, 0) =
          COALESCE(attempts.shadow_cost, 0)
      AND COALESCE(requests.request_count, 0) =
          COALESCE(tasks.request_count, 0)
      AND COALESCE(requests.total_tokens, 0) = COALESCE(tasks.total_tokens, 0)
      AND COALESCE(requests.shadow_cost, 0) = COALESCE(tasks.shadow_cost, 0)
      AND COALESCE(requests.request_count, 0) =
          COALESCE(modules.request_count, 0)
      AND COALESCE(requests.total_tokens, 0) =
          COALESCE(modules.total_tokens, 0)
      AND COALESCE(requests.shadow_cost, 0) =
          COALESCE(modules.shadow_cost, 0)
      AND COALESCE(requests.request_count, 0) =
          COALESCE(operations.request_count, 0)
      AND COALESCE(requests.total_tokens, 0) =
          COALESCE(operations.total_tokens, 0)
      AND COALESCE(requests.shadow_cost, 0) =
          COALESCE(operations.shadow_cost, 0)
      AND COALESCE(requests.request_count, 0) =
          COALESCE(workspaces.request_count, 0)
      AND COALESCE(requests.total_tokens, 0) =
          COALESCE(workspaces.total_tokens, 0)
      AND COALESCE(requests.shadow_cost, 0) =
          COALESCE(workspaces.shadow_cost, 0)
        AS parity_ok,
    1 AS fold_version
FROM (
    SELECT count(*) AS request_count, sum(total_tokens) AS total_tokens,
        sum(shadow_catalog_cost_microunits) AS shadow_cost
    FROM memoriesql.model_request_usage_fold
) AS requests
CROSS JOIN (
    SELECT sum(request_count) AS request_count,
        sum(total_tokens) AS total_tokens,
        sum(shadow_catalog_cost_microunits) AS shadow_cost
    FROM memoriesql.model_run_usage_rollup
) AS runs
CROSS JOIN (
    SELECT sum(request_count) AS request_count,
        sum(total_tokens) AS total_tokens,
        sum(shadow_catalog_cost_microunits) AS shadow_cost
    FROM memoriesql.model_task_attempt_usage_rollup
) AS attempts
CROSS JOIN (
    SELECT sum(request_count) AS request_count,
        sum(total_tokens) AS total_tokens,
        sum(shadow_catalog_cost_microunits) AS shadow_cost
    FROM memoriesql.model_task_usage_rollup
) AS tasks
CROSS JOIN (
    SELECT sum(request_count) AS request_count,
        sum(total_tokens) AS total_tokens,
        sum(shadow_catalog_cost_microunits) AS shadow_cost
    FROM memoriesql.model_module_daily_usage_rollup
) AS modules
CROSS JOIN (
    SELECT sum(request_count) AS request_count,
        sum(total_tokens) AS total_tokens,
        sum(shadow_catalog_cost_microunits) AS shadow_cost
    FROM memoriesql.model_operation_daily_usage_rollup
) AS operations
CROSS JOIN (
    SELECT sum(request_count) AS request_count,
        sum(total_tokens) AS total_tokens,
        sum(shadow_catalog_cost_microunits) AS shadow_cost
    FROM memoriesql.model_workspace_daily_usage_rollup
) AS workspaces;

CREATE VIEW memoriesql.model_bulk_forecast_approval_status
WITH (security_invoker = true)
AS
SELECT approval.*, forecast.forecast_series_key,
    NOT EXISTS (
        SELECT 1
        FROM memoriesql.model_bulk_usage_forecasts AS newer
        WHERE newer.tenant_id = forecast.tenant_id
          AND newer.forecast_series_key = forecast.forecast_series_key
          AND newer.forecast_sequence > forecast.forecast_sequence
    ) AND approval.forecast_hash = forecast.forecast_hash
      AND approval.source_scope_hash = forecast.source_scope_hash
      AND approval.assumptions_hash = forecast.assumptions_hash
      AND approval.quality_policy_revision_id =
          forecast.quality_policy_revision_id
      AND approval.model_profile_key = forecast.model_profile_key
      AND approval.model_profile_revision = forecast.model_profile_revision
      AND approval.safety_ceiling_microunits IS NOT DISTINCT FROM
          forecast.safety_ceiling_microunits AS approval_valid
FROM memoriesql.model_bulk_forecast_approvals AS approval
JOIN memoriesql.model_bulk_usage_forecasts AS forecast
  ON forecast.tenant_id = approval.tenant_id
 AND forecast.forecast_id = approval.forecast_id;

CREATE POLICY model_provider_request_intents_read
ON memoriesql.model_provider_request_intents
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1 FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = model_provider_request_intents.tenant_id
          AND context.workspace_id = model_provider_request_intents.workspace_id
          AND memoriesql.current_context_scope_authorized(
              model_provider_request_intents.access_scope_id,
              'memory.maintain', 'read'
          )
    )
);
CREATE POLICY model_usage_events_read
ON memoriesql.model_usage_events
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1 FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = model_usage_events.tenant_id
          AND context.workspace_id = model_usage_events.workspace_id
          AND memoriesql.current_context_scope_authorized(
              model_usage_events.access_scope_id, 'memory.maintain', 'read'
          )
    )
);
CREATE POLICY model_bulk_usage_forecasts_read
ON memoriesql.model_bulk_usage_forecasts
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1 FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = model_bulk_usage_forecasts.tenant_id
          AND context.workspace_id = model_bulk_usage_forecasts.workspace_id
          AND memoriesql.current_context_scope_authorized(
              model_bulk_usage_forecasts.access_scope_id,
              'memory.maintain', 'read'
          )
    )
);
CREATE POLICY model_bulk_forecast_approvals_read
ON memoriesql.model_bulk_forecast_approvals
FOR SELECT TO memoriesql_application, memoriesql_worker
USING (
    EXISTS (
        SELECT 1 FROM memoriesql.current_authorization_context() AS context
        WHERE context.tenant_id = model_bulk_forecast_approvals.tenant_id
          AND context.workspace_id = model_bulk_forecast_approvals.workspace_id
          AND memoriesql.current_context_scope_authorized(
              model_bulk_forecast_approvals.access_scope_id,
              'memory.maintain', 'read'
          )
    )
);

ALTER TABLE memoriesql.model_provider_request_intents
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.model_provider_request_intents FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.model_usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.model_usage_events FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.model_bulk_usage_forecasts ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.model_bulk_usage_forecasts FORCE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.model_bulk_forecast_approvals
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE memoriesql.model_bulk_forecast_approvals FORCE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA memoriesql FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA memoriesql FROM PUBLIC;

GRANT SELECT ON memoriesql.model_pricing_revisions,
    memoriesql.model_quality_policy_revisions,
    memoriesql.model_task_profile_qualifications,
    memoriesql.model_optimization_target_revisions,
    memoriesql.model_provider_request_intents,
    memoriesql.model_usage_events,
    memoriesql.model_bulk_usage_forecasts,
    memoriesql.model_bulk_forecast_approvals,
    memoriesql.model_request_usage_fold,
    memoriesql.model_run_usage_rollup,
    memoriesql.model_task_attempt_usage_rollup,
    memoriesql.model_task_usage_rollup,
    memoriesql.model_module_daily_usage_rollup,
    memoriesql.model_operation_daily_usage_rollup,
    memoriesql.model_workspace_daily_usage_rollup,
    memoriesql.model_usage_fold_reconciliation,
    memoriesql.model_bulk_forecast_approval_status
TO memoriesql_application, memoriesql_worker;

GRANT EXECUTE ON FUNCTION memoriesql.record_model_provider_request_intent(
    jsonb, text, text
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.reauthorize_semantic_task_for_watch(
    uuid, uuid, uuid, bigint, text, text, timestamp with time zone
) TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.catalog_model_cost_microunits(
    bigint, bigint, bigint, bigint, bigint, bigint, bigint, bigint
) TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.append_model_usage_event(jsonb, text, text)
TO memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.record_bulk_usage_forecast(jsonb)
TO memoriesql_application, memoriesql_worker;
GRANT EXECUTE ON FUNCTION memoriesql.approve_bulk_usage_forecast(jsonb)
TO memoriesql_application, memoriesql_worker;

COMMENT ON TABLE memoriesql.model_provider_request_intents IS
    'Durable pre-dispatch intent for each visible first-party PydanticAI request; no prompts or response content.';
COMMENT ON TABLE memoriesql.model_usage_events IS
    'Append-only normalized usage or explicit unavailable/outcome facts; duplicates are idempotent and raw provider prose is forbidden.';
COMMENT ON VIEW memoriesql.model_request_usage_fold IS
    'Versioned deterministic fold separating normalized units, catalog-equivalent shadow cost, and incremental cash exposure.';
COMMENT ON COLUMN memoriesql.model_request_usage_fold.shadow_catalog_cost_microunits IS
    'Informational catalog-equivalent value; never a stop or downgrade signal for subscription-covered quality-first work.';
COMMENT ON COLUMN memoriesql.model_request_usage_fold.incremental_cash_exposure_microunits IS
    'Actual provider-reported cash when available, otherwise an explicitly labeled basis or unknown; subscription-covered work is zero incremental cash.';
COMMENT ON FUNCTION memoriesql.record_model_provider_request_intent(
    jsonb, text, text
) IS
    'Fails closed before dispatch, pins exact target/policy/pricing, rejects invisible retries and unqualified lower tiers, and serializes conservative safety reservations.';
COMMENT ON FUNCTION memoriesql.reauthorize_semantic_task_for_watch(
    uuid, uuid, uuid, bigint, text, text, timestamp with time zone
) IS
    'Preserves committed user cancellation as a distinct worker-watch stop reason while retaining the existing generation-fenced authorization result.';
COMMENT ON FUNCTION memoriesql.append_model_usage_event(jsonb, text, text) IS
    'Appends a terminal accounting fact through its already-authorized immutable request intent and exact original worker-attempt identity, even after scope-policy revocation or lease loss.';
COMMENT ON VIEW memoriesql.model_bulk_forecast_approval_status IS
    'Approval validity is bound to the exact forecast, source scope, policy, assumptions, and latest immutable series revision.';
