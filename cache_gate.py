#!/usr/bin/env python3
"""claude-cache-gate — Claude Code UserPromptSubmit hook that gates prompts on an expired cache.

The cache TTL (default 1h) resets on every request, so idle time since the
last assistant message decides whether the next turn is a cache read (cheap)
or a full cache write of the whole context (expensive, once). The warning
carries the context size so you can decide: pay the re-write or start fresh.

On expiry the first submit is BLOCKED (prompt dropped, reason shown); the next
submit goes through. A marker in /tmp keyed by session id + last-turn timestamp
makes the block fire once per expiry, not once per prompt.

On expiry the first submit is BLOCKED (prompt dropped, reason shown); the next
submit goes through. A marker file in /tmp keyed by session id and the
last-turn timestamp makes the block fire once per expiry, not once per prompt.

Also usable standalone:  cache_gate.py --list   (all sessions of the cwd)
Always exits 0; any failure is silent so it can never block a turn.
"""
import json
import os
import sys
from datetime import datetime, timezone

TTL_MIN = int(os.environ.get("CLAUDE_CACHE_TTL_MIN", "60"))
WARN_AT_MIN = int(os.environ.get("CLAUDE_CACHE_WARN_MIN", "50"))


def last_turn(transcript):
    """(last assistant timestamp, context tokens at that turn, model) or None."""
    ts, ctx, model = None, 0, None
    try:
        with open(transcript, errors="replace") as f:
            for line in f:
                if '"assistant"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("isSidechain"):
                    continue
                msg = d.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                ts = d.get("timestamp") or ts
                model = msg.get("model") or model
                ctx = (
                    (usage.get("input_tokens") or 0)
                    + (usage.get("cache_read_input_tokens") or 0)
                    + (usage.get("cache_creation_input_tokens") or 0)
                )
    except Exception:
        return None
    if not ts:
        return None
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None
    return when, ctx, model


def idle_minutes(when):
    return (datetime.now(timezone.utc) - when).total_seconds() / 60


def hook():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    transcript = data.get("transcript_path") or ""
    session_id = data.get("session_id") or "unknown"
    if not os.path.isfile(transcript):
        return
    turn = last_turn(transcript)
    if not turn:
        return
    when, ctx, _ = turn
    idle = idle_minutes(when)
    kilo = ctx // 1000
    if idle < TTL_MIN:
        if idle >= WARN_AT_MIN:
            print(json.dumps({"systemMessage":
                f"Prompt cache warm for ~{TTL_MIN - idle:.0f}m more ({kilo}K context)."}))
        return

    msg = (f"Prompt cache expired (idle {idle:.0f}m ≥ {TTL_MIN}m TTL): "
           f"the next turn re-writes ~{kilo}K tokens of context.")
    marker = f"/tmp/claude-cache-gate-{session_id}"
    stamp = when.isoformat()
    try:
        with open(marker) as f:
            already = f.read().strip() == stamp
    except Exception:
        already = False
    if already:
        print(json.dumps({"systemMessage": msg + " Proceeding."}))
        return
    try:
        with open(marker, "w") as f:
            f.write(stamp)
    except Exception:
        pass
    print(json.dumps({"decision": "block", "reason":
        msg + " Blocked once — re-send to pay it, "
              "or /handoff and start a fresh session."}))


def list_sessions():
    cwd = os.getcwd()
    slug = cwd.replace("/", "-")
    proj = os.path.expanduser(f"~/.claude/projects/{slug}")
    if not os.path.isdir(proj):
        print(f"no transcripts for {cwd}")
        return
    rows = []
    for name in os.listdir(proj):
        if not name.endswith(".jsonl"):
            continue
        turn = last_turn(os.path.join(proj, name))
        if not turn:
            continue
        when, ctx, model = turn
        rows.append((when, name[:-6], ctx, model or "?"))
    rows.sort(reverse=True)
    print(f"{'session':36}  {'last turn':16}  {'idle':>7}  {'ctx':>6}  cache")
    for when, sid, ctx, model in rows[:20]:
        idle = idle_minutes(when)
        left = TTL_MIN - idle
        state = "EXPIRED" if left <= 0 else f"{left:.0f}m left"
        print(f"{sid:36}  {when.astimezone():%Y-%m-%d %H:%M}  "
              f"{idle:6.0f}m  {ctx // 1000:5d}K  {state}")


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_sessions()
    else:
        hook()
