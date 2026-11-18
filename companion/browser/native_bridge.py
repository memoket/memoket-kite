#!/usr/bin/env python3
"""Chrome native-messaging adapter; the signed desktop window owns drafts."""

import json
import os
import re
import select
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path.home() / "Library/Application Support/KITE Twin/Browser Replies"
NONCE = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")


def write_json(path, value):
    fd, temp = tempfile.mkstemp(prefix=".message-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def receive():
    size = sys.stdin.buffer.read(4)
    if len(size) != 4:
        raise EOFError
    (length,) = struct.unpack("=I", size)
    if not 0 < length <= 65_536:
        raise ValueError("Invalid message size")
    value = json.loads(sys.stdin.buffer.read(length))
    if not isinstance(value, dict):
        raise ValueError("Invalid message")
    return value


def send(value):
    raw = json.dumps(value, ensure_ascii=False).encode()
    sys.stdout.buffer.write(struct.pack("=I", len(raw)) + raw)
    sys.stdout.buffer.flush()


def bridge():
    first = receive()
    nonce = first.get("id")
    if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise ValueError("Invalid request identity")
    text = first.get("text")
    if (
        first.get("type") != "begin"
        or first.get("channel") not in ("Gmail", "Slack", "飞书")
        or not isinstance(text, str)
        or not text.strip()
        or len(text) > 6000
    ):
        raise ValueError("Invalid selection")
    ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = ROOT.lstat()
    if ROOT.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("KITE's reply bridge requires a private directory")
    request = ROOT / nonce
    request.mkdir(mode=0o700)
    try:
        write_json(request / "begin.json", {"id": nonce, "channel": first["channel"], "text": text})
        deadline = time.monotonic() + 15 * 60
        ready_by = time.monotonic() + 12
        started = False
        pending = None
        while time.monotonic() < deadline:
            if select.select([sys.stdin.buffer], [], [], 0.24)[0]:
                result = receive()
                if (
                    result.get("type") != "result"
                    or result.get("id") != nonce
                    or pending is None
                    or result.get("attempt") != pending
                ):
                    raise ValueError("Unexpected reply")
                entry = {
                    "attempt": pending,
                    "ok": result.get("ok") is True,
                    "error": str(result.get("error") or "")[:240],
                }
                write_json(request / "result.json", entry)
                pending = None
            if (request / "accepted").is_file():
                started = True
            if not started and time.monotonic() > ready_by:
                send({"id": nonce, "error": "请打开 KITE Twin，再从原网页发起回复。"})
                return
            if (request / "closed").is_file():
                return
            output = request / "fill.json"
            if pending is None and output.is_file():
                draft = json.loads(output.read_text(encoding="utf-8"))
                output.unlink()
                value = draft.get("draft")
                if not isinstance(value, str) or not value.strip() or len(value) > 12000:
                    raise ValueError("Invalid output")
                pending = draft["attempt"]
                send({"type": "fill", "id": nonce, "draft": value, "attempt": pending})
    finally:
        shutil.rmtree(request, ignore_errors=True)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        bridge()
    except (EOFError, BrokenPipeError):
        pass
    except (OSError, ValueError, KeyError):
        # Don't log message contents.
        sys.exit(1)
