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

## Checks

Run:

```console
python scripts/generate_catalogs.py --check
python -m unittest discover -v
ruff check src scripts tests
mypy src scripts tests
```

These are the inexpensive automatic checks, not complete installed qualification.
Follow the [CI cost and qualification policy](docs/ci-policy.md): both supported
interpreters still require full local database and restart proof with retained
exact-head evidence. Hosted database qualification is explicitly owner-requested,
not an automatic consequence of pushing a PR. Never report skipped jobs as passed.

Do not include user content, provider-specific fixtures, product code, credentials, generated build outputs, or files outside the explicit export registry.
