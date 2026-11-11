---
name: source-revision-review
description: Read the changed lines of one conversation from two retained revisions.
---

Run `kite-tool source-compare BEFORE --text` with the known baseline checksum.
Use `source-history SOURCE ID` to find the stored checksums. Add `--after`
to select another retained version. Read the shown revision dates and original
lines. When a quote exceeds the budget, use its `source_line_id` with `evidence`.

Use `source-revision-review` when a saved result is needed. Pass
`{"request":{"before":"CHECKSUM"}}` and run the `compare` stage.
