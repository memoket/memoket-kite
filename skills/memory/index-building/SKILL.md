---
name: index-building
description: Commit and verify a strict symbolic Codebook.
---

Run the registered commit stage with the validated annotation artifact.
Check the returned checksum and memory version, then execute the verification stage.
A repeated source revision returns `changed: false`. Retry an interrupted call with the same run and stage IDs.
Keep the original source. The canonical snapshot is managed by the tool.

Use `memory-rebuild` to repair a damaged Codebook from committed source records.
