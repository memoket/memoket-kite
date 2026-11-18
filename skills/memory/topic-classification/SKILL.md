---
name: topic-classification
description: Classify or consolidate compatible topics.
---

Reuse canonical topics and define aliases or parent links in ingestion `annotations.taxonomy`.
Use the consolidation pipeline with `from_topic` and `to_topic` for two existing topics with the
same meaning. Use `topic-reclassification` with `topic` and `parents` to
replace a topic's parents. An empty list makes the topic a root. Run the
verification stage. The tools reject cycles and conflicting aliases.

For a selected set of existing index-only recordings, use `topic-rebuild`.
Prepare a version-bound plan with proposed topic names, parents, original
recording checksums, and the exact segment IDs supporting each proposed topic.
Derive the roots and families from the selected original text. Record explicit
equivalence decisions for labels at the same level and under the same parent.
Review the preview's admission and promotion counts and evidence references.
The workflow replaces Topic postings and preserves the selected originals,
Entity annotations and Facets. Compare native retrieval against a verified
backup before applying a large rebuild to the current workspace.
