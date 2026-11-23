#!/usr/bin/env python3
"""claude-code-guardrails: one config-driven hook script for Claude Code.

Modes (first argument):

  pre                PreToolUse   protected paths, tool deny/ask/allow rules, PII-in-log guard
  session-start      SessionStart short context lines (cwd check, uncommitted changes, rule count)
  instructions-log   InstructionsLoaded  append one line per loaded instruction file

Config: $GUARDRAILS_CONFIG, else <project>/.claude/guardrails.json, else built-in defaults.
Project root: $CLAUDE_PROJECT_DIR, else the hook payload's cwd, else the process cwd.

Design rules:
  * Never crash Claude Code. On its own errors the script fails open, except the
    protected-path check, which is plain string logic and runs first.
  * Every decision carries a reason Claude can read and act on.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "protected_paths": ["~/.ssh/", "~/.aws/", ".env", "*.pem", "id_rsa"],
    "tools": {"deny": [], "ask": [], "allow": []},
    "bypass_ask_becomes_deny": True,
    "pii": {"paths": [], "allowed_email_domains": []},
    "session_start": {"expected_cwd": None, "warn_uncommitted": True, "rules_dir": ".claude/rules"},
    "instructions_log": {"dir": ".claude/logs"},
}

FILE_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Grep", "Glob")
FILE_FIELDS = ("file_path", "path", "notebook_path", "pattern", "glob")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit")
GLOB_CHARS = set("*?[")


# ------------------------------------------------------------------ config
def project_root(data: dict) -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    cwd = data.get("cwd")
    return Path(cwd) if isinstance(cwd, str) and cwd else Path.cwd()


def load_config(root: Path) -> dict:
    path = Path(os.environ["GUARDRAILS_CONFIG"]) if os.environ.get("GUARDRAILS_CONFIG") else root / ".claude" / "guardrails.json"
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if not path.is_file():
        return cfg
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"guardrails: could not read {path} ({exc}); using defaults", file=sys.stderr)
        return cfg
    if isinstance(user, dict):
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


# ------------------------------------------------------------------ output
def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))


def decide(decision: str, reason: str) -> None:
    emit("PreToolUse", permissionDecision=decision, permissionDecisionReason=reason)


# ----------------------------------------------------------------- helpers
def strings_in(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in strings_in(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in strings_in(v)]
    return []


def norm(text: str) -> str:
    return text.replace("\\", "/").lower()


def path_variants(entry: str) -> list[str]:
    """The entry as written, its home-expanded form, and (for ~/ entries) the bare folder
    name, so any user's home (/home/x/.ssh, C:\\Users\\x\\.ssh) is covered too."""
    out = {norm(entry).rstrip("/")}
    if entry.startswith("~"):
        out.add(norm(os.path.expanduser(entry)).rstrip("/"))
        if entry.startswith("~/") or entry.startswith("~\\"):
            out.add(norm(entry[2:]).rstrip("/"))
    return [v for v in out if v]


def tokens(blob: str) -> list[str]:
    return [t for t in re.split(r"[\s'\"=,;|()<>]+", blob) if t]


def protected_hit(blob: str, entries: list[str]) -> str | None:
    """Return the protected entry the blob touches, or None."""
    text = norm(blob)
    toks = tokens(text)
    for entry in entries:
        for variant in path_variants(entry):
            if GLOB_CHARS & set(variant):
                for tok in toks:
                    if fnmatch.fnmatchcase(tok, variant) or fnmatch.fnmatchcase(tok.rsplit("/", 1)[-1], variant):
                        return entry
            else:
                pattern = r"(?:^|[/\s'\"=:(])" + re.escape(variant) + r"(?:$|[/\s'\")])"
                if re.search(pattern, text):
                    return entry
    return None


# ------------------------------------------------------ read-only shell check
READ_ONLY_CMDS = {
    "grep", "egrep", "fgrep", "rg", "cat", "head", "tail", "sed", "awk", "ls", "find",
    "wc", "sort", "uniq", "cut", "tr", "nl", "stat", "file", "pwd", "echo", "printf",
    "jq", "diff", "basename", "dirname", "realpath", "true", "test", "[", "git",
}
SHELL_WORDS = {"for", "in", "do", "done", "if", "then", "else", "elif", "fi", "while", "{", "}", "!"}
GIT_READ = {"log", "status", "show", "diff", "rev-parse", "ls-files", "blame", "grep", "shortlog", "describe"}
WRITE_HINTS = re.compile(
    r"(?:\bsed\s+(?:-[a-zA-Z]*i|--in-place)|-delete\b|-exec\b|-execdir\b|\bsystem\s*\(|\btee\b|"
    r"\bxargs\b|`|\beval\b|\bsource\b|>\s*>|(?<![0-9&])>(?!\s*&|\s*/dev/null))"
)


def is_read_only_shell(cmd: str) -> bool:
    """True only when every part of the command is a known read-only tool."""
    cmd = cmd.strip()
    if not cmd or WRITE_HINTS.search(cmd.replace("2>/dev/null", "").replace("2>&1", "")):
        return False
    text = re.sub(r"\$\(", " ; ", cmd).replace(")", " ; ")
    for seg in re.split(r"\|\||&&|[;|\n]", text):
        words = seg.strip().split()
        while words and (words[0] in SHELL_WORDS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
            words = words[1:]
        if not words:
            continue
        if words[0] not in READ_ONLY_CMDS:
            if seg.strip().startswith("for "):
                continue
            return False
        if words[0] == "git":
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            if sub not in GIT_READ:
                return False
    return True


# -------------------------------------------------------------- tool rules
def rule_applies(rule: dict, tool: str, ti: dict) -> bool:
    if not isinstance(rule, dict) or not fnmatch.fnmatchcase(tool, str(rule.get("match", ""))):
        return False
    when = rule.get("when") or {}
    for field, regex in when.items():
        if not re.search(str(regex), str(ti.get(field, ""))):
            return False
    if rule.get("unless_read_only_shell"):
        field = rule.get("command_field", "command")
        if is_read_only_shell(str(ti.get(field) or "")):
            return False
    return True


def check_tools(cfg: dict, tool: str, ti: dict) -> tuple[str, str] | None:
    rules = cfg.get("tools") or {}
    defaults = {
        "deny": "Blocked by guardrails.json.",
        "allow": "Allowed by guardrails.json.",
        "ask": "guardrails.json asks a human to approve this call.",
    }
    for decision in ("deny", "allow", "ask"):
        for rule in rules.get(decision) or []:
            if rule_applies(rule, tool, ti):
                return decision, str(rule.get("reason") or defaults[decision])
    return None


# ------------------------------------------------------------- PII guard
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def outside_domains(addr: str, allowed: list[str]) -> bool:
    domain = addr.rsplit("@", 1)[-1].lower()
    return not any(domain == d.lower() or domain.endswith("." + d.lower()) for d in allowed)


def path_matches(path: str, root: Path, patterns: list[str]) -> bool:
    full = norm(path)
    try:
        rel = norm(str(Path(path).resolve().relative_to(root.resolve())))
    except (ValueError, OSError):
        rel = full
    return any(fnmatch.fnmatchcase(rel, norm(p)) or fnmatch.fnmatchcase(full, "*/" + norm(p)) for p in patterns)


def check_pii(cfg: dict, root: Path, tool: str, ti: dict) -> str | None:
    pii = cfg.get("pii") or {}
    path = ti.get("file_path")
    if tool not in WRITE_TOOLS or not isinstance(path, str) or not path_matches(path, root, pii.get("paths") or []):
        return None
    if tool == "Write":
        full = Path(path) if Path(path).is_absolute() else root / path
        try:
            before = full.read_text(encoding="utf-8") if full.is_file() else ""
        except (OSError, UnicodeError):
            before = ""
        after = str(ti.get("content") or "")
    else:
        edits = [ti] if tool == "Edit" else (ti.get("edits") or [])
        before = "\n".join(str(e.get("old_string") or "") for e in edits)
        after = "\n".join(str(e.get("new_string") or "") for e in edits)
    allowed = pii.get("allowed_email_domains") or []
    old = {e.lower() for e in EMAIL_RE.findall(before) if outside_domains(e, allowed)}
    added = {e.lower() for e in EMAIL_RE.findall(after) if outside_domains(e, allowed)} - old
    if not added:
        return None
    return (
        f"Blocked: this write adds {len(added)} personal email address(es) to {Path(path).name}, "
        "a file guardrails.json marks as no-PII. Use an id or a hash instead."
    )


# ------------------------------------------------------------------ modes
def pre(data: dict, cfg: dict, root: Path) -> None:
    tool = str(data.get("tool_name") or "")
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}

    if tool in FILE_TOOLS:
        blob = " ".join(v for v in (ti.get(k) for k in FILE_FIELDS) if isinstance(v, str))
    else:
        blob = " ".join(strings_in(ti))
    hit = protected_hit(blob, list(cfg.get("protected_paths") or []))
    if hit:
        decide("deny", f"Blocked: '{hit}' is a protected path. Claude never reads, lists, searches or touches it.")
        return

    verdict = check_tools(cfg, tool, ti)
    if verdict:
        decision, why = verdict
        if decision == "ask" and cfg.get("bypass_ask_becomes_deny") and data.get("permission_mode") == "bypassPermissions":
            decision, why = "deny", f"Blocked in bypass mode (nobody can approve): {why}"
        decide(decision, why)
        return

    reason = check_pii(cfg, root, tool, ti)
    if reason:
        decide("deny", reason)


def uncommitted(root: Path) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [ln[3:].strip() for ln in proc.stdout.decode("utf-8", "replace").splitlines() if len(ln) > 3]


def always_loaded(rule: Path) -> bool:
    try:
        text = rule.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if not text.startswith("---"):
        return True
    end = text.find("\n---", 3)
    return "paths:" not in (text[3:end] if end != -1 else "")


def session_start(data: dict, cfg: dict, root: Path) -> None:
    opts = cfg.get("session_start") or {}
    lines = []
    cwd = str(data.get("cwd") or os.getcwd())
    expected = opts.get("expected_cwd")
    if expected and os.path.normcase(os.path.abspath(cwd)) != os.path.normcase(os.path.abspath(os.path.expanduser(expected))):
        lines.append(f"WARNING: session started in {cwd}, expected {expected}. Project rules and guards may not load.")
    if opts.get("warn_uncommitted", True):
        changed = uncommitted(root)
        if changed:
            shown = ", ".join(changed[:5]) + (" ..." if len(changed) > 5 else "")
            lines.append(f"{len(changed)} tracked file(s) changed outside a commit: {shown}")
    rules_dir = root / str(opts.get("rules_dir") or ".claude/rules")
    if rules_dir.is_dir():
        count = sum(1 for r in rules_dir.glob("*.md") if always_loaded(r))
        lines.append(f"rules: {count} always-loaded rule file(s) in {opts.get('rules_dir') or '.claude/rules'}")
    if lines:
        emit("SessionStart", additionalContext="\n".join(lines))


def instructions_log(data: dict, cfg: dict, root: Path) -> None:
    if not data.get("file_path"):
        return
    now = datetime.now()
    fields = [
        now.strftime("%H:%M:%S"),
        str(data.get("load_reason") or "-"),
        str(data.get("memory_type") or "-"),
        str(data.get("file_path")),
        str(data.get("trigger_file_path") or "-"),
        str(data.get("session_id") or "-"),
    ]
    logs = root / str((cfg.get("instructions_log") or {}).get("dir") or ".claude/logs")
    try:
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / f"instructions-{now.date().isoformat()}.log").open("a", encoding="utf-8") as fh:
            fh.write("\t".join(f.replace("\t", " ") for f in fields) + "\n")
    except OSError:
        pass


MODES = {"pre": pre, "session-start": session_start, "instructions-log": instructions_log}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else "pre"
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return 0
    if not isinstance(data, dict) or mode not in MODES:
        return 0
    root = project_root(data)
    try:
        MODES[mode](data, load_config(root), root)
    except Exception as exc:  # fail open, but say so
        print(f"guardrails {mode} error: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
