"""Private preparation identity; no public result/digest or quality claim."""

from __future__ import annotations

import dataclasses
import unittest

from memoriesql.infrastructure.postgres.result_preparation import PreparedContent


class PreparationEncoding(unittest.TestCase):
    def test_exact_canonical_partitions_and_immutable_inputs(self) -> None:
        rows = {"rows": [["\U0001f680", "-0x0.0p+0", None, True]]}
        content = PreparedContent.encode(
            content=rows, witnesses={"members": ["1", "1"]}, dependencies=[]
        )
        rows["rows"].clear()
        self.assertEqual(
            content.content, b'{"rows":[["\\ud83d\\ude80","-0x0.0p+0",null,true]]}'
        )
        content.validate()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            content.content = b"null"  # type: ignore[misc]

    def test_partition_boundaries_are_part_of_private_digest(self) -> None:
        a = PreparedContent(b"1", b"23", b"4")
        b = PreparedContent(b"12", b"3", b"4")
        self.assertEqual(
            b"".join(dataclasses.astuple(a)), b"".join(dataclasses.astuple(b))
        )
        self.assertNotEqual(a.artifact_sha256, b.artifact_sha256)
        a.validate()
        b.validate()

    def test_manually_constructed_unsafe_or_noncanonical_bytes_are_refused(
        self,
    ) -> None:
        for value in (
            b'{"a":1,"a":2}',
            b'{ "a":1}',
            b'{"a":1.0}',
            b"NaN",
            b'"\\ud800"',
            '"\u00e9"'.encode(),
            b"\xff",
            b'"a\x00b"',
            b'{"b":2,"a":1}',
            b"1e0",
            b"+1",
            b"-0",
            b"[1,]",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    PreparedContent(value, b"[]", b"[]").validate()

    def test_native_float_or_unknown_object_is_never_coerced(self) -> None:
        for value in (1.5, float("inf"), object(), {1: "untyped key"}):
            with self.subTest(value=type(value)):
                with self.assertRaises((TypeError, ValueError)):
                    PreparedContent.encode(content=value, witnesses=[], dependencies=[])

    def test_byte_bound_is_checked_before_decoding_untrusted_large_content(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "byte bound"):
            PreparedContent(b"x" * (64 * 1024 * 1024), b"[]", b"[]").validate()
