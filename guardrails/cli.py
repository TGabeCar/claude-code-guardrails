"""claude-guardrails: install, test and check the guardrails hooks.

  claude-guardrails init [--preset solo|team|production] [--dir PATH] [--force]
  claude-guardrails explain TOOL [JSON_INPUT] [--bypass] [--dir PATH]
  claude-guardrails doctor [--dir PATH]
  claude-guardrails presets
  claude-guardrails hook pre|session-start|instructions-log   (what settings.json calls)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import guard

PRESETS_DIR = Path(__file__).with_name("presets")
GUARD_SRC = Path(__file__).with_name("guard.py")
HOOK_REL = ".claude/hooks/guard.py"
CONFIG_REL = ".claude/guardrails.json"
SETTINGS_REL = ".claude/settings.json"
PRE_MATCHER = "Read|Grep|Glob|Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit|mcp__.*"
HOOK_EVENTS = {
    "PreToolUse": ("pre", PRE_MATCHER),
    "SessionStart": ("session-start", None),
    "InstructionsLoaded": ("instructions-log", None),
}

# ------------------------------------------------------------------ output
_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


GREEN, RED, YELLOW, DIM, BOLD = "32", "31", "33", "2", "1"
BADGE = {"deny": paint(" DENY ", "41;97;1"), "ask": paint(" ASK ", "43;30;1"), "allow": paint(" ALLOW ", "42;30;1")}


def presets() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(PRESETS_DIR.glob("*.json"))}


def hook_command(mode: str) -> str:
    return f'python "$CLAUDE_PROJECT_DIR/{HOOK_REL}" {mode}'


# -------------------------------------------------------------------- init
def merge_settings(settings: dict) -> list[str]:
    """Add our hooks to a settings dict in place. Returns the events that changed."""
    hooks = settings.setdefault("hooks", {})
    changed = []
    for event, (mode, matcher) in HOOK_EVENTS.items():
        groups = hooks.setdefault(event, [])
        cmd = hook_command(mode)
        if any(cmd == h.get("command") for g in groups for h in g.get("hooks", [])):
            continue
        group: dict = {"hooks": [{"type": "command", "command": cmd, "timeout": 30}]}
        if matcher:
            group = {"matcher": matcher, **group}
        groups.append(group)
        changed.append(event)
    return changed


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    available = presets()
    if args.preset not in available:
        print(paint(f"Unknown preset '{args.preset}'. Pick one of: {', '.join(available)}", RED))
        return 2
    (root / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)

    shutil.copyfile(GUARD_SRC, root / HOOK_REL)
    print(f"{paint('✓', GREEN)} hook script   {HOOK_REL}")

    cfg_path = root / CONFIG_REL
    if cfg_path.exists() and not args.force:
        print(f"{paint('•', YELLOW)} config kept   {CONFIG_REL} (exists; --force to replace)")
    else:
        cfg = {k: v for k, v in available[args.preset].items() if not k.startswith("_")}
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        print(f"{paint('✓', GREEN)} config        {CONFIG_REL} (preset: {args.preset})")

    settings_path = root / SETTINGS_REL
    settings = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except ValueError:
            print(paint(f"✗ {SETTINGS_REL} is not valid JSON; fix it and re-run.", RED))
            return 1
    changed = merge_settings(settings)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    what = ", ".join(changed) if changed else "already wired"
    print(f"{paint('✓', GREEN)} hooks         {SETTINGS_REL} ({what})")
    print()
    print(f"Try it:  {paint('claude-guardrails explain Read ' + chr(39) + json.dumps({'file_path': '.env'}) + chr(39), BOLD)}")
    print("Then restart Claude Code in this folder so it picks up the hooks.")
    return 0


# ----------------------------------------------------------------- explain
def cmd_explain(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    try:
        if args.tool == "-":
            data = json.load(sys.stdin)
            if not isinstance(data, dict) or not isinstance(data.get("tool_name"), str) or not data["tool_name"].strip():
                raise ValueError("payload must be an object with a non-empty tool_name")
            if not isinstance(data.get("tool_input", {}), dict):
                raise ValueError("tool_input must be an object")
        else:
            ti = json.loads(args.input) if args.input else {}
            data = {"tool_name": args.tool, "tool_input": ti}
    except ValueError as exc:
        print(paint(f"Input is not a valid tool call: {exc}", RED))
        return 2
    os.environ.setdefault("CLAUDE_PROJECT_DIR", str(root))
    cfg = guard.load_config(root)
    if args.bypass:
        data["permission_mode"] = "bypassPermissions"
    verdict = guard.evaluate(data, cfg, root)
    print(f"{paint(data['tool_name'], BOLD)} {paint(json.dumps(data.get('tool_input', {})), DIM)}")
    if not verdict:
        print(f"  {paint(' PASS ', '47;30;1')} no rule matched; Claude Code's normal permissions apply")
        return 0
    decision, reason, rule = verdict
    print(f"  {BADGE[decision]} {reason}")
    print(f"  {paint('rule: ' + rule, DIM)}")
    return 0


# ------------------------------------------------------------------ doctor
def cmd_doctor(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        print(f"{paint('✓', GREEN) if good else paint('✗', RED)} {text}")

    line(sys.version_info >= (3, 10), f"python {sys.version.split()[0]}")
    hook = root / HOOK_REL
    if hook.is_file():
        src = hook.read_text(encoding="utf-8")
        current = f'__version__ = "{guard.__version__}"' in src
        line(current, f"{HOOK_REL} present" + ("" if current else f" but older than {guard.__version__} (re-run init)"))
    else:
        line(False, f"{HOOK_REL} missing (run: claude-guardrails init)")
    cfg_path = root / CONFIG_REL
    if cfg_path.is_file():
        try:
            json.loads(cfg_path.read_text(encoding="utf-8"))
            line(True, f"{CONFIG_REL} parses")
        except ValueError as exc:
            line(False, f"{CONFIG_REL} is not valid JSON ({exc})")
    else:
        print(f"{paint('•', YELLOW)} {CONFIG_REL} not found; built-in defaults apply")
    settings_path = root / SETTINGS_REL
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except ValueError:
        settings = {}
        line(False, f"{SETTINGS_REL} is not valid JSON")
    for event, (mode, _) in HOOK_EVENTS.items():
        groups = (settings.get("hooks") or {}).get(event) or []
        wired = any(f"guard.py\" {mode}" in str(h.get("command")) or f"hook {mode}" in str(h.get("command"))
                    for g in groups for h in g.get("hooks", []))
        line(wired, f"{event} hook wired")
    probe = guard.evaluate({"tool_name": "Read", "tool_input": {"file_path": ".env"}}, guard.load_config(root), root)
    line(bool(probe and probe[0] == "deny"), "live probe: reading .env is denied")
    print()
    print(paint("All good. Your agent is on a leash.", GREEN) if ok else paint("Fix the ✗ lines above.", RED))
    return 0 if ok else 1


# ----------------------------------------------------------------- presets
def cmd_presets(_: argparse.Namespace) -> int:
    for name, cfg in presets().items():
        rules = sum(len(v or []) for v in (cfg.get("tools") or {}).values())
        print(f"{paint(name.ljust(11), BOLD)} {cfg.get('_about', '')}")
        print(paint(f"{'':11} {len(cfg.get('protected_paths') or [])} protected paths · {rules} tool rules"
                    f"{' · PII guard' if cfg.get('pii') else ''}", DIM))
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    return guard.main([args.mode])


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="claude-guardrails", description="Config-driven safety hooks for Claude Code.")
    p.add_argument("--version", action="version", version=f"claude-guardrails {guard.__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="install the hook, a preset config and settings.json wiring")
    s.add_argument("--preset", default="solo")
    s.add_argument("--dir", default=".")
    s.add_argument("--force", action="store_true", help="replace an existing guardrails.json")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("explain", help="show what the guard decides for one tool call")
    s.add_argument("tool", help="tool name, or - to read a full hook payload from stdin")
    s.add_argument("input", nargs="?", default="{}")
    s.add_argument("--bypass", action="store_true", help="simulate bypassPermissions mode")
    s.add_argument("--dir", default=".")
    s.set_defaults(fn=cmd_explain)

    s = sub.add_parser("doctor", help="check the install in a project")
    s.add_argument("--dir", default=".")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("presets", help="list the built-in presets")
    s.set_defaults(fn=cmd_presets)

    s = sub.add_parser("hook", help="run a hook mode (reads the hook payload on stdin)")
    s.add_argument("mode", choices=list(guard.MODES))
    s.set_defaults(fn=cmd_hook)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
