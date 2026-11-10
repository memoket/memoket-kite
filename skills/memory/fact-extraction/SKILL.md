---
name: fact-extraction
description: Prepare structured facts with direct citations.
---

Read the normalized conversation and create the `annotations` input containing `facts`.
Use one independently meaningful statement per fact. Cite exact `segment_ids` from the source.
Choose narrow topics and reuse existing canonical labels. See `kite-tool describe annotate`.
Use `status: confirmed` only for explicit decisions or reported state. Label proposals, rejections,
open questions and inferences separately. Supply `state_key` and `state` together for evolving state.
Review the citations and wording before committing. An empty fact list is valid when there is nothing to extract.
The optional provider pipeline produces candidates and requires explicit permission for the extraction stage.
