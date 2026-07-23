## What this changes

## Why

## Test plan

- [ ] `ruff check src tests eval scripts` and `ruff format --check src tests eval scripts`
- [ ] `mypy src`
- [ ] `bandit -r src && pip-audit --skip-editable`
- [ ] `pytest`
- [ ] If this touches a lens or the curator: ran `python eval/run_eval.py` and the recall change is stated below

**Recall change (if applicable):**
