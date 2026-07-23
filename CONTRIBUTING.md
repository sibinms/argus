# Contributing to Argus

Pull requests are welcome.

## Development setup

```bash
git clone https://github.com/sibinms/argus.git
cd argus
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before submitting a PR

Run the same checks CI enforces:

```bash
ruff check src tests eval scripts
ruff format --check src tests eval scripts
python scripts/check_readme_version.py
mypy src
bandit -r src && pip-audit --skip-editable
pytest
```

If you change a lens or the curator, also run `python eval/run_eval.py` and
include the recall change in your PR description. CI runs it too (the `eval`
job), but only informationally — LLM output is non-deterministic, so a hard
recall gate would be flaky. State what you measured rather than relying on
CI's number alone.

## Adding a lens

Lenses are plain markdown, no schema. See
[docs/writing-a-lens.md](docs/writing-a-lens.md) for the format and where
built-in lenses live.

## Reporting bugs and requesting features

Use the issue templates — they ask for the context that's actually useful
here (repro steps for a bug, the recall/precision trade-off for a feature).

## Security

Don't open a public issue for a vulnerability — see
[SECURITY.md](SECURITY.md).

## License

By contributing, you agree your contribution is licensed under this
project's [MIT license](LICENSE).
