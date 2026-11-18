#!/usr/bin/env python3
"""Register the KITE native host for the explicitly chosen Chromium extension ID."""

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("browser", choices=["chrome", "edge"])
parser.add_argument("extension_id", help="copy the ID from your extensions page")
args = parser.parse_args()
if not re.fullmatch("[a-p]{32}", args.extension_id):
    parser.error("expected the 32-character Chromium extension ID")
if sys.platform != "darwin":
    parser.error("this installer targets macOS")

support = Path.home() / "Library/Application Support"
base = support / "KITE Twin/Native Messaging"
base.mkdir(mode=0o700, parents=True, exist_ok=True)
bridge = base / "browser-reply-host"
source = Path(__file__).with_name("native_bridge.py").read_text(encoding="utf-8")
bridge.write_text(f"#!{sys.executable}\n" + source.split("\n", 1)[1], encoding="utf-8")
bridge.chmod(0o700)
if " " in sys.executable:
    bridge.write_text(
        "#!/bin/sh\nexec " + shlex.join([sys.executable, str(base / "reply.py")]) + ' "$@"\n'
    )
    (base / "reply.py").write_text(source, encoding="utf-8")
name = "org.memoket.kite.twin.replies"
target_dir = support / {"chrome": "Google/Chrome", "edge": "Microsoft Edge"}[args.browser]
manifests = target_dir / "NativeMessagingHosts"
manifests.mkdir(parents=True, exist_ok=True)
target = manifests / f"{name}.json"
target.write_text(
    json.dumps(
        {
            "name": name,
            "description": "KITE Twin browser replies",
            "path": str(bridge),
            "type": "stdio",
            "allowed_origins": [f"chrome-extension://{args.extension_id}/"],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
print(target)
