# Add a workflow and a source connector

This example runs against an installed KITE with the `agent` extra. It imports
one synthetic incident update from a local export and retrieves the original
line with a project-specific workflow.

## Create a project extension

An extension directory contains `pipeline_defs/` and the `skills/` files that
its workflows reference. Use a project-specific pipeline ID to avoid name
collisions with bundled workflows. KITE validates the definition against its
installed schemas and only runs tools in its registry.

```sh
project="$(mktemp -d)"
export KITE_DATA_DIR="$(mktemp -d)"
mkdir -p "$project/pipeline_defs" "$project/skills/incident-review"

cat > "$project/skills/incident-review/SKILL.md" <<'SKILL'
---
name: incident-review
description: Trace an incident topic to the original updates.
---

Check the resolved topic labels before retrieving evidence. In the report,
cite each supporting incident ID and update ID. Keep proposed follow-up
actions separate from decisions that the incident team confirmed.
SKILL

cat > "$project/pipeline_defs/example-incident-review.yaml" <<'YAML'
id: example-incident-review
version: 1
description: Trace an incident topic to original updates.
inputs:
  query: query
stages:
  - id: resolve
    skill: skills/incident-review/SKILL.md
    tool: resolve
    needs: []
    inputs: {query: input.query}
    output: resolved
    acceptance: Review the resolved topic names.
  - id: retrieve
    skill: skills/incident-review/SKILL.md
    tool: retrieve
    needs: [resolve]
    inputs: {resolved: stage.resolve}
    output: matches
    acceptance: Collect matching fact IDs.
  - id: report
    skill: skills/incident-review/SKILL.md
    tool: report
    needs: [retrieve]
    inputs: {matches: stage.retrieve}
    output: report
    acceptance: Cite the original incident and update IDs.
YAML

kite_ext() { kite-tool --extensions "$project" "$@"; }
kite_ext pipelines
kite_ext validate-pipeline example-incident-review
```

The `skill_root` returned by `validate-pipeline` is the base for the relative
`skill` paths in the definition. Read each referenced skill before running its
stage. Use `kite-tool describe TOOL` to inspect the tool's inputs and output.

During discovery, `pipelines` reports invalid project definitions in
`invalid_extensions` alongside the usable workflows in `pipelines`.
`validate-pipeline NAME` and `start NAME` reject an invalid definition.
Rename any project workflow whose ID collides with a bundled ID. To use the
bundled version while fixing the project, run without `--extensions`.

## Convert a local export

A connector runs outside KITE and produces a conversation using the installed
`schemas/conversation.schema.json` contract. Keep a stable source namespace
and preserve original conversation and segment IDs so imports can be replayed,
updated, and cited. Read and retain only the source data you are authorized to
use.

```sh
cat > "$project/export.json" <<'JSON'
{
  "incident_id": "incident-42",
  "updates": [
    {"id": "update-1", "author": "oncall", "text": "We moved the alert review to Tuesday."}
  ]
}
JSON

python3 - "$project/export.json" > "$project/conversation.json" <<'PY'
import json
import sys
from pathlib import Path

export = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
incident = export["incident_id"]
json.dump(
    {
        "conversation_id": incident,
        "source": "sample-incident-log",
        "metadata": {"source_reference": incident},
        "segments": [
            {"segment_id": row["id"], "speaker_id": row["author"], "text": row["text"]}
            for row in export["updates"]
        ],
    },
    sys.stdout,
)
PY

kite-tool file-source "$project/conversation.json" --format conversation > "$project/source.json"
kite-tool start memory-ingestion --run-id import-example --input "$project/source.json"
kite-tool stage import-example normalize

kite-tool stage import-example annotate --input - <<'JSON'
{"annotations":{"facts":[{"content":"The alert review moved to Tuesday.","segment_ids":["update-1"],"topics":["alerts"],"status":"confirmed"}]}}
JSON
kite-tool stage import-example commit --summary
kite-tool stage import-example verify --summary
```

## Trace the imported evidence

```sh
kite_ext start example-incident-review --run-id review-example --input - <<'JSON'
{"query":{"topics":["alerts"],"source":"sample-incident-log"}}
JSON
kite_ext stage review-example resolve
kite_ext stage review-example retrieve --summary
kite_ext status review-example
kite_ext stage review-example report
```

The report cites conversation `incident-42`, segment `update-1`, and the
original line about Tuesday. To resume a custom run, supply the same extension
with `--extensions`. You can move the directory. If you change the definition
or a referenced skill, start a new run.
