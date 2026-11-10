---
name: topic-classification
description: Classify or consolidate compatible topics.
---

Reuse canonical topics and define aliases or parent links in ingestion `annotations.taxonomy`.
Use the consolidation pipeline with `from_topic` and `to_topic` for two existing topics with the
same meaning. Run the verification stage. A cyclic graph or conflicting alias must fail before commit.
