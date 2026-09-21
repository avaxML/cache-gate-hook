"""Run with: python3 tests/test_cache_gate.py"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "cache_gate.py"
INSTALLER = ROOT / "install.sh"


def transcript(minutes_ago: int, context_tokens: int = 110_000) -> Path:
    timestamp = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    record = {
        "type": "assistant",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "message": {
            "model": "claude-opus-5",
            "usage": {
                "input_tokens": 1_000,
                "cache_read_input_tokens": context_tokens - 1_000,
            },
        },
    }
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    handle.write("not json\n")
    handle.write(json.dumps({**record, "isSidechain": True}) + "\n")
    handle.write(json.dumps(record) + "\n")
    handle.close()
    return Path(handle.name)


def run_hook(
    client: str,
    payload: dict[str, object] | str,
    state_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, object] | None:
    environment = dict(os.environ, CACHE_GATE_STATE_DIR=str(state_dir))
    environment.update(extra_env or {})
    input_text = payload if isinstance(payload, str) else json.dumps(payload)
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--client", client],
        input=input_text,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout) if process.stdout.strip() else None


def seed_codex_state(state_dir: Path, session_id: str, minutes_ago: int) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"codex:{session_id}".encode()).hexdigest()
    (state_dir / f"{key}.json").write_text(
        json.dumps({"last_model_activity": time.time() - minutes_ago * 60}),
        encoding="utf-8",
    )


def test_malformed_input_fails_open() -> None:
    with tempfile.TemporaryDirectory() as directory:
        assert run_hook("claude", "not json", Path(directory)) is None
        assert run_hook("codex", "[]", Path(directory)) is None


def test_claude_warm_is_silent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        payload = {
            "session_id": "claude-warm",
            "hook_event_name": "UserPromptSubmit",
            "transcript_path": str(transcript(5)),
            "prompt": "continue",
        }
        assert run_hook("claude", payload, Path(directory)) is None


def test_claude_near_expiry_warns() -> None:
    with tempfile.TemporaryDirectory() as directory:
        payload = {
            "session_id": "claude-warning",
            "hook_event_name": "UserPromptSubmit",
            "transcript_path": str(transcript(55)),
            "prompt": "continue",
        }
        output = run_hook("claude", payload, Path(directory))
        assert output and "warm for" in str(output["systemMessage"])


def test_claude_requires_the_exact_repeated_prompt() -> None:
    with tempfile.TemporaryDirectory() as directory:
        state_dir = Path(directory)
        payload = {
            "session_id": "claude-expired",
            "hook_event_name": "UserPromptSubmit",
            "transcript_path": str(transcript(120)),
            "prompt": "first prompt",
        }
        first = run_hook("claude", payload, state_dir)
        assert first and first["decision"] == "block" and "110K" in str(first["reason"])
        changed = run_hook("claude", {**payload, "prompt": "changed prompt"}, state_dir)
        assert changed and changed["decision"] == "block"
        repeated = run_hook("claude", {**payload, "prompt": "changed prompt"}, state_dir)
        assert repeated and "decision" not in repeated
        assert "accepted" in str(repeated["systemMessage"])
        assert run_hook("claude", {**payload, "prompt": "later prompt"}, state_dir) is None
        next_expiry = {
            **payload,
            "transcript_path": str(transcript(119)),
            "prompt": "later prompt",
        }
        assert run_hook("claude", next_expiry, state_dir)["decision"] == "block"


def test_claude_missing_transcript_is_silent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        payload = {
            "session_id": "missing",
            "hook_event_name": "UserPromptSubmit",
            "transcript_path": "/nonexistent/transcript.jsonl",
            "prompt": "continue",
        }
        assert run_hook("claude", payload, Path(directory)) is None


def test_state_write_failure_does_not_block() -> None:
    with tempfile.TemporaryDirectory() as directory:
        state_path = Path(directory) / "not-a-directory"
        state_path.write_text("occupied", encoding="utf-8")
        payload = {
            "session_id": "write-failure",
            "hook_event_name": "UserPromptSubmit",
            "transcript_path": str(transcript(120)),
            "prompt": "continue",
        }
        output = run_hook("claude", payload, state_path)
        assert output and "decision" not in output
        assert "allowed" in str(output["systemMessage"])


def test_codex_cold_session_is_informational() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = run_hook(
            "codex",
            {"session_id": "codex-cold", "hook_event_name": "SessionStart"},
            Path(directory),
        )
        assert output and "cold" in str(output["systemMessage"])


def test_codex_stop_records_activity() -> None:
    with tempfile.TemporaryDirectory() as directory:
        state_dir = Path(directory)
        stop = run_hook(
            "codex",
            {"session_id": "codex-active", "hook_event_name": "Stop"},
            state_dir,
        )
        assert stop == {}
        output = run_hook(
            "codex",
            {
                "session_id": "codex-active",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "continue",
            },
            state_dir,
        )
        assert output is None


def test_codex_near_expiry_warns() -> None:
    with tempfile.TemporaryDirectory() as directory:
        state_dir = Path(directory)
        seed_codex_state(state_dir, "codex-warning", 26)
        output = run_hook(
            "codex",
            {
                "session_id": "codex-warning",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "continue",
            },
            state_dir,
        )
        assert output and "warm" in str(output["systemMessage"])


def test_codex_requires_the_exact_repeated_prompt() -> None:
    with tempfile.TemporaryDirectory() as directory:
        state_dir = Path(directory)
        session_id = "codex-expired"
        seed_codex_state(state_dir, session_id, 31)
        payload = {
            "session_id": session_id,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "first prompt",
        }
        first = run_hook("codex", payload, state_dir)
        assert first and first["decision"] == "block"
        changed = run_hook("codex", {**payload, "prompt": "changed prompt"}, state_dir)
        assert changed and changed["decision"] == "block"
        repeated = run_hook("codex", {**payload, "prompt": "changed prompt"}, state_dir)
        assert repeated and "decision" not in repeated
        assert "accepted" in str(repeated["systemMessage"])
        assert run_hook("codex", {**payload, "prompt": "later prompt"}, state_dir) is None


def test_codex_stop_rearms_the_gate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        state_dir = Path(directory)
        session_id = "codex-rearm"
        seed_codex_state(state_dir, session_id, 31)
        payload = {
            "session_id": session_id,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "continue",
        }
        assert run_hook("codex", payload, state_dir)["decision"] == "block"
        assert "accepted" in str(run_hook("codex", payload, state_dir)["systemMessage"])
        run_hook("codex", {"session_id": session_id, "hook_event_name": "Stop"}, state_dir)
        seed_codex_state(state_dir, session_id, 31)
        assert run_hook("codex", payload, state_dir)["decision"] == "block"


def test_installer_is_idempotent_and_preserves_settings() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        claude_settings = root / "claude.json"
        codex_hooks = root / "codex.json"
        unrelated = {"hooks": [{"type": "command", "command": "echo keep"}]}
        claude_settings.write_text(
            json.dumps({"theme": "dark", "hooks": {"UserPromptSubmit": [unrelated]}}),
            encoding="utf-8",
        )
        legacy = {
            "hooks": [
                {
                    "type": "command",
                    "command": "/usr/bin/python3 /tmp/cache_status.py",
                }
            ]
        }
        codex_hooks.write_text(
            json.dumps(
                {
                    "custom": True,
                    "hooks": {"Stop": [unrelated], "UserPromptSubmit": [legacy]},
                }
            ),
            encoding="utf-8",
        )
        claude_settings.chmod(0o600)
        codex_hooks.chmod(0o600)
        environment = dict(
            os.environ,
            CLAUDE_SETTINGS=str(claude_settings),
            CODEX_HOOKS=str(codex_hooks),
        )
        for _ in range(2):
            process = subprocess.run(
                ["bash", str(INSTALLER), "all"],
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            assert process.returncode == 0, process.stderr

        claude = json.loads(claude_settings.read_text(encoding="utf-8"))
        codex = json.loads(codex_hooks.read_text(encoding="utf-8"))
        assert claude["theme"] == "dark"
        assert codex["custom"] is True
        assert claude_settings.stat().st_mode & 0o777 == 0o600
        assert codex_hooks.stat().st_mode & 0o777 == 0o600
        claude_commands = [
            hook["command"]
            for group in claude["hooks"]["UserPromptSubmit"]
            for hook in group["hooks"]
        ]
        assert sum("cache_gate.py" in command for command in claude_commands) == 1
        assert "echo keep" in claude_commands
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            commands = [
                hook["command"]
                for group in codex["hooks"][event]
                for hook in group["hooks"]
            ]
            assert sum("cache_gate.py" in command for command in commands) == 1
            assert not any("cache_status.py" in command for command in commands)
        stop_commands = [
            hook["command"]
            for group in codex["hooks"]["Stop"]
            for hook in group["hooks"]
        ]
        assert "echo keep" in stop_commands


if __name__ == "__main__":
    tests = [
        (name, function)
        for name, function in globals().copy().items()
        if name.startswith("test_") and callable(function)
    ]
    for name, function in sorted(tests):
        function()
        print("ok", name)
