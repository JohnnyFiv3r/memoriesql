"""The public-boundary check against scratch git repositories.

Each test builds a small real repository in a temporary directory and runs the
real check there, because what git tracks, stages and ignores is the behaviour
under test. This file is itself scanned by the check, so the URLs and home paths
it must refuse are assembled from pieces and never written whole.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.verify_public_boundary as boundary

SOURCE = boundary.ROOT
GH = "git" + "hub"
HOST = "https://" + "github.com/"
FOREIGN = HOST + "pallets/flask"
HOME = "/ho" + "me/dev/project/"
URL_REFUSED = "non-public repository URL"
CREDENTIAL_REFUSED = "credential in repository URL"
HOME_REFUSED = "absolute home path"
LOOKALIKES = (
    "JohnnyFiv3r/memoriesql-private",
    "JohnnyFiv3r/memoriesql-private.git",
    "JohnnyFiv3r/memoriesql." + "github.io",
    "JohnnyFiv3r/memoriesql.wiki.git",
    "JohnnyFiv3r/memoriesql.git.git",
    "JohnnyFiv3r/memoriesql.gitx",
    "JohnnyFiv3r/memoriesql.git-private",
    "JohnnyFiv3r/memoriesql.GIT",
    "JohnnyFiv3r/memoriesql_desktop",
    "johnnyfiv3r/memoriesql.git",
    "Other/memoriesql.git",
)


class ScratchRepository(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.outside = Path(scratch.name).resolve()
        self.repo = self.outside / "repo"
        inventory = json.loads(
            (SOURCE / "contracts/migration-inventory.json").read_text()
        )
        copied = [
            ".gitignore",
            "contracts/migration-inventory.json",
            "contracts/public-registry.json",
            "scripts/verify_public_boundary.py",
            *("migrations/" + row["filename"] for row in inventory["migrations"]),
        ]
        for relative in copied:
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / relative, target)
        # Only the repository's own ignore rules may decide what is examined:
        # no personal excludes file, and no repository inherited from a hook.
        environment = patch.dict(
            os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        )
        environment.start()
        self.addCleanup(environment.stop)
        for inherited in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            os.environ.pop(inherited, None)
        root = patch.object(boundary, "ROOT", self.repo)
        root.start()
        self.addCleanup(root.stop)
        self.git("init", "-q")
        self.git("add", "-A")

    def git(self, *arguments: str, cwd: Path | None = None) -> None:
        subprocess.run(
            ["git", *arguments], cwd=cwd or self.repo, check=True, capture_output=True
        )

    def write(self, relative: str, text: str = "Fictional note.\n") -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def passes(self) -> dict[str, object]:
        result = boundary.verify()
        self.assertEqual(result["private_boundary_leaks"], [])
        return result

    def refusal(self) -> str:
        with self.assertRaises(ValueError) as refused:
            boundary.verify()
        return str(refused.exception)

    def command(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.repo / "scripts/verify_public_boundary.py")],
            capture_output=True,
            text=True,
            check=False,
        )


class ExaminedFiles(ScratchRepository):
    def test_a_clean_checkout_passes_with_the_registry_count(self) -> None:
        registry = json.loads(
            (SOURCE / "contracts/public-registry.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            self.passes(),
            {
                "default_policy": "deny",
                "record_count": len(registry["records"]),
                "denied_roots": [],
                "private_boundary_leaks": [],
            },
        )

    def test_a_checkout_whose_directory_name_ends_in_a_space_passes(self) -> None:
        spaced = self.outside / "checkout "
        self.repo.rename(spaced)
        with patch.object(boundary, "ROOT", spaced):
            self.passes()

    def test_environments_the_repository_ignores_are_never_read(self) -> None:
        leak = FOREIGN + "\n" + HOME + "\n"
        for relative in (
            ".venv/lib/a.py",
            ".venv/bin/activate",
            ".venv/pyvenv.cfg",
            "venv/lib/a.py",
            ".tox/py313/lib/a.py",
        ):
            self.write(relative, leak)
        self.passes()

    def test_other_ignored_files_are_never_read(self) -> None:
        self.write("migrations/.DS_Store")
        self.write("build/x.txt", FOREIGN)
        (self.repo / "pkg/__pycache__").mkdir(parents=True)
        (self.repo / "pkg/__pycache__/m.pyc").write_bytes(FOREIGN.encode())
        self.passes()

    def test_a_tracked_file_is_read_whatever_its_directory_is_called(self) -> None:
        for directory in ("docs/build", "docs/dist", "docs/__pycache__"):
            for text, reason in ((FOREIGN, URL_REFUSED), (HOME, HOME_REFUSED)):
                with self.subTest(directory=directory, reason=reason):
                    leak = self.write(directory + "/leak.md", text)
                    self.git("add", "-f", directory + "/leak.md")
                    self.assertEqual(
                        self.refusal(),
                        f"private-boundary text leaked: {directory}/leak.md:{reason}",
                    )
                    self.git("rm", "-q", "-f", "--cached", directory + "/leak.md")
                    leak.unlink()
        self.passes()

    def test_untracked_files_git_does_not_ignore_are_read(self) -> None:
        self.write("notes.txt", FOREIGN)
        self.write(".vscode/settings.json", HOME)
        self.assertEqual(
            self.refusal(),
            "private-boundary text leaked: "
            f".vscode/settings.json:{HOME_REFUSED}, notes.txt:{URL_REFUSED}",
        )

    def test_a_staged_file_is_read_like_a_committed_one(self) -> None:
        self.write("notes.txt", FOREIGN)
        self.git("add", "notes.txt")
        self.assertEqual(
            self.refusal(), f"private-boundary text leaked: notes.txt:{URL_REFUSED}"
        )

    def test_untracked_denied_roots_and_scratch_migrations_are_refused(self) -> None:
        denied = self.write("apps/a.txt")
        self.assertEqual(self.refusal(), "denied public repository roots: ['apps']")
        denied.unlink()
        self.write("migrations/9999_scratch.sql", "SELECT 1;\n")
        self.assertEqual(self.refusal(), "unregistered migration source")

    def test_a_tracked_file_missing_from_disk_does_not_crash(self) -> None:
        extra = self.write("extra.txt")
        self.git("add", "extra.txt")
        extra.unlink()
        self.passes()
        next((self.repo / "migrations").iterdir()).unlink()
        self.assertEqual(self.refusal(), "unregistered migration source")


class RepositoryLinks(ScratchRepository):
    def test_an_allowed_repository_may_end_in_one_dot_git(self) -> None:
        self.write(
            "a.md",
            HOST
            + "JohnnyFiv3r/memoriesql.git\n"
            + HOST
            + "actions/python-versions.git\n",
        )
        self.git("add", "a.md")
        self.passes()

    def test_lookalike_repository_names_are_refused(self) -> None:
        for name in LOOKALIKES:
            with self.subTest(name):
                self.write("a.md", HOST + name + "\n")
                self.git("add", "a.md")
                self.assertEqual(
                    self.refusal(), f"private-boundary text leaked: a.md:{URL_REFUSED}"
                )


class RecognisedLinkForms(ScratchRepository):
    def refused_as(self, text: str, reason: str) -> None:
        self.write("a.md", text + "\n")
        self.git("add", "a.md")
        self.assertEqual(self.refusal(), f"private-boundary text leaked: a.md:{reason}")

    def test_every_recognised_form_of_a_foreign_repository_is_refused(self) -> None:
        foreign = "someone/private-repo"
        cases = (
            (f"git@{GH}.com:{foreign}.git", URL_REFUSED),
            (f"ssh://git@{GH}.com/{foreign}", URL_REFUSED),
            (f"http://{GH}.com/{foreign}", URL_REFUSED),
            (f"https://www.{GH}.com/{foreign}", URL_REFUSED),
            (f"HTTPS://{GH.upper()}.COM/{foreign}", URL_REFUSED),
            (f"https://ghp_example@{GH}.com/{foreign}", CREDENTIAL_REFUSED),
            (f"see {GH}.com/{foreign} for details", URL_REFUSED),
            (f"https://raw.{GH}usercontent.com/{foreign}/main/README.md", URL_REFUSED),
            (f"https://api.{GH}.com/repos/{foreign}", URL_REFUSED),
            (f"https://gist.{GH}.com/someone/0123456789abcdef", URL_REFUSED),
            (f"https://codeload.{GH}.com/{foreign}/zip/main", URL_REFUSED),
            (f"https://someone.{GH}.io/private-repo", URL_REFUSED),
        )
        for text, reason in cases:
            with self.subTest(text):
                self.refused_as(text, reason)

    def test_allowed_repositories_are_accepted_in_every_form(self) -> None:
        ours = "JohnnyFiv3r/memoriesql"
        python = "actions/python-versions"
        self.write(
            "a.md",
            "\n".join(
                (
                    f"git@{GH}.com:{ours}.git",
                    f"ssh://git@{GH}.com/{ours}",
                    f"HTTPS://{GH.title()}.com/{ours}",
                    f"https://raw.{GH}usercontent.com/{ours}/main/README.md",
                    f"https://api.{GH}.com/repos/{python}",
                    f"https://{GH}.com/{python}",
                )
            )
            + "\n",
        )
        self.git("add", "a.md")
        self.passes()

    def test_userinfo_is_refused_even_for_an_allowed_repository_and_never_printed(
        self,
    ) -> None:
        self.write("a.md", f"https://ghp_example@{GH}.com/JohnnyFiv3r/memoriesql\n")
        self.git("add", "a.md")
        message = self.refusal()
        self.assertEqual(
            message, f"private-boundary text leaked: a.md:{CREDENTIAL_REFUSED}"
        )
        self.assertNotIn("ghp_example", message)
        result = self.command()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("ghp_example", result.stderr)
        self.assertEqual(
            result.stderr, f"private-boundary text leaked: a.md:{CREDENTIAL_REFUSED}\n"
        )

    def test_owner_and_repository_stay_case_sensitive_in_every_form(self) -> None:
        for text in (
            f"git@{GH}.com:johnnyfiv3r/memoriesql.git",
            f"https://{GH}.com/JohnnyFiv3r/MemoriesQL",
            f"git@{GH}.com:Actions/python-versions.git",
            f"https://{GH}.com/actions/Python-Versions",
        ):
            with self.subTest(text):
                self.refused_as(text, URL_REFUSED)

    def test_github_urls_naming_no_repository_are_accepted(self) -> None:
        self.write(
            "a.md",
            "\n".join(
                (
                    f"https://docs.{GH}.com/en/actions/using-{GH}-hosted-runners",
                    f"https://api.{GH}.com/rate_limit",
                    f"https://raw.{GH}usercontent.com/someone",
                    f"https://not{GH}.com/x/y",
                )
            )
            + "\n",
        )
        self.git("add", "a.md")
        self.passes()

    def test_every_pages_site_is_refused(self) -> None:
        for text in (
            f"https://johnnyfiv3r.{GH}.io/memoriesql",
            f"https://someone.{GH}.io",
        ):
            with self.subTest(text):
                self.refused_as(text, URL_REFUSED)

    def test_the_git_user_is_exempt_only_on_github_ssh_forms(self) -> None:
        ours = "JohnnyFiv3r/memoriesql"
        for text in (
            f"ssh://git@raw.{GH}usercontent.com/{ours}",
            f"https://git@{GH}.com/{ours}",
            f"ssh://deploy@{GH}.com/{ours}",
            f"ssh://git@{GH}.com",
            f"ssh://git@{GH}.com/",
            f"ssh://git@{GH}.com?probe=1",
        ):
            with self.subTest(text):
                self.refused_as(text, CREDENTIAL_REFUSED)
        self.write("a.md", f"git@{GH}.com:{ours}.git\nssh://git@{GH}.com/{ours}\n")
        self.git("add", "a.md")
        self.passes()

    def test_hosts_that_only_begin_like_github_hosts_are_accepted(self) -> None:
        invalid = ".example.invalid"
        self.write(
            "a.md",
            "\n".join(
                (
                    f"https://fake-user@{GH}.com{invalid}/x/y",
                    f"https://{GH}.com{invalid}/someone/private-repo",
                    f"https://not{GH}.com/someone/private-repo",
                    f"https://raw.{GH}usercontent.com{invalid}/someone/private-repo",
                    f"https://gist.{GH}.com{invalid}/x",
                    f"https://gist.{GH}.com@example.invalid/x",
                    f"https://someone.{GH}.io@example.invalid/x",
                    f"https://gist.{GH}.com:secret@example.invalid/x",
                    f"https://someone.{GH}.io:secret@example.invalid/x",
                    f"A sentence that ends with {GH}.com.",
                )
            )
            + "\n",
        )
        self.git("add", "a.md")
        self.passes()

    def test_every_occurrence_of_the_gist_host_is_refused(self) -> None:
        for text in (
            f"https://gist.{GH}.com",
            f"https://gist.{GH}.com?probe=1",
            f"https://gist.{GH}.com/someone/0123456789abcdef",
            f"gist.{GH}.com/someone/0123456789abcdef",
        ):
            with self.subTest(text):
                self.refused_as(text, URL_REFUSED)

    def test_a_url_glued_after_another_is_still_read(self) -> None:
        for text in (
            f"https://api.{GH}.com/x,https://{GH}.com/evil/r",
            f"https://{GH}.com/JohnnyFiv3r/memoriesql/https://{GH}.com/evil/r",
            f"https://{GH}.com/JohnnyFiv3r/memoriesql/https://someone.{GH}.io",
        ):
            with self.subTest(text):
                self.refused_as(text, URL_REFUSED)

    def test_each_occurrence_is_reported_once(self) -> None:
        self.write(
            "a.md",
            f"git@{GH}.com:someone/private-repo\n"
            f"https://ghp_example@{GH}.com/someone/private-repo\n",
        )
        self.git("add", "a.md")
        self.assertEqual(
            self.refusal(),
            "private-boundary text leaked: "
            f"a.md:{URL_REFUSED}, a.md:{CREDENTIAL_REFUSED}",
        )


class WithoutAUsableCheckout(ScratchRepository):
    def test_without_git_on_path_the_check_refuses(self) -> None:
        empty = self.outside / "empty"
        empty.mkdir()
        with patch.dict(os.environ, {"PATH": str(empty)}):
            self.assertIsNone(shutil.which("git"))
            self.assertEqual(self.refusal(), boundary.NO_GIT)

    def test_a_copy_without_git_metadata_is_refused(self) -> None:
        shutil.rmtree(self.repo / ".git")
        self.assertEqual(self.refusal(), boundary.NOT_A_CHECKOUT)

    def test_a_copy_inside_another_repository_is_refused(self) -> None:
        shutil.rmtree(self.repo / ".git")
        self.git("init", "-q", cwd=self.outside)
        self.assertEqual(self.refusal(), boundary.NOT_A_CHECKOUT)

    def test_a_repository_git_cannot_read_is_refused(self) -> None:
        (self.repo / ".git/index").write_bytes(b"garbage\n")
        refusal = self.refusal()
        start, end = boundary.CANNOT_LIST.split("{reason}")
        self.assertTrue(refusal.startswith(start), refusal)
        self.assertTrue(refusal.endswith(end), refusal)
        self.assertGreater(len(refusal), len(start) + len(end))
        self.assertNotIn("\n", refusal)

    def test_a_refusal_never_falls_back_to_reading_the_disk(self) -> None:
        shutil.rmtree(self.repo / ".git")
        self.write("apps/a.txt")
        self.assertEqual(self.refusal(), boundary.NOT_A_CHECKOUT)


class Command(ScratchRepository):
    def test_the_command_prints_one_line_and_exits_one_on_failure(self) -> None:
        self.write("notes.txt", FOREIGN)
        result = self.command()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr, f"private-boundary text leaked: notes.txt:{URL_REFUSED}\n"
        )

    def test_the_command_prints_the_result_and_exits_zero_on_success(self) -> None:
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), self.passes())
        self.assertEqual(
            list(json.loads(result.stdout)),
            [
                "default_policy",
                "denied_roots",
                "private_boundary_leaks",
                "record_count",
            ],
        )
