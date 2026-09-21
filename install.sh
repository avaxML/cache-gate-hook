#!/usr/bin/env bash
# Register cache_gate.py as a UserPromptSubmit hook in ~/.claude/settings.json.
# Idempotent: re-running replaces an existing claude-cache-gate entry.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
HOOK="python3 $HERE/cache_gate.py"
mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
python3 - "$SETTINGS" "$HOOK" <<'PY'
import json, sys
path, cmd = sys.argv[1], sys.argv[2]
s = json.load(open(path))
groups = s.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
groups[:] = [g for g in groups
             if not any("cache_gate.py" in h.get("command", "") for h in g.get("hooks", []))]
groups.append({"hooks": [{"type": "command", "command": cmd}]})
json.dump(s, open(path, "w"), indent=2)
open(path, "a").write("\n")
print(f"registered: {cmd}\nin: {path}\nrestart Claude Code to activate.")
PY
