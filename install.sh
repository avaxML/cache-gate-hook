#!/usr/bin/env bash
# Register cache-gate-hook for Claude Code, Codex, or both clients.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-claude}"
CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
CODEX_HOOKS="${CODEX_HOOKS:-$HOME/.codex/hooks.json}"

case "$TARGET" in
  claude|codex|all) ;;
  *)
    echo "usage: $0 [claude|codex|all]" >&2
    exit 2
    ;;
esac

python3 - "$TARGET" "$CLAUDE_SETTINGS" "$CODEX_HOOKS" "$HERE/cache_gate.py" <<'PY'
import json
import os
import sys
from pathlib import Path

target, claude_settings, codex_hooks, script = sys.argv[1:]


def load(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise SystemExit(f"expected a JSON object in {path}")
    return value


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def remove_cache_gate(groups):
    return [
        group
        for group in groups
        if not any(
            any(
                name in hook.get("command", "")
                for name in ("cache_gate.py", "cache_status.py")
            )
            for hook in group.get("hooks", [])
            if isinstance(hook, dict)
        )
    ]


def command(client):
    return f'/usr/bin/python3 "{script}" --client {client}'


def install_claude(path):
    settings = load(path)
    hooks = settings.setdefault("hooks", {})
    groups = remove_cache_gate(hooks.setdefault("UserPromptSubmit", []))
    groups.append(
        {"hooks": [{"type": "command", "command": command("claude"), "timeout": 3}]}
    )
    hooks["UserPromptSubmit"] = groups
    save(path, settings)
    print(f"Claude Code hook registered in {path}")


def install_codex(path):
    settings = load(path)
    hooks = settings.setdefault("hooks", {})
    definitions = {
        "SessionStart": {"matcher": "startup|resume|clear|compact"},
        "UserPromptSubmit": {},
        "Stop": {},
    }
    for event, group in definitions.items():
        groups = remove_cache_gate(hooks.setdefault(event, []))
        group = dict(group)
        group["hooks"] = [
            {
                "type": "command",
                "command": command("codex"),
                "timeout": 3,
                "statusMessage": "Checking prompt cache",
            }
        ]
        groups.append(group)
        hooks[event] = groups
    settings.setdefault(
        "description",
        "Warn and block once when a session prompt cache has expired.",
    )
    save(path, settings)
    print(f"Codex hooks registered in {path}")
    print("Open /hooks in Codex and trust the new hook definitions.")


if target in ("claude", "all"):
    install_claude(Path(claude_settings).expanduser())
if target in ("codex", "all"):
    install_codex(Path(codex_hooks).expanduser())
PY
