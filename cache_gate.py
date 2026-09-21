#!/usr/bin/env python3
"""Gate the first prompt submitted after a Claude Code or Codex cache expiry.

Claude Code exposes usage records in its transcript, so its cache age comes
from the last assistant response. Codex transcript files are not a stable hook
interface, so the Codex adapter records completed turns through the Stop event.

The hook fails open. Invalid input, unreadable transcripts, and state write
failures never trap a session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULTS = {
    "claude": {"ttl": 60, "warn": 50},
    "codex": {"ttl": 30, "warn": 25},
}


def _env_minutes(client: str, name: str) -> int:
    default = DEFAULTS[client][name.lower()]
    specific = f"CACHE_GATE_{client.upper()}_{name.upper()}_MIN"
    generic = f"CACHE_GATE_{name.upper()}_MIN"
    legacy = f"CLAUDE_CACHE_{name.upper()}_MIN" if client == "claude" else ""
    value = os.environ.get(specific) or os.environ.get(generic)
    if legacy:
        value = value or os.environ.get(legacy)
    try:
        return max(1, int(value)) if value is not None else default
    except ValueError:
        return default


def _state_dir() -> Path:
    configured = os.environ.get("CACHE_GATE_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "cache-gate-hook"


def _state_path(client: str, session_id: str) -> Path:
    key = hashlib.sha256(f"{client}:{session_id}".encode()).hexdigest()
    return _state_dir() / f"{key}.json"


def _read_state(client: str, session_id: str) -> dict[str, Any]:
    try:
        value = json.loads(_state_path(client, session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(client: str, session_id: str, state: dict[str, Any]) -> None:
    directory = _state_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    target = _state_path(client, session_id)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, target)


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _claude_last_turn(transcript: str) -> tuple[datetime, int] | None:
    timestamp: str | None = None
    context_tokens = 0
    try:
        with open(transcript, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if '"assistant"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if record.get("type") != "assistant" or record.get("isSidechain"):
                    continue
                message = record.get("message") or {}
                usage = message.get("usage") if isinstance(message, dict) else None
                if not isinstance(usage, dict):
                    continue
                timestamp = record.get("timestamp") or timestamp
                context_tokens = sum(
                    int(usage.get(field) or 0)
                    for field in (
                        "input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                )
    except OSError:
        return None
    when = _parse_timestamp(timestamp)
    return (when, context_tokens) if when is not None else None


def _idle_minutes(timestamp: float) -> float:
    return max(0.0, (time.time() - timestamp) / 60)


def _prompt_hash(payload: dict[str, Any]) -> str:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        prompt = ""
    return hashlib.sha256(prompt.encode()).hexdigest()


def _emit_gate(
    client: str,
    session_id: str,
    state: dict[str, Any],
    expiry_key: str,
    prompt_hash: str,
    message: str,
) -> None:
    if state.get("acknowledged_expiry") == expiry_key:
        return
    if (
        state.get("blocked_expiry") == expiry_key
        and state.get("blocked_prompt_hash") == prompt_hash
    ):
        state["acknowledged_expiry"] = expiry_key
        state.pop("blocked_expiry", None)
        state.pop("blocked_prompt_hash", None)
        try:
            _write_state(client, session_id, state)
        except OSError:
            pass
        print(json.dumps({"systemMessage": message + " Repeated prompt accepted."}))
        return

    state["blocked_expiry"] = expiry_key
    state["blocked_prompt_hash"] = prompt_hash
    try:
        _write_state(client, session_id, state)
    except OSError:
        print(
            json.dumps(
                {
                    "systemMessage": (
                        message
                        + " Cache Gate could not save its state, so the prompt was allowed."
                    )
                }
            )
        )
        return
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    message
                    + " The prompt was not sent. Submit the exact same prompt again "
                    "to continue, or start a fresh session."
                ),
            }
        )
    )


def _claude_hook(payload: dict[str, Any]) -> None:
    if payload.get("hook_event_name") not in (None, "UserPromptSubmit"):
        return
    transcript = payload.get("transcript_path")
    session_id = str(payload.get("session_id") or "unknown")
    if not isinstance(transcript, str) or not os.path.isfile(transcript):
        return
    turn = _claude_last_turn(transcript)
    if turn is None:
        return

    when, context_tokens = turn
    ttl = _env_minutes("claude", "ttl")
    warn_at = min(ttl, _env_minutes("claude", "warn"))
    idle = _idle_minutes(when.timestamp())
    context_k = context_tokens // 1000
    if idle < ttl:
        if idle >= warn_at:
            print(
                json.dumps(
                    {
                        "systemMessage": (
                            f"Prompt cache warm for about {ttl - idle:.0f}m more "
                            f"({context_k}K context)."
                        )
                    }
                )
            )
        return

    message = (
        f"Prompt cache expired after {idle:.0f}m idle "
        f"(configured Claude Code TTL: {ttl}m). The next turn will rewrite "
        f"about {context_k}K tokens of context."
    )
    state = _read_state("claude", session_id)
    _emit_gate(
        "claude",
        session_id,
        state,
        when.isoformat(),
        _prompt_hash(payload),
        message,
    )


def _codex_hook(payload: dict[str, Any]) -> None:
    event = payload.get("hook_event_name")
    session_id = str(payload.get("session_id") or "unknown")
    state = _read_state("codex", session_id)

    if event == "Stop":
        state["last_model_activity"] = time.time()
        state.pop("acknowledged_expiry", None)
        state.pop("blocked_expiry", None)
        state.pop("blocked_prompt_hash", None)
        try:
            _write_state("codex", session_id, state)
        except OSError:
            pass
        print("{}")
        return

    try:
        last_activity = float(state["last_model_activity"])
    except (KeyError, TypeError, ValueError):
        if event == "SessionStart":
            print(
                json.dumps(
                    {
                        "systemMessage": (
                            "Prompt cache is cold for this Codex session. "
                            "No completed model turn has been recorded yet."
                        )
                    }
                )
            )
        return

    ttl = _env_minutes("codex", "ttl")
    warn_at = min(ttl, _env_minutes("codex", "warn"))
    idle = _idle_minutes(last_activity)
    if event == "SessionStart":
        status = (
            f"likely expired after {idle:.0f}m idle"
            if idle >= ttl
            else f"warm for at least about {ttl - idle:.0f}m more"
        )
        print(json.dumps({"systemMessage": f"Prompt cache is {status}."}))
        return
    if event != "UserPromptSubmit":
        return
    if idle < ttl:
        if idle >= warn_at:
            print(
                json.dumps(
                    {
                        "systemMessage": (
                            f"Prompt cache likely warm for about {ttl - idle:.0f}m more."
                        )
                    }
                )
            )
        return

    message = (
        f"Prompt cache likely expired after {idle:.0f}m idle "
        f"(Codex minimum TTL: {ttl}m)."
    )
    _emit_gate(
        "codex",
        session_id,
        state,
        str(last_activity),
        _prompt_hash(payload),
        message,
    )


def _detect_client(payload: dict[str, Any]) -> str:
    if payload.get("hook_event_name") in ("SessionStart", "Stop"):
        return "codex"
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str):
        try:
            with open(transcript, encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if '"type":"token_usage_record"' in line:
                        return "codex"
                    if '"assistant"' in line:
                        return "claude"
        except OSError:
            pass
    return "claude"


def _list_claude_sessions() -> None:
    slug = os.getcwd().replace("/", "-")
    project = Path.home() / ".claude" / "projects" / slug
    rows: list[tuple[datetime, str, int]] = []
    if project.is_dir():
        for transcript in project.glob("*.jsonl"):
            turn = _claude_last_turn(str(transcript))
            if turn is not None:
                when, context_tokens = turn
                rows.append((when, transcript.stem, context_tokens))
    if not rows:
        print(f"No Claude Code transcripts found for {os.getcwd()}")
        return
    rows.sort(reverse=True)
    ttl = _env_minutes("claude", "ttl")
    print(f"{'session':36}  {'last turn':16}  {'idle':>7}  {'ctx':>6}  cache")
    for when, session_id, context_tokens in rows[:20]:
        idle = _idle_minutes(when.timestamp())
        remaining = ttl - idle
        status = "EXPIRED" if remaining <= 0 else f"{remaining:.0f}m left"
        print(
            f"{session_id:36}  {when.astimezone():%Y-%m-%d %H:%M}  "
            f"{idle:6.0f}m  {context_tokens // 1000:5d}K  {status}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("claude", "codex"))
    parser.add_argument("--list", action="store_true", help="list Claude Code sessions")
    args = parser.parse_args()
    if args.list:
        _list_claude_sessions()
        return
    payload = _read_payload()
    client = args.client or _detect_client(payload)
    if client == "codex":
        _codex_hook(payload)
    else:
        _claude_hook(payload)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
