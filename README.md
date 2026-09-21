# claude-cache-gate

A Claude Code hook that tells you when a session's prompt cache has expired,
and gates the first prompt you send afterwards so you can decide whether to
pay for the re-write or start a fresh session.

## Why

Claude Code keeps your conversation in Anthropic's prompt cache. The cache
TTL (1 hour on Claude Code sessions) resets on every request. If you leave a
session idle past the TTL and come back, the **next turn re-writes the entire
context** as a cache write — for a 100K+ token session that is the single most
expensive turn you can send. After that one turn the cache is warm again.

Nothing in the UI tells you this happened. This hook does.

## What it does

`cache_gate.py` runs on `UserPromptSubmit`. It reads the session transcript,
finds the last assistant turn, and computes idle time:

| idle time | behaviour |
|---|---|
| < 50 min | silent |
| 50–60 min | note: `Prompt cache warm for ~8m more (110K context).` |
| ≥ 60 min, first submit | **blocked** — prompt is dropped and you see: `Prompt cache expired (idle 1226m ≥ 60m TTL): the next turn re-writes ~110K tokens of context. Blocked once — re-send to pay it, or /handoff and start a fresh session.` |
| ≥ 60 min, second submit | proceeds, with a `…Proceeding.` note |

The block fires once per expiry (a marker in `/tmp` keyed by session id and
last-turn timestamp), so after the turn runs and the cache is warm again, the
gate re-arms for the next idle period.

It always exits 0 and never writes to the model's context — messages go to
you via `systemMessage`.

### Listing all sessions

Before choosing a session to resume, from the project directory:

```
$ python3 cache_gate.py --list
session                               last turn            idle     ctx  cache
0a13365f-0557-5c3b-ae95-86fb1428e37c  2026-09-21 08:10       0m     43K  60m left
4fcdf2ce-a667-46d3-864f-e321f04f74be  2026-09-20 11:44    1226m    110K  EXPIRED
```

## Setup — for humans

```bash
git clone https://github.com/avaxML/claude-cache-gate.git ~/Projects/claude-cache-gate
~/Projects/claude-cache-gate/install.sh
```

`install.sh` adds a `UserPromptSubmit` entry to `~/.claude/settings.json`
pointing at the cloned script (idempotent — re-run after moving the clone).
Restart Claude Code. That's it; no dependencies beyond Python 3.

To pick a different settings file: `CLAUDE_SETTINGS=path/to/settings.json ./install.sh`.

Tune with environment variables (set them where Claude Code is launched):

| variable | default | meaning |
|---|---|---|
| `CLAUDE_CACHE_TTL_MIN` | `60` | cache TTL in minutes. Use `5` if your setup runs the 5-minute cache. |
| `CLAUDE_CACHE_WARN_MIN` | `50` | idle minutes after which the "warm for ~Xm" heads-up shows |

To make it warn-only instead of blocking, edit `hook()` in `cache_gate.py` and
replace the `{"decision": "block", ...}` print with a `systemMessage`.

Uninstall: remove the `cache_gate.py` entry from `hooks.UserPromptSubmit` in
`~/.claude/settings.json`.

## Setup — for agents

If you are an AI agent installing this on a user's behalf:

1. Clone to a stable path: `git clone https://github.com/avaxML/claude-cache-gate.git <path>`.
2. Run `<path>/install.sh`. It edits `~/.claude/settings.json` in place with
   Python's `json` module, preserving every other key. Do not hand-edit the
   file in parallel.
3. Verify without restarting: feed the hook a synthetic expired transcript and
   expect a `block` decision on the first call and `Proceeding` on the second:
   ```bash
   python3 <path>/tests/test_cache_gate.py
   ```
   All four tests must print `ok`. Exit code is the source of truth, not grep.
4. Tell the user to restart Claude Code — hooks load at session start.

Hook contract, if you need to reason about it:

- **Input** (stdin JSON from Claude Code): `session_id`, `transcript_path`.
- **Output** (stdout JSON, exit 0 always): either nothing, `{"systemMessage": "..."}`
  (shown to the user, not added to context), or
  `{"decision": "block", "reason": "..."}` (prompt dropped, reason shown).
- **State**: `/tmp/claude-cache-gate-<session_id>` containing the ISO
  timestamp of the last assistant turn the block fired for.
- **Transcript parsing**: JSONL; uses lines with `"type": "assistant"` and a
  `message.usage` object; skips `isSidechain` lines; context size is
  `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` of
  the last such line.

Do not add this hook to a project-level `.claude/settings.json` that is
committed to a repo — the command contains an absolute path on the user's
machine. It belongs in the user-level settings file.

## Development

```bash
python3 tests/test_cache_gate.py   # or: python3 -m pytest tests/
```

CI runs the same on every push and PR. `main` is protected: changes land via
pull request with green CI, no force pushes.

## License

MIT
