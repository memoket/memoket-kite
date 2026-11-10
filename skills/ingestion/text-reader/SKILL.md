---
name: text-reader
description: Read a user-selected text conversation or transcript.
---

For a user-selected UTF-8 file, run
`kite-tool file-source FILE --format text --source-id STABLE_ID` and pass the
result as the pipeline input. Supply a source date only when known.

Use `format: text` for raw UTF-8 text in `payload.text` with `conversation_id` and
`metadata.source_reference`. KITE preserves it as one segment called `text-1`.
Use `format: transcript` with `payload.segments` when structured source turns are available.
Preserve text, order, original IDs, speakers and offsets. Omit metadata that the source did not supply.
