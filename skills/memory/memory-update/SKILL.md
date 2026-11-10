---
name: memory-update
description: Apply source revisions and cited corrections while preserving history.
---

Supply the last committed checksum as `source.expected_checksum` when revising the same source.
An unrelated new source omits the checksum. For a decision or task correction, add a new confirmed
fact with the same `state_key` and its predecessor's fact ID in `supersedes`. Cite the new evidence.
Check that history remains available and that the report marks the correct effective state.
