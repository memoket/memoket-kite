#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

MODEL="${MODEL:-gpt-4.1-mini}"
WORKERS="${WORKERS:-4}"
# Honour the same artifact root the Python tooling reads.
ARTIFACTS="${KITE_ARTIFACTS_DIR:-artifacts}"
# `python` is absent on installs that ship only `python3`; the stage commands
# below run under whichever this resolves to, so a virtualenv still wins.
PYTHON="${PYTHON:-$(command -v python || command -v python3)}"
MODE="${1:-full}"
RESUME=()
if [[ "$MODE" == "resume" ]]; then
  RESUME=(--resume)
elif [[ "$MODE" != "full" ]]; then
  echo "usage: $0 [full|resume]" >&2
  exit 2
fi

# Validate the tag before starting the model-backed Codebook build.
TAG="$("$PYTHON" -c '
import sys

from benchmarks.locomo import DEFAULT_TAG, evaluation_tag

try:
    print(evaluation_tag(sys.argv[1] or DEFAULT_TAG))
except ValueError as error:
    raise SystemExit(str(error)) from error
' "${TAG:-}")"

echo "benchmark=locomo dataset=$ARTIFACTS/datasets/locomo/locomo10.json"
echo "model=$MODEL workers=$WORKERS tag=$TAG mode=$MODE"

"$PYTHON" -m benchmarks.locomo.build \
  --samples 0 1 2 3 4 5 6 7 8 9 \
  --model "$MODEL" ${RESUME[@]+"${RESUME[@]}"}

"$PYTHON" -m benchmarks.locomo.evaluate \
  --samples 0 1 2 3 4 5 6 7 8 9 \
  --model "$MODEL" --answer-model "$MODEL" --workers "$WORKERS" --tag "$TAG" \
  --plan-cache "$ARTIFACTS/cache/plans/locomo-$TAG" ${RESUME[@]+"${RESUME[@]}"}

"$PYTHON" -m benchmarks.locomo.score \
  --tag "$TAG" --judge-model "$MODEL" --workers "$WORKERS"
