---
name: topic-classification
description: Classify or consolidate compatible topics.
---

Reuse canonical topics and define aliases or parent links in ingestion `annotations.taxonomy`.
Use the consolidation pipeline with `from_topic` and `to_topic` for two existing topics with the
same meaning. Use `topic-reclassification` with `topic` and `parents` to
replace a topic's parents. An empty list makes the topic a root. Run the
verification stage. The tools reject cycles and conflicting aliases.
