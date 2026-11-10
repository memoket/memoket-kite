---
name: memory-update
description: Apply source revisions and cited annotations while preserving history.
---

Supply the current committed checksum as `source.expected_checksum` when updating an existing
source. For unchanged source text, submit only the annotations to add. Use the original segment
IDs and preserve the source payload exactly. An unrelated new source omits the checksum.
For a decision or task correction, add a confirmed fact with the same `state_key` and its
predecessor's fact ID in `supersedes`. Cite evidence that supports the corrected assertion.
Check that history remains available and that the report marks the correct effective state.
