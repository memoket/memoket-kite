"""Observe an installed KITE submit hook and verify its saved symbolic execution."""

from __future__ import annotations

import json
import re
import runpy
import shlex
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

_ENGINE = "memoket_kite.core.algebra.execute_plan"
_QUOTE = re.compile(r"^\[([^\]\n]+#[^\]\n]+)\]", re.M)
_TYPED_INDEXES = frozenset(
    {"topics", "entities", "obj", "place", "event", "duration", "obj_bridge"}
)


def typed_index_activity(trace: list[dict]) -> dict[str, set[str]]:
    """Separate hard filters, ranking lookups, and resulting score changes."""
    activity = {name: set() for name in ("filters", "ranking", "hits", "score_changes")}
    for entry in trace:
        axis = entry.get("atom")
        stage = entry["stage"].rsplit(":", 1)[-1]
        if axis not in _TYPED_INDEXES or stage not in ("expand", "prefer_labels"):
            continue
        activity["filters" if stage == "expand" else "ranking"].add(axis)
        if entry.get("hits", 0) > 0:
            activity["hits"].add(axis)
            if stage == "prefer_labels" and entry.get("bonus", 0) > 0:
                activity["score_changes"].add(axis)
    return activity


def hook_options(command: list[str]) -> tuple[str, ...]:
    config = (
        'hooks.UserPromptSubmit=[{hooks=[{type="command",timeout=8,command='
        f"{json.dumps(shlex.join(command))}}}]}}]"
    )
    return ("--dangerously-bypass-hook-trust", "-c", config)


def symbolic_hook(launcher: Path, run_id: str, metrics: Path) -> tuple[str, ...]:
    return hook_options(
        [sys.executable, "-I", str(Path(__file__).resolve()), str(launcher), run_id, str(metrics)]
    )


def observe(launcher: Path, run_id: str, metrics: Path) -> None:
    event = json.load(sys.stdin)
    event["prompt"] = f"KITE plan: {run_id}\n" + event["prompt"]
    api = runpy.run_path(str(launcher.with_name("codex_context.py")))
    calls = []
    receipts = []
    run = subprocess.run

    def observed(command, *args, **kwargs):
        result = run(command, *args, **kwargs)
        calls.append({"args": command[1:], "exit_code": result.returncode})
        return result

    start = time.perf_counter()
    capture = "SYMBOLIC_RECEIPT_OPTION" in api
    with patch.object(subprocess, "run", observed):
        options = {"on_symbolic_receipt": receipts.append} if capture else {}
        context = api["prepare_context"](event, launcher, **options)
    metrics.write_text(
        json.dumps(
            {
                "bytes": len((context or "").encode()),
                "seconds": time.perf_counter() - start,
                "calls": calls,
                "additional_context": context,
                **({"receipts": receipts} if capture else {}),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if context:
        print(json.dumps(api["hook_output"](context)))


def verify_delivery(launcher, run_id, hook, cwd, env, memory_version):
    """Verify an observed receipt, or clearly identify a legacy replay."""
    context = hook.get("additional_context")
    if not context or not hook.get("calls"):
        return {"verified": False, "error": "no observed symbolic hook delivery"}
    call = hook["calls"][-1]
    arguments = call["args"]
    if (
        call["exit_code"] != 0
        or not arguments
        or arguments[0] != "symbolic"
        or "--reuse" not in arguments
        or arguments[arguments.index("--reuse") + 1 :][:1] != [run_id]
    ):
        return {"verified": False, "error": "hook did not complete the selected saved query"}
    try:
        output = json.loads(context.split("\n", 1)[1])
        if "receipts" in hook or "--delivery-receipt" in arguments:
            method = "observed_receipt"
            if "--delivery-receipt" not in arguments or len(hook.get("receipts", ())) != 1:
                raise ValueError("hook is missing its same-execution receipt")
            response = hook["receipts"][0]
            delivered, receipt = response["evidence"], response["receipt"]
        else:
            method = "legacy_replay"
            delivered, receipt = _replay_delivery(launcher, arguments, cwd, env)
        if not isinstance(output, str) or output != delivered:
            raise ValueError("recorded output differs from the injected evidence")
        if (
            receipt["engine"] != _ENGINE
            or receipt["compiler"] != "reused_symbolic_plan"
            or receipt["memory_version"] != memory_version
            or receipt["reused_plan"]["run_id"] != run_id
            or not isinstance(receipt["plan"], dict)
            or not isinstance(receipt["trace"], list)
            or not receipt["trace"]
            or any(
                not isinstance(entry, dict) or not isinstance(entry.get("stage"), str)
                for entry in receipt["trace"]
            )
        ):
            raise ValueError("receipt lacks a matching symbolic engine, plan, trace or snapshot")
        candidates = {
            f"{line['conversation_id']}#{line['segment_id']}": line
            for span in receipt["spans"]
            for line in span["evidence"]
        }
        refs, position = set(), 0
        while match := _QUOTE.search(output, position):
            ref = match[1]
            line = candidates.get(ref)
            if line is None:
                raise ValueError(f"injected citation {ref} missing from the symbolic receipt")
            speaker = f" {line['speaker_id']}:" if "speaker_id" in line else ""
            quote = f"{speaker} {line['text']}"
            if output.startswith(quote, match.end()):
                position = match.end() + len(quote)
            elif output[match.end() :] == quote.rstrip():
                position = len(output)
            else:
                raise ValueError(f"injected quotation differs from original at {ref}")
            refs.add(ref)
        if not refs:
            from memoket_kite.tools.symbolic import _schedule_follow_up

            follow_up = _schedule_follow_up({**receipt, "spans": []})
            if not follow_up or output != follow_up:
                raise ValueError("no original citations or linked follow-up in the hook delivery")
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
    ) as exc:
        return {"verified": False, "error": str(exc)}
    return {
        "verified": True,
        "verification": method,
        "reference_ids": sorted(refs),
        "receipt": receipt,
        **({"follow_up_only": True} if not refs else {}),
    }


def _replay_delivery(launcher, arguments, cwd, env):
    def run(args):
        return subprocess.check_output(
            [str(launcher), *args], cwd=cwd, env=env, timeout=30, text=True
        ).strip()

    delivered = run(arguments)
    raw_args = arguments.copy()
    if "--delivery-bytes" in raw_args:
        start = raw_args.index("--delivery-bytes")
        del raw_args[start : start + 2]
        delivered = json.loads(delivered)
    else:
        raw_args.remove("--text")
    return delivered, json.loads(run(raw_args))


if __name__ == "__main__":
    observe(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
