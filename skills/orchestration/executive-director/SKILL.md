---
name: executive-director
description: Select and advance KITE pipelines for conversation memory.
---

Read [AGENT_GUIDE.md](../../../AGENT_GUIDE.md). Use `kite-tool pipelines` and `kite-tool validate-pipeline NAME`.
Choose based on the user's task. Validate input and load the skill for each ready stage.
Execute one stage per call. Stop downstream stages on failure and inspect the JSON error.
Resume with `status RUN_ID`. Start a new run for a changed definition or snapshot.
Request explicit user approval for the exact deletion preview before the apply stage.
