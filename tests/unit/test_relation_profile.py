"""The packaged relation semantic profile is a projection that never gates authoring."""

from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from memoriesql.application import authored_relations as ar
from memoriesql.application.relation_profile import (
    BUILT_IN_KEYS,
    RELATION_SEMANTIC_PROFILE,
    SSTORYTIME_COMMIT,
    RelationFamilyMapping,
    RelationSemanticProfile,
)


class RelationProfileTests(unittest.TestCase):
    def test_profile_maps_exactly_the_eleven_built_in_predicates(self) -> None:
        mappings = {m.key: m for m in RELATION_SEMANTIC_PROFILE.mappings}
        self.assertEqual(tuple(mappings), BUILT_IN_KEYS)
        self.assertEqual(
            {key for key, m in mappings.items() if m.family is not None},
            {"caused_by", "led_to", "enables", "part_of", "depends_on", "blocks", "associated_with"},
        )
        self.assertEqual(
            (mappings["part_of"].family, mappings["part_of"].orientation), ("CONTAINS", -2)
        )
        self.assertEqual(mappings["supersedes"].mapping_status, "intentionally_native")
        self.assertEqual(mappings["derived_from"].mapping_status, "unresolved")
        with self.assertRaises(ValidationError):
            RelationSemanticProfile.model_validate(
                RELATION_SEMANTIC_PROFILE.model_dump() | {"mappings": RELATION_SEMANTIC_PROFILE.mappings[1:]}
            )
        with self.assertRaises(ValidationError):
            RelationFamilyMapping(key="blocks", family="LEADSTO", orientation=None,
                                  mapping_status="provisional")

    def test_attribution_names_the_pinned_upstream_without_links(self) -> None:
        text = json.dumps(RELATION_SEMANTIC_PROFILE.model_dump(mode="json"))
        self.assertIn(SSTORYTIME_COMMIT, RELATION_SEMANTIC_PROFILE.attribution)
        self.assertIn("Apache-2.0", RELATION_SEMANTIC_PROFILE.attribution)
        self.assertNotIn("http", text)

    def test_family_never_reaches_the_authoring_contract(self) -> None:
        authoring = json.dumps(
            [ar.RelatedExecutionInput.model_json_schema(), ar.RelatedExecutionOutput.model_json_schema()]
        )
        for word in ("family", "orientation", "LEADSTO", "mapping_status"):
            self.assertNotIn(word, authoring)


if __name__ == "__main__":
    unittest.main()
