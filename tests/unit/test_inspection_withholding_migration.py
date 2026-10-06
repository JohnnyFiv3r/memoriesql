"""Database-free structure of migration 0043 (inspection withholding).

0043 restates stored-bead inspection (inspect_stored_bead_v1, latest installed
in 0041) and its evidence reader (read_stored_bead_evidence_v1, latest
installed in 0024) from their installed text with exactly the listed edits,
for the owner's decisions of 2026-10-06:
- decision 8: inspection withholds what the query population withholds;
- decision 6 (pairs 3 and 4): a denial, or a budget, leaves no audit trace, and
  every dependency that can deny the bead is authorized before any budget;
- the approved context-source fix: a context source is authorized through its
  event's source object, as evidence is.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "migrations"
NAME = "0043_inspection_withholding.sql"
INSPECT = "inspect_stored_bead_v1"
EVIDENCE = "read_stored_bead_evidence_v1"

UNAVAILABLE = "RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501';"
BUDGET = "RAISE EXCEPTION 'stored_bead_budget' USING ERRCODE='54000';"
HANDLER = "EXCEPTION WHEN insufficient_privilege THEN RETURN unavailable;\n"

FAMILY = """ -- Owner decision 8 of 2026-10-06: inspection withholds exactly what the query
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
"""

AUTHORIZE_FIRST = """  -- Owner decision 6 of 2026-10-06 (pair 4): authorize every statement under
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
   PERFORM memoriesql.revisiting_source_authorize(source);
  END LOOP;
"""

CONTEXT_OLD = """   FOREACH source IN ARRAY s.context_source_ids LOOP
    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
   END LOOP;
"""
CONTEXT_NEW = """   FOREACH source IN ARRAY s.context_source_ids LOOP
    -- A context source is a unit of the statement's own evidence. Authorize
    -- its event's source object, as evidence is; missing or unreadable reads
    -- exactly as an unknown note.
    SELECT e.source_object_id INTO source FROM memoriesql.source_units cu
     JOIN memoriesql.source_events e ON e.tenant_id=cu.tenant_id AND e.event_id=cu.event_id
     WHERE cu.tenant_id=s.tenant_id AND cu.source_unit_id=source;
    IF NOT FOUND THEN RAISE EXCEPTION 'stored_bead_unavailable' USING ERRCODE='42501'; END IF;
    PERFORM memoriesql.revisiting_source_authorize(source); sources:=array_append(sources,source);
   END LOOP;
"""

ACCEPTED = """ SELECT bv.* INTO v FROM memoriesql.accepted_bead_semantics a
 JOIN memoriesql.bead_versions bv USING(tenant_id,bead_version_id)
 WHERE a.tenant_id=b.tenant_id AND a.bead_id=b.bead_id;
"""
STATEMENT_BUDGET = """  SELECT count(*) INTO amount FROM (SELECT 1 FROM memoriesql.bead_semantic_statements
   WHERE tenant_id=v.tenant_id AND bead_id=v.bead_id AND statement_sequence<=watermark LIMIT 33) bounded;
"""
BYTES_BUDGET = (
    " IF octet_length(memoriesql.canonical_semantic_json_text(result))>262144 THEN "
    + BUDGET
    + " END IF;\n"
)
REVALIDATE = """ -- Revalidate all disclosed dependencies at the return boundary; audit writes only.
 FOR source IN SELECT DISTINCT unnest(sources) ORDER BY 1 LOOP
  PERFORM memoriesql.revisiting_source_authorize(source);
 END LOOP;
"""
BUDGETS_LAST = """ -- Owner decision 6 of 2026-10-06 (pair 4): budgets only after every
 -- authorization.
"""

# Applied in order to the latest installed text. Each edit is (installed text,
# 0043 text, occurrences). The handler is set aside first, so that only the
# body's denials and budgets become raises.
EDITS: tuple[tuple[str, str, int], ...] = (
    (HANDLER, "@@HANDLER@@\n", 1),
    ("THEN RETURN unavailable;", "THEN " + UNAVAILABLE, 9),
    ("THEN RETURN budget;", "THEN " + BUDGET, 5),
    (
        "@@HANDLER@@\n",
        HANDLER + " WHEN program_limit_exceeded THEN RETURN budget;\n",
        1,
    ),
    (CONTEXT_OLD, CONTEXT_NEW, 1),
    (ACCEPTED, ACCEPTED + FAMILY, 1),
    (STATEMENT_BUDGET, AUTHORIZE_FIRST + STATEMENT_BUDGET, 1),
    (BYTES_BUDGET + REVALIDATE, REVALIDATE + BUDGETS_LAST + BYTES_BUDGET, 1),
)

# The classification contribution's checks can deny the whole note, so they
# move, unchanged, from after the mentions to just before the statement budget.
CLASSIFICATION_FIRST = "  IF v.classification_contribution IS NOT NULL THEN\n"
CLASSIFICATION_LAST = (
    "  ELSIF r.task_contract_version=5 AND "
    "r.task_contract_key='memory.semantic.author-complete-unit' THEN "
    + UNAVAILABLE
    + "\n  END IF;\n"
)
CLASSIFICATION_MOVED = """  -- Owner decision 6 of 2026-10-06 (pair 4): the classification contribution's
  -- checks, moved unchanged from after the mentions, deny before any budget.
"""

EVIDENCE_UNAVAILABLE = (
    "RAISE EXCEPTION 'stored_evidence_unavailable' USING ERRCODE='42501';"
)
EVIDENCE_BUDGET = "RAISE EXCEPTION 'stored_evidence_budget' USING ERRCODE='54000';"
EVIDENCE_BUDGET_REPLY = (
    '\'{"contract_version":1,"outcome":"budget_exhausted","evidence":null}\'::jsonb'
)
SNAPSHOT_REFUSED = (
    " IF snapshot->>'outcome'<>'available' THEN RETURN jsonb_build_object("
    "'contract_version',1,'outcome',snapshot->>'outcome','evidence',NULL); END IF;\n"
)
SNAPSHOT_RAISED = (
    " IF snapshot->>'outcome'='budget_exhausted' THEN "
    + EVIDENCE_BUDGET
    + "\n ELSIF snapshot->>'outcome'<>'available' THEN "
    + EVIDENCE_UNAVAILABLE
    + " END IF;\n"
)
EVIDENCE_HANDLER = (
    "EXCEPTION WHEN insufficient_privilege OR no_data_found THEN RETURN unavailable;\n"
)

# The evidence reader inspects the note before and after the page, so decision
# 8 reaches it through inspection. Its refusals after the first inspection, and
# its budget, now raise inside its block as inspection's do.
EVIDENCE_EDITS: tuple[tuple[str, str, int], ...] = (
    (
        "CREATE FUNCTION memoriesql.read_stored_bead_evidence_v1(",
        "CREATE OR REPLACE FUNCTION memoriesql.read_stored_bead_evidence_v1(",
        1,
    ),
    (
        "  IF anchor IS NULL THEN RETURN unavailable; END IF;\n",
        "  IF anchor IS NULL THEN " + EVIDENCE_UNAVAILABLE + " END IF;\n",
        1,
    ),
    (SNAPSHOT_REFUSED, SNAPSHOT_RAISED, 2),
    (
        "  RETURN " + EVIDENCE_BUDGET_REPLY + "; END IF;\n",
        "  " + EVIDENCE_BUDGET + " END IF;\n",
        1,
    ),
    (
        EVIDENCE_HANDLER,
        EVIDENCE_HANDLER
        + " WHEN program_limit_exceeded THEN RETURN "
        + EVIDENCE_BUDGET_REPLY
        + ";\n",
        1,
    ),
)


def function(text: str, name: str) -> str:
    match = re.search(
        rf"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.{name}\(.*?\$\$;\n",
        text,
        re.S,
    )
    assert match is not None, name
    return match.group(0)


def installed(name: str) -> str:
    """The function as its latest installed migration before 0043."""
    latest = ""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name >= NAME:
            break
        text = path.read_text()
        if re.search(rf"CREATE (?:OR REPLACE )?FUNCTION memoriesql\.{name}\(", text):
            latest = text
    return function(latest, name)


def edited(text: str, edits: tuple[tuple[str, str, int], ...]) -> str:
    for old, new, count in edits:
        assert text.count(old) == count, (old[:60], text.count(old), count)
        text = text.replace(old, new)
    return text


def restated() -> str:
    text = edited(installed(INSPECT), EDITS)
    assert text.count(CLASSIFICATION_FIRST) == 1
    start = text.index(CLASSIFICATION_FIRST)
    end = text.index(CLASSIFICATION_LAST, start) + len(CLASSIFICATION_LAST)
    block = text[start:end]
    text = text[:start] + text[end:]
    assert text.count(STATEMENT_BUDGET) == 1
    return text.replace(
        STATEMENT_BUDGET, CLASSIFICATION_MOVED + block + STATEMENT_BUDGET
    )


def restated_evidence() -> str:
    return edited(installed(EVIDENCE), EVIDENCE_EDITS)


class InspectionWithholdingMigration(unittest.TestCase):
    def test_the_restatement_is_the_installed_text_with_exactly_the_edits(
        self,
    ) -> None:
        sql = (MIGRATIONS / NAME).read_text()
        self.assertEqual(function(sql, INSPECT), restated())
        self.assertEqual(function(sql, EVIDENCE), restated_evidence())

    def test_every_denial_and_budget_in_the_body_rolls_back(self) -> None:
        body = function((MIGRATIONS / NAME).read_text(), INSPECT)
        # Only the handler returns the bare outcomes; every path before it
        # raises, so its audit rows are rolled back with it.
        self.assertEqual(body.count("RETURN unavailable"), 1)
        self.assertEqual(body.count("RETURN budget"), 1)
        self.assertIn(
            HANDLER + " WHEN program_limit_exceeded THEN RETURN budget;\n", body
        )
        reader = function((MIGRATIONS / NAME).read_text(), EVIDENCE)
        # The evidence reader returns only its page; every refusal and budget
        # is its handler's.
        self.assertEqual(reader.count("RETURN unavailable"), 1)
        self.assertEqual(reader.count("RETURN jsonb_build_object"), 1)
        self.assertEqual(reader.count(EVIDENCE_BUDGET_REPLY), 1)

    def test_the_note_is_authorized_before_any_budget(self) -> None:
        # Pair 4. Every check that can deny the note runs before the first
        # budget. Later checks only repeat them (the statements' own loop and
        # the return boundary's revalidation), and the mentions' checks never
        # deny the note: an unreadable decision is reported per mention.
        body = function((MIGRATIONS / NAME).read_text(), INSPECT)
        first_budget = body.find(BUDGET)
        self.assertLess(body.find(AUTHORIZE_FIRST), first_budget)
        self.assertEqual(body.count(CLASSIFICATION_FIRST), 1)
        self.assertLess(body.find(CLASSIFICATION_FIRST), first_budget)
        self.assertLess(
            body.rfind("revisiting_source_authorize"), body.find(BYTES_BUDGET)
        )

    def test_nothing_else_changes(self) -> None:
        sql = (MIGRATIONS / NAME).read_text()
        self.assertNotRegex(sql, r"(?im)^\s*GRANT\b")
        self.assertEqual(
            re.findall(
                r"^CREATE (?:OR REPLACE )?FUNCTION memoriesql\.([a-z_0-9]+)\(",
                sql,
                re.M,
            ),
            [INSPECT, EVIDENCE],
        )


if __name__ == "__main__":
    unittest.main()
