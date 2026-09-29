"""A real accepted assessed relation whose closure a paired agent can read (AM-5).

Shared by PR-05's executor tests and the trusted query host's harness. Both pass
an EXISTING database and AssessedLifecycle fixture (QueryResultCommit's `h.db`
and `h.fixture`). The relation goes through the real activation, author,
specialist, attestor and apply path, with fictional content only.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any


@dataclass(frozen=True)
class RelationGrants:
    relation_id: uuid.UUID
    source_bead_id: uuid.UUID
    target_bead_id: uuid.UUID
    # The two explicit scopes the endpoints live in (the owner-private default
    # scope admits no grants to other principals).
    source_scope: uuid.UUID
    remote_scope: uuid.UUID
    # Closure member name -> the agent's access grant that makes it readable:
    # "remote_source" and "remote_target".
    grants: Mapping[str, uuid.UUID]
    # The agent's pairing, revised once here to include both scopes. Later
    # revisions of it must expect this revision.
    pairing_grant_id: uuid.UUID
    pairing_revision: int


def explicit_scope(db: Any, fixture: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """An explicit scope with its own fictional source, exactly as the fixture's
    remote_scope() builds one, except that every call gets a distinct source
    identity: remote_scope() may run only once per fixture (the harness may
    already have used it). Returns the scope and its source."""
    scope, source = uuid.uuid4(), uuid.uuid4()
    tag = source.hex[:12]
    pairings = db.execute(
        "SELECT r.pairing_grant_id, r.revision, r.allowed_capabilities, "
        "r.allowed_access_scope_ids FROM memoriesql.pairing_grant_revisions AS r "
        "JOIN memoriesql.pairing_grants AS g USING (tenant_id, pairing_grant_id) "
        "WHERE g.paired_principal_id IN (%s,%s) AND r.revision=(SELECT max(l.revision) "
        "FROM memoriesql.pairing_grant_revisions AS l WHERE l.tenant_id=r.tenant_id "
        "AND l.pairing_grant_id=r.pairing_grant_id)",
        (fixture.worker_principal, fixture.attestor),
    ).fetchall()
    assert len(pairings) == 2, "the worker and the attestor are each paired once"
    with db.transaction():
        fixture.begin()
        db.execute(
            "SELECT memoriesql.create_access_scope(%s,%s,'explicit',%s,%s)",
            (scope, uuid.uuid4(), "Fictional agent orchard " + tag, fixture.now),
        )
        for principal in (
            fixture.principal,
            fixture.worker_principal,
            fixture.attestor,
        ):
            db.execute(
                "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                (
                    uuid.uuid4(),
                    principal,
                    scope,
                    ["read", "write"],
                    fixture.now,
                    fixture.now + timedelta(hours=1),
                ),
            )
        # The worker's and the attestor's pairings extend to the new scope.
        for pairing, revision, capabilities, scopes in pairings:
            db.execute(
                "SELECT memoriesql.revise_pairing_grant(%s,%s,%s,%s,'active',%s,%s,%s)",
                (
                    pairing,
                    revision,
                    capabilities,
                    [*scopes, scope],
                    fixture.now,
                    fixture.now + timedelta(hours=1),
                    fixture.now,
                ),
            )
    db.execute(
        "INSERT INTO memoriesql.protected_resources SELECT tenant_id,workspace_id,%s,%s,"
        "resource_kind,owner_user_id,status,created_by_principal_id,created_at "
        "FROM memoriesql.protected_resources WHERE resource_id=%s",
        (scope, source, fixture.source),
    )
    db.execute(
        "INSERT INTO memoriesql.source_objects(tenant_id,workspace_id,access_scope_id,"
        "source_object_id,source_system,object_kind,external_object_id,schema_version,"
        "metadata,created_at,last_observed_at,owner_user_id) SELECT tenant_id,"
        "workspace_id,%s,%s,source_system,object_kind,%s,schema_version,metadata,"
        "created_at,last_observed_at,owner_user_id FROM memoriesql.source_objects "
        "WHERE source_object_id=%s",
        (scope, source, "orchard.agent." + tag, fixture.source),
    )
    producer, policy = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "INSERT INTO memoriesql.evidence_producer_policies SELECT tenant_id,workspace_id,"
        "%s,%s,%s,producer_principal_id,qualification_ref,normalization_policy_version,"
        "qualification_evidence_sha256,approved_by_principal_id,created_at,expires_at,"
        "status FROM memoriesql.evidence_producer_policies WHERE producer_policy_id=%s",
        (scope, producer, source, fixture.policy),
    )
    db.execute(
        "INSERT INTO memoriesql.complete_input_dispatch_policies SELECT tenant_id,%s,"
        "workspace_id,%s,%s,attestor_principal_id,approved_by_principal_id,"
        "qualification_evidence_sha256,created_at,expires_at,status,6 "
        "FROM memoriesql.complete_input_dispatch_policies WHERE dispatch_policy_id=%s",
        (policy, scope, source, fixture.dispatch_policy),
    )
    fixture.producers[source], fixture.policies[source] = producer, policy
    fixture.checkpoints[source] = "orchard.agent." + tag + ".raw"
    fixture.positions[source] = (0, 0)
    return scope, source


def assessed_relation_for_agent(
    db: Any, fixture: Any, agent_principal: uuid.UUID
) -> RelationGrants:
    """Precondition: the agent is paired exactly once, over any explicit scope,
    with memory.query and source.read. Each endpoint lives in its own new explicit
    scope. The agent's pairing gains both scopes and keeps its validity window, and
    the agent reads each through exactly one access grant."""
    source_scope, source_object = explicit_scope(db, fixture)
    target_scope, target_object = explicit_scope(db, fixture)
    tag = uuid.uuid4().hex[:8]
    source = fixture.remote_bead(
        "Fictional source: the orchard ledger balanced.",
        "Fictional second observation: every crate was counted.",
        key="agent-relation-source-" + tag,
        scope=source_scope,
        source=source_object,
    )
    target = fixture.remote_bead(
        "Fictional target: the orchard audit passed.",
        key="agent-relation-target-" + tag,
        scope=target_scope,
        source=target_object,
    )
    # Its own idempotency key: an existing fixture may already have activated a
    # relation under the fixture's default key.
    fixture.activate_relations(source, (target,), key="agent-relation-" + tag)
    fixture.propose(
        lambda packet: [
            fixture.proposal(
                packet,
                "supports",
                fixture.endpoint(packet, source, 0),
                fixture.endpoint(packet, target, 0),
                basis="agent_inferred",
            )
        ]
    )
    fixture.run_relations()
    row = db.execute(
        "SELECT relation_id FROM memoriesql.assessed_relations "
        "WHERE source_bead_id=%s AND target_bead_id=%s",
        (source, target),
    ).fetchone()
    assert row is not None, "the specialist did not accept the fictional relation"
    grants = {"remote_source": uuid.uuid4(), "remote_target": uuid.uuid4()}
    # Read as the test's admin connection, as remote_scope() does; the owner's
    # application role cannot read pairing revisions directly.
    pairings = db.execute(
        "SELECT r.pairing_grant_id, r.revision, r.allowed_capabilities, "
        "r.allowed_access_scope_ids, r.issued_at, r.expires_at "
        "FROM memoriesql.pairing_grant_revisions AS r "
        "JOIN memoriesql.pairing_grants AS g USING (tenant_id, pairing_grant_id) "
        "WHERE g.paired_principal_id=%s AND r.revision=(SELECT max(l.revision) "
        "FROM memoriesql.pairing_grant_revisions AS l WHERE l.tenant_id=r.tenant_id "
        "AND l.pairing_grant_id=r.pairing_grant_id)",
        (agent_principal,),
    ).fetchall()
    assert len(pairings) == 1, "the agent must be paired exactly once"
    pairing, revision, capabilities, scopes, issued, expires = pairings[0]
    with db.transaction():
        fixture.begin()
        revised = db.execute(
            "SELECT memoriesql.revise_pairing_grant(%s,%s,%s,%s,'active',%s,%s,%s)",
            (
                pairing,
                revision,
                capabilities,
                [*scopes, source_scope, target_scope],
                issued,
                expires,
                fixture.now,
            ),
        ).fetchone()[0]
        for member, scope in (
            ("remote_source", source_scope),
            ("remote_target", target_scope),
        ):
            db.execute(
                "SELECT memoriesql.create_access_grant(%s,%s,%s,%s,%s,%s)",
                (
                    grants[member],
                    agent_principal,
                    scope,
                    ["read"],
                    fixture.now,
                    fixture.now + timedelta(hours=1),
                ),
            )
    return RelationGrants(
        row[0],
        source,
        target,
        source_scope,
        target_scope,
        grants,
        pairing,
        int(revised) if revised is not None else revision + 1,
    )


def revoke_member(
    db: Any, fixture: Any, grants: RelationGrants, member: str = "remote_target"
) -> None:
    """Make exactly one closure member unreadable to the agent: revoke its grant."""
    with db.transaction():
        fixture.begin()
        db.execute(
            "SELECT memoriesql.revise_access_grant(%s,1,%s,'revoked',%s,%s,%s)",
            (
                grants.grants[member],
                ["read"],
                fixture.now,
                fixture.now + timedelta(hours=1),
                fixture.now,
            ),
        )
