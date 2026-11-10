# Add a workflow and a source connector

Install KITE with the `agent` extra from this checkout or a
[CI preview wheel](README.md). The bundled `incident-log` example imports a
synthetic incident update and traces its original text, speaker and time.

## Copy the example to a project

`kite-tool resources` locates the installed examples. Copy `incident-log` so
you can edit the project files independently.

```sh
examples="$(kite-tool resources | python -c 'import json, sys; print(json.load(sys.stdin)["examples"])')"
scratch="$(mktemp -d)"
project="$scratch/incident-log"
cp -R "$examples/incident-log" "$project"
export KITE_DATA_DIR="$scratch/memory"

kite_ext() { kite-tool --extensions "$project" "$@"; }
kite_ext pipelines
kite_ext validate-pipeline example-incident-review
```

The [YAML definition](incident-log/pipeline_defs/example-incident-review.yaml)
names its input, each stage's tool and prerequisites, and its output type.
Read the [skill](incident-log/skills/incident-review/SKILL.md) before running a
stage. `validate-pipeline` returns `skill_root`, the base for relative skill
paths. Use `kite-tool describe TOOL` to inspect a tool's contract.

## Convert and import a local export

The [connector](incident-log/connector.py) reads the
[sample export](incident-log/export.json) and outputs a KITE conversation. It
preserves the incident and update IDs, timestamps, speaker labels, and source
text. `file-source` validates the output against the installed conversation
schema. The [prepared annotations](incident-log/annotations.json) cite an
original update ID.

```sh
python "$project/connector.py" "$project/export.json" > "$scratch/conversation.json"
kite-tool file-source "$scratch/conversation.json" --format conversation \
  | kite-tool ingest --input - --annotations "$project/annotations.json" \
      --run-id import-example --resume-existing
```

Use stable source, conversation and segment IDs when adapting the connector.
Keep optional metadata tied to what the export actually provides. Read and
retain only authorized source data.

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

The report cites `incident-42`, update `update-1`, and the original line about
Tuesday. Its evidence also includes `oncall` and the offset of 1250 ms.

Put project definitions under `pipeline_defs/` and referenced skills under
`skills/`. Choose a pipeline ID distinct from bundled IDs. During discovery,
`pipelines` lists invalid definitions in `invalid_extensions`. Reuse the same
extension when resuming a run. After editing its definition or a referenced
skill, start a new run against the revised files.
