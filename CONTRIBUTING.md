# Contributing

The current repository is a private review stage for a proposed public open-core project. Contributions should remain inside the provider-neutral contract-catalog boundary described in [Repository boundary](docs/architecture/repository-boundary.md).

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

Do not include user content, provider-specific fixtures, product code, credentials, generated build outputs, or files outside the explicit export registry.
