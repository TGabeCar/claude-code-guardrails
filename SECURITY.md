# Security policy

## Reporting a bypass

If you find a way to get a tool call past a rule that should catch it, please report it privately through **GitHub's private vulnerability reporting** on this repo (Security tab → "Report a vulnerability"), not in a public issue.

Include the config, the tool name and input, and what you expected versus what happened. `claude-guardrails explain` output is ideal.

## Scope

In scope: protected-path checks, tool-rule matching, the read-only shell check, bypass-mode handling, and the PII guard.

Known and documented limits (see "Honest limits" in the README): string checks can't stop code that builds a path at runtime. Hooks are one layer; use OS permissions too.

## Supported versions

The latest minor release gets fixes.
