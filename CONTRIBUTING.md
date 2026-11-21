# Contributing

This is the public memoriesQL open-core repository. Contributions may extend its
provider-neutral contracts, canonical PostgreSQL schema and runtime within the
explicit [repository boundary](docs/architecture/repository-boundary.md). Desktop
product code, source-specific acquisition/parsing and provider bindings belong
outside this repository. Read [AGENTS.md](AGENTS.md) before making changes.

## Developer Certificate of Origin

This project uses the [Developer Certificate of Origin 1.1](https://developercertificate.org/) and does not require a contributor license agreement. Sign each commit with:

```text
Signed-off-by: Your Name <your.email@example.com>
```

Use `git commit -s` to add the sign-off. By contributing, you certify the statements in the DCO for that contribution.

## Quickstart

You need Python 3.13 or 3.14, the two supported versions, on Linux or macOS. You
do not need a database.

```console
git clone https://github.com/JohnnyFiv3r/memoriesql
cd memoriesql
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
export PYTHONPATH=src
```

Use `python3.14` in the third line for Python 3.14. In each new shell, from the
checkout, run the `source` and `export` lines again. The checks run from the
source tree, so do not install the project itself.

## Checks

Run these from the repository root:

```console
python scripts/generate_catalogs.py --check
python -m unittest discover -v
ruff check src scripts tests
mypy src scripts tests
```

Each one exits 0 on a clean checkout. The `package` job in
[`.github/workflows/python-package.yml`](.github/workflows/python-package.yml) is
the authoritative, complete list. It also checks every generated contract, the
export boundary and a deterministic package build, and it is the one check every
pull request must pass. Run the tests as an ordinary user: two of them fail when
run as root.

These checks need no PostgreSQL. The database tests in `tests/runtime` and
`tests/migrations` are not collected by the command above. They run only against
the installed package in hosted CI, under the
[CI cost and qualification policy](docs/ci-policy.md), which says when that
qualification runs. You are not expected to reproduce it locally.
Never report skipped jobs as passed.

Do not include user content, provider-specific fixtures, product code, credentials, generated build outputs, or files outside the explicit export registry.
