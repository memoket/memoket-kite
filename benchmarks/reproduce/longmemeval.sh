#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

MODEL="${MODEL:-gpt-4.1-mini}"
BUILD_WORKERS="${BUILD_WORKERS:-8}"
EVAL_WORKERS="${EVAL_WORKERS:-4}"
# The reference tag is taken from the manifest so the default local run uses
# the documented result path. Override TAG to keep an experiment separate.
TAG="${TAG:-$("${PYTHON_BOOTSTRAP:-$(command -v python3 || command -v python)}" -c "
import json, pathlib, re, sys
manifest = json.loads(pathlib.Path('benchmarks/reproduce/manifest.json').read_text())
path = manifest['benchmarks']['longmemeval']['result_path']
sys.stdout.write(re.sub(r'^longmemeval-', '', pathlib.PurePosixPath(path).parent.name))
")}"
# `python` is absent on installs that ship only `python3`; the stage commands
# below run under whichever this resolves to, so a virtualenv still wins.
PYTHON="${PYTHON:-$(command -v python || command -v python3)}"
MODE="${1:-full}"
RESUME=()
CHECK_ONLY=false
CHECK_FLAGS=()
if [[ "$MODE" == "resume" ]]; then
  RESUME=(--resume)
elif [[ "$MODE" == "check" || "$MODE" == "check-offline" || "$MODE" == "check-provider" ]]; then
  CHECK_ONLY=true
  if [[ "$MODE" == "check-offline" ]]; then
    CHECK_FLAGS=(--offline)
  elif [[ "$MODE" == "check-provider" ]]; then
    CHECK_FLAGS=(--probe-provider)
  fi
  MODE=full
elif [[ "$MODE" != "full" ]]; then
  echo "usage: $0 [full|resume|check|check-offline|check-provider]" >&2
  exit 2
fi

"$PYTHON" -m benchmarks.reproduce.preflight longmemeval \
  --model "$MODEL" --tag "$TAG" --mode "$MODE" --build-workers "$BUILD_WORKERS" \
  --eval-workers "$EVAL_WORKERS" ${CHECK_FLAGS[@]+"${CHECK_FLAGS[@]}"}
if [[ "$CHECK_ONLY" == true ]]; then
  exit 0
fi

ARTIFACTS="$("$PYTHON" -c 'from benchmarks.common.paths import ARTIFACTS_ROOT; print(ARTIFACTS_ROOT)')"
echo "benchmark=longmemeval dataset=$ARTIFACTS/datasets/longmemeval/longmemeval_s_cleaned.json"
echo "model=$MODEL build_workers=$BUILD_WORKERS eval_workers=$EVAL_WORKERS tag=$TAG mode=$MODE"

"$PYTHON" -m benchmarks.longmemeval.build \
  --n 500 --workers "$BUILD_WORKERS" --model "$MODEL" ${RESUME[@]+"${RESUME[@]}"}

"$PYTHON" -m benchmarks.longmemeval.evaluate \
  --n 500 --workers "$EVAL_WORKERS" --model "$MODEL" --answer-model "$MODEL" --tag "$TAG" \
  --plan-cache "$ARTIFACTS/cache/plans/longmemeval-$TAG" ${RESUME[@]+"${RESUME[@]}"}

"$PYTHON" -m benchmarks.longmemeval.score \
  --tag "$TAG" --judge-model "$MODEL" --workers "$EVAL_WORKERS"
