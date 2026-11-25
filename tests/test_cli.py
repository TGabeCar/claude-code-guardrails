import json
import os
import re

import pytest

from guardrails import cli, guard


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


@pytest.mark.parametrize("preset", ["solo", "team", "production"])
def test_presets_are_valid_and_deny_env(tmp_path, preset):
    cfg = cli.presets()[preset]
    assert cfg["_about"]
    for rules in cfg["tools"].values():
        for rule in rules:
            for regex in (rule.get("when") or {}).values():
                re.compile(regex)
    data = {"tool_name": "Read", "tool_input": {"file_path": "app/.env"}}
    assert guard.evaluate(data, {**guard.DEFAULT_CONFIG, **cfg}, tmp_path)[0] == "deny"


def test_init_writes_everything_and_doctor_passes(tmp_path, capsys):
    code, out = run(capsys, "init", "--preset", "team", "--dir", str(tmp_path))
    assert code == 0
    assert (tmp_path / ".claude/hooks/guard.py").read_text(encoding="utf-8") == cli.GUARD_SRC.read_text(encoding="utf-8")
    cfg = json.loads((tmp_path / ".claude/guardrails.json").read_text(encoding="utf-8"))
    assert "_about" not in cfg and cfg["pii"]["paths"]
    code, out = run(capsys, "doctor", "--dir", str(tmp_path))
    assert code == 0, out
    assert "on a leash" in out


def test_init_is_idempotent_and_keeps_existing_settings(tmp_path, capsys):
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}))
    run(capsys, "init", "--dir", str(tmp_path))
    run(capsys, "init", "--dir", str(tmp_path))
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo hi"
    for event in cli.HOOK_EVENTS:
        assert len(data["hooks"][event]) == 1


def test_init_keeps_custom_config_unless_forced(tmp_path, capsys):
    cfg = tmp_path / ".claude/guardrails.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"protected_paths": ["mine/"]}')
    run(capsys, "init", "--dir", str(tmp_path))
    assert json.loads(cfg.read_text())["protected_paths"] == ["mine/"]
    run(capsys, "init", "--preset", "production", "--force", "--dir", str(tmp_path))
    assert "~/.kube/" in json.loads(cfg.read_text())["protected_paths"]


def test_init_rejects_unknown_preset(tmp_path, capsys):
    code, out = run(capsys, "init", "--preset", "yolo", "--dir", str(tmp_path))
    assert code == 2 and "solo" in out


def test_doctor_fails_on_empty_project(tmp_path, capsys):
    code, out = run(capsys, "doctor", "--dir", str(tmp_path))
    assert code == 1
    assert "missing" in out


def test_doctor_flags_stale_hook(tmp_path, capsys):
    run(capsys, "init", "--dir", str(tmp_path))
    hook = tmp_path / ".claude/hooks/guard.py"
    hook.write_text(hook.read_text(encoding="utf-8").replace(guard.__version__, "0.0.1"), encoding="utf-8")
    code, out = run(capsys, "doctor", "--dir", str(tmp_path))
    assert code == 1 and "older than" in out


def test_explain_reports_rule(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GUARDRAILS_CONFIG", raising=False)
    run(capsys, "init", "--preset", "production", "--dir", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    code, out = run(capsys, "explain", "Bash", '{"command": "kubectl delete pod api-0"}', "--dir", str(tmp_path))
    assert code == 0 and "DENY" in out and "tools.deny" in out
    code, out = run(capsys, "explain", "mcp__crm__execute_api", '{"endpoint": "GET /v1/deals"}', "--dir", str(tmp_path))
    assert "ALLOW" in out
    code, out = run(capsys, "explain", "mcp__crm__delete_contact", "--bypass", "--dir", str(tmp_path))
    assert "bypass" in out
    code, out = run(capsys, "explain", "Bash", '{"command": "pytest -q"}', "--dir", str(tmp_path))
    assert "PASS" in out


def test_explain_rejects_bad_json(tmp_path, capsys):
    code, out = run(capsys, "explain", "Read", "{nope", "--dir", str(tmp_path))
    assert code == 2


def test_presets_command_lists_all(capsys):
    code, out = run(capsys, "presets")
    assert code == 0
    assert all(name in out for name in ("solo", "team", "production"))


def test_version_matches_package():
    import guardrails
    assert guardrails.__version__ == guard.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", guard.__version__)
