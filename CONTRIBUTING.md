# Contributing

Thanks for helping keep agents on a leash.

## Ground rules

- **The hook stays dependency-free.** `guardrails/guard.py` is copied into people's projects and must run on plain Python 3.10+.
- **Fail open, but never on a protected path.** A bug in the guard must not brick a session, and the protected-path check must stay plain string logic.
- **Every decision needs a reason** Claude can read and act on.
- **Tests for every rule change.** Add a case to `tests/`; CI runs Linux and Windows.

## Setup

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

## Good first contributions

- A new preset for a common stack (with tests that compile its regexes)
- More read-only commands in `READ_ONLY_CMDS`, with a test proving the write variants still ask
- Docs: a recipe for your MCP server's tool names

Open an issue first for anything bigger, so we can agree on the shape.

## Security issues

Please don't open a public issue for a bypass. See [SECURITY.md](SECURITY.md).
