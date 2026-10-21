"""Installed acceptance of the trusted query host, outside the confined runner.

The host needs Unix sockets and separate host processes (restart, SIGKILL
mid-query), which `run_installed_acceptance.py` denies by design. Invoke with
`python -I` from the disposable directory holding the flat-copied runtime tests,
after installing the wheel; it uses the same N1_TEST_DATABASE_URL service and
fictional fixtures. No owner data, model call or production database is involved.
"""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path
from typing import Any

EXPECTED_TEST_IDS = (
    "query_host_acceptance.QueryHostAcceptance.test_a_commit_still_in_flight_is_never_read_as_a_rollback",
    "query_host_acceptance.QueryHostAcceptance.test_a_page_that_cannot_fit_is_refused_never_empty",
    "query_host_acceptance.QueryHostAcceptance.test_admission_bypass_cannot_write_elevate_or_widen",
    "query_host_acceptance.QueryHostAcceptance.test_host_runs_expiry_cleanup_and_check_reports_it",
    "query_host_acceptance.QueryHostAcceptance.test_lost_owner_connection_keeps_capacity_until_work_ends",
    "query_host_acceptance.QueryHostAcceptance.test_operator_close_is_owner_only_and_refused_while_unsettled",
    "query_host_acceptance.QueryHostAcceptance.test_other_uid_is_refused_before_database_work",
    "query_host_acceptance.QueryHostAcceptance.test_owner_loss_after_reader_settles_settles_once_without_receipt",
    "query_host_acceptance.QueryHostAcceptance.test_preflight_names_the_login_that_cannot_connect",
    "query_host_acceptance.QueryHostAcceptance.test_provisioning_never_discards_secrets_a_lost_commit_may_have_kept",
    "query_host_acceptance.QueryHostAcceptance.test_provisioning_rotation_and_privilege_preflight",
    "query_host_acceptance.QueryHostAcceptance.test_public_cli_query_and_reuse_cross_real_socket_with_current_authority",
    "query_host_acceptance.QueryHostAcceptance.test_refusals_are_indistinguishable_and_reach_no_executor",
    "query_host_acceptance.QueryHostAcceptance.test_replies_are_the_executors_bytes_and_readers_match_direct",
    "query_host_acceptance.QueryHostAcceptance.test_request_content_never_reaches_replies_or_the_host_log",
    "query_host_acceptance.QueryHostAcceptance.test_restart_redelivers_the_same_result_without_rerun",
    "query_host_acceptance.QueryHostAcceptance.test_sigkill_mid_query_keeps_capacity_until_reader_ends",
)


def test_ids(suite: unittest.TestSuite) -> list[str]:
    identities: list[str] = []
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            identities.extend(test_ids(case))
        elif isinstance(case, unittest.TestCase):
            identities.append(case.id())
        else:
            raise ValueError("invalid query host test selection")
    return identities


class ContainmentResult(unittest.TextTestResult):
    containment_fatal = False
    fatal_evidence_failed = False

    def addError(self, test: unittest.TestCase, err: Any) -> None:
        if getattr(err[1], "fatal_containment", False):
            # Set the irreversible stop BEFORE ordinary unittest machinery can
            # continue. Existing TestCase cleanup still runs on this exception.
            self.containment_fatal = True
            self.stop()
            try:
                from query_host_cli_identity import latch

                latch("host_case_containment_unknown", [])
            except Exception:
                self.fatal_evidence_failed = True
        super().addError(test, err)


def main() -> int:
    sys.path.insert(0, str(Path.cwd()))
    if (Path.cwd() / "query-host-containment-fatal.json").exists():
        print("prior containment failure; no proof reuse", file=sys.stderr)
        return 1
    # The fault injector is support code, not another acceptance module.
    # Require every host oracle AND the real public CLI route exactly once.
    suite = unittest.defaultTestLoader.loadTestsFromName("query_host_acceptance")
    actual = Counter(test_ids(suite))
    expected = Counter(EXPECTED_TEST_IDS)
    if actual != expected:
        print(
            "query host acceptance selection incomplete: "
            f"expected={sum(expected.values())} discovered={sum(actual.values())}; "
            "refusing to pass",
            file=sys.stderr,
        )
        return 1
    result = unittest.TextTestRunner(verbosity=2, resultclass=ContainmentResult).run(
        suite
    )
    if not isinstance(result, ContainmentResult):
        return 1
    if result.containment_fatal or result.fatal_evidence_failed:
        return 1
    if result.testsRun != len(EXPECTED_TEST_IDS) or result.skipped:
        print(
            "query host acceptance omitted or skipped cases; refusing to pass",
            file=sys.stderr,
        )
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
