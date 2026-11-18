"""Exercise real native-messaging frames and the private desktop mailbox."""

import json
import os
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

HOST = Path(__file__).resolve().parents[1] / "native_bridge.py"


def put(stream, message):
    packet = json.dumps(message, ensure_ascii=False).encode()
    stream.write(struct.pack("=I", len(packet)) + packet)
    stream.flush()


def get(stream):
    assert select.select([stream], [], [], 5)[0], "No native host reply"
    (size,) = struct.unpack("=I", stream.read(4))
    return json.loads(stream.read(size))


def await_file(path):
    for _ in range(80):
        if path.is_file():
            return path
        time.sleep(0.05)
    raise AssertionError(f"Native host didn't write {path.name}")


class NativeBridge(unittest.TestCase):
    def test_originating_request_survives_rejected_fill_then_closes(self):
        with tempfile.TemporaryDirectory() as home:
            nonce = str(uuid.uuid4())
            parent = Path(home) / "Library/Application Support/KITE Twin/Browser Replies"
            mailbox = parent / nonce
            with subprocess.Popen(
                [sys.executable, str(HOST)],
                env={**os.environ, "HOME": home},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            ) as proc:
                begin = {"id": nonce, "channel": "飞书", "text": "Pack the demo on Friday."}
                try:
                    put(proc.stdin, {"type": "begin", **begin})
                    request = await_file(mailbox / "begin.json")
                    self.assertEqual(json.loads(request.read_text()), begin)
                    self.assertEqual(parent.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(request.stat().st_mode & 0o777, 0o600)
                    (mailbox / "accepted").touch()
                    draft = "Draft only: will bring a checklist."
                    for ok in (False, True):
                        attempt = str(uuid.uuid4())
                        temp = mailbox / "draft.tmp"
                        temp.write_text(json.dumps({"attempt": attempt, "draft": draft}))
                        temp.replace(mailbox / "fill.json")
                        self.assertEqual(
                            get(proc.stdout),
                            {"type": "fill", "id": nonce, "attempt": attempt, "draft": draft},
                        )
                        put(
                            proc.stdin,
                            {
                                "type": "result",
                                "id": nonce,
                                "attempt": attempt,
                                "ok": ok,
                                "error": "target changed" if not ok else "",
                            },
                        )
                        ack = await_file(mailbox / "result.json")
                        response = json.loads(ack.read_text())
                        self.assertEqual((response["attempt"], response["ok"]), (attempt, ok))
                        ack.unlink()
                    (mailbox / "closed").touch()
                    self.assertEqual(proc.wait(5), 0)
                    self.assertFalse(mailbox.exists())
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(3)

    def test_request_with_invalid_id_creates_no_mailbox(self):
        with tempfile.TemporaryDirectory() as home:
            packet = b'{"type":"begin","id":"../invalid","channel":"Gmail","text":"sample"}'
            result = subprocess.run(
                [sys.executable, str(HOST)],
                input=struct.pack("=I", len(packet)) + packet,
                env={**os.environ, "HOME": home},
                capture_output=True,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(Path(home).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
