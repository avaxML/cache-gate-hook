# cache-gate-hook

A local hook for Codex and Claude Code that warns when a session's prompt
cache is near expiry. After expiry, it blocks the first submitted prompt and
requires the exact same prompt to be submitted again.

The pause makes the cost visible before a long session rewrites its cached
context. You can accept that rewrite or start a fresh session.

## Behavior

| client | default TTL | activity source | behavior after expiry |
|---|---:|---|---|
| Claude Code | 60 minutes | Last assistant usage record in the session transcript | Blocks once and reports the approximate context size |
| Codex | 30 minutes | The last completed turn, recorded by a `Stop` hook | Blocks once and reports that the cache likely expired |

The same safety rule applies to both clients:

1. The first prompt after expiry is blocked.
2. Submitting the exact same prompt again allows it through.
3. Submitting a different prompt replaces the blocked prompt, so the new text
   must also be submitted twice.
4. A completed turn re-arms the gate for the next expiry.

Prompt text is stored only as a SHA-256 hash. Session state lives in
`~/.cache/cache-gate-hook/` by default.

The TTLs are conservative indicators, not proof of a cache miss. OpenAI states
that GPT-5.6 and later cached prefixes can be reused for at least 30 minutes
after the latest write or reuse, and may remain available longer. Anthropic
documents both five-minute and one-hour prompt cache durations. See the
[OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
and the
[Anthropic prompt caching guide](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

## Install for humans

Clone the repository to a stable path. The hook configuration contains the
absolute path to `cache_gate.py`.

```bash
git clone https://github.com/avaxML/cache-gate-hook.git ~/Projects/cache-gate-hook
cd ~/Projects/cache-gate-hook
```

For Claude Code:

```bash
./install.sh claude
```

This adds one `UserPromptSubmit` hook to `~/.claude/settings.json`. Restart
Claude Code after installation.

For Codex:

```bash
./install.sh codex
```

This adds `SessionStart`, `UserPromptSubmit`, and `Stop` hooks to
`~/.codex/hooks.json`. Start or resume a Codex session, open `/hooks`, review
the definitions, and trust them. Codex requires trust for each new or changed
hook definition. The
[Codex hooks documentation](https://learn.chatgpt.com/docs/hooks)
describes this review flow.

To install both clients:

```bash
./install.sh all
```

The installer is idempotent. It replaces older `cache_gate.py` entries and
preserves unrelated settings and hooks.

## Install for agents

When an agent installs the hook for a user:

1. Clone `https://github.com/avaxML/cache-gate-hook.git` to a stable path.
2. Run `install.sh claude`, `install.sh codex`, or `install.sh all` based on the
   user's requested clients. Running the installer changes user-level agent
   configuration.
3. Do not copy these entries into a repository's `.claude/` or `.codex/`
   directory. The generated command contains a machine-specific absolute path.
4. Run the verification commands below. Treat their exit codes as the result.
5. For Claude Code, tell the user to restart the client. For Codex, tell the
   user to review and trust the definitions in `/hooks`.

The installer accepts alternate files for isolated or managed setups:

```bash
CLAUDE_SETTINGS=/path/to/settings.json ./install.sh claude
CODEX_HOOKS=/path/to/hooks.json ./install.sh codex
```

Hook contracts:

- Claude Code invokes `UserPromptSubmit` with `session_id`, `prompt`, and
  `transcript_path`. The hook reads the most recent non-sidechain assistant
  usage record.
- Codex invokes `SessionStart`, `UserPromptSubmit`, and `Stop`. The hook records
  activity on `Stop` because Codex documents its transcript format as unstable
  for hook integrations.
- Both clients receive either no output, a `systemMessage`, or a
  `{"decision":"block","reason":"..."}` response.
- The hook catches unexpected errors and exits successfully. Cache visibility
  must not trap an agent session.

Official hook references:

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [Codex hooks reference](https://learn.chatgpt.com/docs/hooks)

## Configuration

| variable | default | purpose |
|---|---:|---|
| `CACHE_GATE_CLAUDE_TTL_MIN` | `60` | Claude Code cache TTL in minutes |
| `CACHE_GATE_CLAUDE_WARN_MIN` | `50` | Claude Code warning threshold |
| `CACHE_GATE_CODEX_TTL_MIN` | `30` | Codex minimum cache TTL in minutes |
| `CACHE_GATE_CODEX_WARN_MIN` | `25` | Codex warning threshold |
| `CACHE_GATE_TTL_MIN` | unset | Override the TTL for either client |
| `CACHE_GATE_WARN_MIN` | unset | Override the warning threshold for either client |
| `CACHE_GATE_STATE_DIR` | `~/.cache/cache-gate-hook` | Override the private state directory |

Client-specific variables take precedence over the generic variables. The old
`CLAUDE_CACHE_TTL_MIN` and `CLAUDE_CACHE_WARN_MIN` names remain supported for
existing Claude Code installations.

If your Claude setup uses the standard five-minute cache, set both Claude
values where Claude Code starts:

```bash
export CACHE_GATE_CLAUDE_TTL_MIN=5
export CACHE_GATE_CLAUDE_WARN_MIN=4
```

## List Claude Code sessions

From a project directory:

```bash
python3 cache_gate.py --list
```

The command lists up to 20 Claude Code transcripts for that project with their
last turn, idle time, context size, and estimated cache state. Codex session
status appears through its `SessionStart` hook instead.

## Uninstall

Remove commands containing `cache_gate.py` from these user-level files:

- Claude Code: `~/.claude/settings.json` under `hooks.UserPromptSubmit`
- Codex: `~/.codex/hooks.json` under `hooks.SessionStart`,
  `hooks.UserPromptSubmit`, and `hooks.Stop`

You may also remove `~/.cache/cache-gate-hook/`. It contains only per-session
timestamps and prompt hashes.

## Development

```bash
python3 -B -m py_compile cache_gate.py tests/test_cache_gate.py
python3 -B tests/test_cache_gate.py
bash -n install.sh
```

The direct test runner uses only the Python standard library. CI runs the same
checks on every push and pull request.

## License

MIT
