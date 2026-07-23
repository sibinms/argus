"""eval/run_eval.py lives outside the argus package (it's a script, not
shipped), so it's loaded directly from its file path rather than imported
normally."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_eval", Path(__file__).parent.parent / "eval" / "run_eval.py"
)
assert _SPEC and _SPEC.loader
run_eval = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_eval)


def test_build_config_defaults_when_env_vars_absent(monkeypatch):
    monkeypatch.delenv("ARGUS_EVAL_LENS_MODEL", raising=False)
    monkeypatch.delenv("ARGUS_EVAL_CURATOR_MODEL", raising=False)

    default = run_eval.Config()
    config = run_eval.build_config()

    assert config.models.lens == default.models.lens
    assert config.models.curator == default.models.curator


def test_build_config_applies_env_var_overrides(monkeypatch):
    monkeypatch.setenv("ARGUS_EVAL_LENS_MODEL", "gemini/gemini-3.5-flash")
    monkeypatch.setenv("ARGUS_EVAL_CURATOR_MODEL", "gemini/gemini-3.1-pro-preview")

    config = run_eval.build_config()

    assert config.models.lens == "gemini/gemini-3.5-flash"
    assert config.models.curator == "gemini/gemini-3.1-pro-preview"
