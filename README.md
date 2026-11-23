# claude-code-guardrails

[![CI](https://github.com/TGabeCar/claude-code-guardrails/actions/workflows/ci.yml/badge.svg)](https://github.com/TGabeCar/claude-code-guardrails/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

**Config-driven safety hooks for [Claude Code](https://docs.claude.com/en/docs/claude-code).** One dependency-free Python script, one JSON file. It keeps an agent out of your secrets, stops it from sending mail or wiping live data without a human, and keeps personal data out of committed logs.

Born from running Claude Code as a daily operations agent (scheduled jobs, live APIs, real money) where "please be careful" in a prompt is not a control.

```text
> Read ~/.ssh/id_ed25519

⏺ Read(~/.ssh/id_ed25519)
  ⎿  Blocked: '~/.ssh/' is a protected path. Claude never reads, lists, searches or touches it.
```

## What it does

| Guard | Hook | Behavior |
|---|---|---|
| **Protected paths** | `PreToolUse` | Denies any Read/Grep/Glob/Write/Edit, shell command or MCP call that touches a listed path or glob (`.env`, `~/.ssh/`, `*.pem`, …). Checks home-expanded and Windows paths too. |
| **Tool rules** | `PreToolUse` | `deny` / `allow` / `ask` per tool name glob (`mcp__*__send_email`), optionally narrowed by input fields (`"when": {"endpoint": "^GET "}`). |
| **Read-only shell pass** | `PreToolUse` | `unless_read_only_shell` lets `git log`, `grep`, `cat` through a remote-command tool and still asks for anything that could write. |
| **Bypass-mode safety** | `PreToolUse` | In `bypassPermissions` mode (unattended runs) nobody can answer an `ask`, so it becomes a `deny`. |
| **PII-in-logs guard** | `PreToolUse` | Blocks writes that *add* personal email addresses to files you mark (e.g. `logs/*.md`). Allowed domains are configurable; addresses already there don't trip it. |
| **Session start check** | `SessionStart` | Tells Claude when the session started in the wrong folder, how many tracked files changed outside a commit, and how many always-loaded rules exist. |
| **Instruction load log** | `InstructionsLoaded` | Appends one line per loaded `CLAUDE.md` / rule file, so you can audit what the agent was actually told. |

Fails open on its own errors (it never bricks your session), and every decision carries a reason Claude can read and act on.

## Install

1. Copy `guardrails/guard.py` into your project, e.g. `.claude/hooks/guard.py`.
2. Copy [`examples/guardrails.json`](examples/guardrails.json) to `.claude/guardrails.json` and edit it.
3. Wire the hooks in `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Read|Grep|Glob|Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit|mcp__.*",
        "hooks": [{ "type": "command", "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py\" pre" }] }
    ],
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py\" session-start" }] }
    ],
    "InstructionsLoaded": [
      { "hooks": [{ "type": "command", "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py\" instructions-log" }] }
    ]
  }
}
```

No config file? It still runs with safe defaults (`.env`, `~/.ssh/`, `~/.aws/`, `*.pem`, `id_rsa`).

## Config reference

```jsonc
{
  "protected_paths": ["~/.ssh/", ".env", "*.pem"],   // substring-on-path-boundary, or glob if it has * ? [
  "tools": {
    "deny":  [{ "match": "mcp__*__send_email", "reason": "Humans send email." }],
    "allow": [{ "match": "mcp__*__execute_api", "when": { "endpoint": "^GET " } }],
    "ask":   [{ "match": "mcp__*__run_command", "unless_read_only_shell": true }]
  },
  "bypass_ask_becomes_deny": true,
  "pii": { "paths": ["logs/*.md"], "allowed_email_domains": ["yourcompany.com"] },
  "session_start": { "expected_cwd": null, "warn_uncommitted": true, "rules_dir": ".claude/rules" },
  "instructions_log": { "dir": ".claude/logs" }
}
```

- **Order:** protected paths → `deny` → `allow` → `ask` → PII. The first match wins, so a narrow `allow` (GET only) can sit above a broad `ask`.
- **`when`:** every field regex must match the tool input for the rule to apply.
- **Config lookup:** `$GUARDRAILS_CONFIG`, else `<project>/.claude/guardrails.json`, else defaults.

## Honest limits

Hooks are a seatbelt, not a sandbox. A determined script can build a protected path at runtime (`"~/.s" + "sh"`) and slip past string checks. Pair this with OS-level permissions and keep real secrets out of reach of the agent's user.

## Development

```bash
python -m pip install pytest
python -m pytest -q
```

CI runs the suite on Linux and Windows across Python 3.10–3.13, plus a gitleaks secret scan.

## License

MIT
