"""Run: python3 -m pytest tests/  (or python3 tests/test_cache_gate.py)"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "cache_gate.py")


def transcript(minutes_ago, ctx=110_000):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    ts = ts.replace("+00:00", "Z")
    line = {"type": "assistant", "timestamp": ts,
            "message": {"model": "claude-opus-5",
                        "usage": {"input_tokens": 1000,
                                  "cache_read_input_tokens": ctx - 1000}}}
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write(json.dumps(line) + "\n")
    f.close()
    return f.name


def run(path, session="test-" + str(os.getpid()), env=None):
    e = dict(os.environ, CLAUDE_CACHE_TTL_MIN="60", CLAUDE_CACHE_WARN_MIN="50", **(env or {}))
    p = subprocess.run([sys.executable, SCRIPT],
                       input=json.dumps({"session_id": session, "transcript_path": path}),
                       capture_output=True, text=True, env=e)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def clear(session):
    try:
        os.remove(f"/tmp/claude-cache-gate-{session}")
    except FileNotFoundError:
        pass


def test_warm_is_silent():
    assert run(transcript(5)) is None


def test_near_expiry_warns():
    out = run(transcript(55))
    assert out and "warm for" in out["systemMessage"]


def test_expired_blocks_once_then_proceeds():
    s = "test-block"
    clear(s)
    t = transcript(120)
    first = run(t, s)
    assert first["decision"] == "block" and "110K" in first["reason"]
    second = run(t, s)
    assert "decision" not in second and "Proceeding" in second["systemMessage"]
    clear(s)


def test_missing_transcript_is_silent():
    assert run("/nonexistent.jsonl") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
