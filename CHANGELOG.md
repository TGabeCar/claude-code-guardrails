# Changelog

## 0.2.0 (2026-11-25)

- **New `claude-guardrails` CLI**
  - `init --preset solo|team|production`: copies the hook, writes the config, and merges `.claude/settings.json` without clobbering it (safe to re-run)
  - `explain TOOL JSON [--bypass]`: shows exactly which rule fires and why, before Claude ever hits it
  - `doctor`: checks the install, hook wiring, config validity and hook version, then runs a live probe
  - `presets`: lists the built-in presets
- **Three presets:** `solo`, `team`, `production` (money movement denied, infra changes denied, every live write asks)
- `guard.evaluate()` is the importable decision function; the hook output is unchanged
- Animated demo and social preview image

## 0.1.0 (2026-11-23)

- First release: protected paths, deny/allow/ask tool rules with `when` filters, read-only shell pass, ask-to-deny in bypass mode, PII-in-logs guard, SessionStart context, instruction-load audit log
