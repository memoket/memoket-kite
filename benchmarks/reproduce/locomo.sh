#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

MODEL="${MODEL:-gpt-4.1-mini}"
WORKERS="${WORKERS:-4}"
# `python` is absent on installs that ship only `python3`; the stage commands
# below run under whichever this resolves to, so a virtualenv still wins.
PYTHON="${PYTHON:-$(command -v python || command -v python3)}"
MODE="${1:-full}"
RESUME=()
CHECK_ONLY=false
CHECK_FLAGS=()
if [[ "$MODE" == "resume" ]]; then
  RESUME=(--resume)
elif [[ "$MODE" == "check" || "$MODE" == "check-offline" ]]; then
  CHECK_ONLY=true
  if [[ "$MODE" == "check-offline" ]]; then
    CHECK_FLAGS=(--offline)
  fi
  MODE=full
elif [[ "$MODE" != "full" ]]; then
  echo "usage: $0 [full|resume|check|check-offline]" >&2
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

"$PYTHON" -m benchmarks.reproduce.preflight locomo \
  --model "$MODEL" --tag "$TAG" --mode "$MODE" --eval-workers "$WORKERS" \
  ${CHECK_FLAGS[@]+"${CHECK_FLAGS[@]}"}
if [[ "$CHECK_ONLY" == true ]]; then
  exit 0
fi

ARTIFACTS="$("$PYTHON" -c 'from benchmarks.common.paths import ARTIFACTS_ROOT; print(ARTIFACTS_ROOT)')"
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
