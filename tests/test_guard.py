import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "guardrails" / "guard.py"
EXAMPLE = ROOT / "examples" / "guardrails.json"


def run(mode, payload, project, config=EXAMPLE):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    if config:
        env["GUARDRAILS_CONFIG"] = str(config)
    else:
        env.pop("GUARDRAILS_CONFIG", None)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), mode],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.decode("utf-8").strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


def decision(tool, ti, project, **extra):
    out = run("pre", {"tool_name": tool, "tool_input": ti, **extra}, project)
    return out["permissionDecision"] if out else None


# --------------------------------------------------------- protected paths
@pytest.mark.parametrize(
    "tool,ti",
    [
        ("Read", {"file_path": "/repo/.env"}),
        ("Read", {"file_path": "C:\\repo\\.env"}),
        ("Read", {"file_path": "/home/me/.ssh/id_ed25519"}),
        ("Grep", {"pattern": "KEY", "path": "~/.secrets/"}),
        ("Glob", {"pattern": "**/*.pem"}),
        ("Bash", {"command": "cat .env | head"}),
        ("Bash", {"command": "ls ~/.aws"}),
        ("PowerShell", {"command": "Get-Content certs/server.pem"}),
        ("mcp__fs__read_file", {"path": "/srv/app/.env"}),
    ],
)
def test_protected_paths_are_denied(tmp_path, tool, ti):
    assert decision(tool, ti, tmp_path) == "deny"


def test_home_expanded_protected_path_is_denied(tmp_path):
    home_ssh = os.path.join(os.path.expanduser("~"), ".ssh", "config")
    assert decision("Read", {"file_path": home_ssh}, tmp_path) == "deny"


@pytest.mark.parametrize(
    "tool,ti",
    [
        ("Read", {"file_path": "/repo/.environment.md"}),
        ("Read", {"file_path": "/repo/src/env.py"}),
        ("Bash", {"command": "echo $ENV_NAME"}),
        ("Read", {"file_path": "/repo/README.md"}),
    ],
)
def test_lookalikes_are_not_blocked(tmp_path, tool, ti):
    assert decision(tool, ti, tmp_path) is None


# --------------------------------------------------------------- tool rules
def test_send_tools_are_denied(tmp_path):
    assert decision("mcp__mail__send_email", {"to": "a@b.c"}, tmp_path) == "deny"


def test_get_api_call_is_allowed_but_post_asks(tmp_path):
    assert decision("mcp__crm__execute_api", {"endpoint": "GET /v1/items"}, tmp_path) == "allow"
    assert decision("mcp__crm__execute_api", {"endpoint": "POST /v1/items"}, tmp_path) == "ask"


def test_read_only_remote_command_passes_but_write_asks(tmp_path):
    assert decision("mcp__box__run_command", {"command": "git log --oneline | head -5"}, tmp_path) is None
    assert decision("mcp__box__run_command", {"command": "rm -rf build"}, tmp_path) == "ask"
    assert decision("mcp__box__run_command", {"command": "cat a > b"}, tmp_path) == "ask"
    assert decision("mcp__box__run_command", {"command": "git push"}, tmp_path) == "ask"


def test_ask_becomes_deny_in_bypass_mode(tmp_path):
    out = run(
        "pre",
        {"tool_name": "mcp__db__delete_rows", "tool_input": {}, "permission_mode": "bypassPermissions"},
        tmp_path,
    )
    assert out["permissionDecision"] == "deny"
    assert "bypass" in out["permissionDecisionReason"]


def test_unlisted_tools_pass_through(tmp_path):
    assert decision("Bash", {"command": "pytest -q"}, tmp_path) is None


# --------------------------------------------------------------- PII guard
def test_new_personal_email_in_log_is_denied(tmp_path):
    ti = {"file_path": str(tmp_path / "logs" / "today.md"), "content": "Refunded jane.doe@gmail.com"}
    assert decision("Write", ti, tmp_path) == "deny"


def test_allowed_domain_and_existing_email_pass(tmp_path):
    log = tmp_path / "logs" / "today.md"
    log.parent.mkdir()
    log.write_text("Contact: old@gmail.com\n", encoding="utf-8")
    ti = {"file_path": str(log), "content": "Contact: old@gmail.com\nOwner: ops@example.com\n"}
    assert decision("Write", ti, tmp_path) is None


def test_edit_adding_email_is_denied(tmp_path):
    ti = {"file_path": str(tmp_path / "logs" / "x.md"), "old_string": "TBD", "new_string": "bob@yahoo.com"}
    assert decision("Edit", ti, tmp_path) == "deny"


def test_pii_rule_ignores_other_files(tmp_path):
    ti = {"file_path": str(tmp_path / "notes.md"), "content": "bob@yahoo.com"}
    assert decision("Write", ti, tmp_path) is None


# ------------------------------------------------------------ robustness
def test_defaults_used_without_config(tmp_path):
    out = run("pre", {"tool_name": "Read", "tool_input": {"file_path": "/x/.env"}}, tmp_path, config=None)
    assert out["permissionDecision"] == "deny"


def test_broken_config_falls_back_to_defaults(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    out = run("pre", {"tool_name": "Read", "tool_input": {"file_path": "/x/.env"}}, tmp_path, config=bad)
    assert out["permissionDecision"] == "deny"


def test_garbage_stdin_never_crashes(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), "pre"], input=b"\xff\xfe not json", capture_output=True)
    assert proc.returncode == 0


# ------------------------------------------------------------ session start
def test_session_start_reports_rules_and_cwd(tmp_path):
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "always.md").write_text("# always\n", encoding="utf-8")
    (rules / "scoped.md").write_text("---\npaths: src/**\n---\n# scoped\n", encoding="utf-8")
    cfg = tmp_path / "g.json"
    cfg.write_text(json.dumps({"session_start": {"expected_cwd": str(tmp_path / "elsewhere")}}), encoding="utf-8")
    out = run("session-start", {"cwd": str(tmp_path)}, tmp_path, config=cfg)
    text = out["additionalContext"]
    assert "1 always-loaded rule file" in text
    assert "WARNING" in text


def test_session_start_flags_uncommitted_changes(tmp_path):
    git = ["git", "-C", str(tmp_path)]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["config", "user.email", "t@example.com"], check=True)
    subprocess.run(git + ["config", "user.name", "t"], check=True)
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    subprocess.run(git + ["add", "a.txt"], check=True)
    subprocess.run(git + ["commit", "-qm", "init"], check=True)
    (tmp_path / "a.txt").write_text("2", encoding="utf-8")
    out = run("session-start", {"cwd": str(tmp_path)}, tmp_path)
    assert "1 tracked file(s) changed" in out["additionalContext"]


# --------------------------------------------------------- instructions log
def test_instructions_log_appends_a_line(tmp_path):
    payload = {"file_path": "/p/CLAUDE.md", "load_reason": "session_start", "memory_type": "Project", "session_id": "s1"}
    assert run("instructions-log", payload, tmp_path) is None
    logs = list((tmp_path / ".claude" / "logs").glob("instructions-*.log"))
    assert len(logs) == 1
    line = logs[0].read_text(encoding="utf-8").strip().split("\t")
    assert line[1:4] == ["session_start", "Project", "/p/CLAUDE.md"]
