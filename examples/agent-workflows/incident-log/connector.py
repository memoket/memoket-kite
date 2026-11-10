"""Convert a local incident export into a KITE conversation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def convert(export: dict) -> dict:
    incident = export["incident_id"]
    conversation = {
        "conversation_id": incident,
        "source": "sample-incident-log",
        "metadata": {"source_reference": incident},
        "segments": [],
    }
    if export.get("started_at") is not None:
        conversation["started_at"] = export["started_at"]
    for update in export["updates"]:
        segment = {"segment_id": update["id"], "text": update["text"]}
        if update.get("author") is not None:
            segment["speaker_id"] = update["author"]
        if update.get("offset_ms") is not None:
            segment["start_ms"] = update["offset_ms"]
        conversation["segments"].append(segment)
    return conversation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="local incident export JSON")
    args = parser.parse_args()
    try:
        export = json.loads(args.export.read_text(encoding="utf-8"))
        conversation = convert(export)
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        parser.exit(2, "cannot read a valid incident export\n")
    print(json.dumps(conversation, indent=2))


if __name__ == "__main__":
    main()
