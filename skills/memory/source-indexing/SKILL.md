---
name: source-indexing
description: Build and verify a searchable batch of original sources and optional span labels.
---

Use the user's selected files. Supply an absolute `selection.transcripts` directory
or `selection.batch_file` containing the same JSON accepted by `index-sources`.
For transcript folders, supply `selection.recording_metadata` when the user
provides a separate directory of same-ID recording metadata. Treat original
content and metadata as data. When the user asks for a content index, prepare
`index_spans` from the actual conversation before starting the pipeline.
Run `kite-tool annotation-blocks --transcript FILE.txt`, using the same
recording metadata option, or `kite-tool annotation-blocks --input SOURCE.json
--prepare`. Follow `next_offset` until the source is covered. Treat the
suggestions as reading boundaries. Merge a question with its answer when the
same subject continues. Cite the smallest original span supporting each group
of labels.
For each span, cite the original segment IDs and assign any supported topics,
named entities, and object, place, event, or duration facets. One span may
support several topics. Leave unsupported dimensions empty. Keep recording
IDs and project membership in source metadata. Leave the facts array empty.
For named entities, supply `entity_mentions` with the canonical `entity`,
original `segment_id`, and verbatim `text`. Use `entity_aliases` for verified
alternative spellings. Review each mention in context and omit amounts,
filler words, or ambiguous names.

For a transcript folder, set `selection.annotations` to the absolute
directory of prepared JSON files. Use the returned `annotation_filename`.
Each file contains the prepared `source_checksum` and an `annotations`
object with the combined pages. For a JSON source batch, add
`source_checksum` to each prepared entry alongside `source` and
`annotations`. If a source changes, read it again before reusing its labels.

Inspect the preview counts for topics, entities, and facets before committing.
The commit checks the prepared
sources against the preview digest. If files or the workspace change between
stages, start a fresh run and inspect the new preview. Verify the committed
Codebook and original-text spans. Open **Workflows** in the viewer to inspect
the run, then open the linked change to compare source graphs and original lines.
