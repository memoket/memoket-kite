"""Observe Codex JSON events without changing the task or model context."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import tempfile
import time
from contextlib import suppress


def read_events(command, prompt, env, start, *, timeout=180):
    """Timestamp completed lines as they reach the benchmark process."""
    deadline = start + timeout
    observed = []
    with tempfile.TemporaryFile() as input_file:
        input_file.write(prompt.encode("utf-8"))
        input_file.seek(0)
        with (
            subprocess.Popen(
                command,
                stdin=input_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            ) as process,
            selectors.DefaultSelector() as ready,
        ):
            ready.register(process.stdout, selectors.EVENT_READ)
            tail = b""
            try:
                while ready.select(max(0, deadline - time.perf_counter())):
                    chunk = os.read(process.stdout.fileno(), 65536)
                    seen = time.perf_counter() - start
                    if not chunk:
                        if tail:
                            observed.append((seen, tail))
                        return process.wait(
                            timeout=max(0, deadline - time.perf_counter())
                        ), observed
                    *lines, tail = (tail + chunk).split(b"\n")
                    observed.extend((seen, line) for line in lines)
                    if time.perf_counter() >= deadline:
                        break
                raise subprocess.TimeoutExpired(command, timeout)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                raise


def interval_seconds(intervals):
    """Count overlapping command intervals once."""
    total = stop = 0.0
    for start, end in sorted(intervals):
        total += max(0, end - max(start, stop))
        stop = max(stop, end)
    return total


def describe(events):
    turn_start = turn_end = None
    pending = {}
    commands = {}
    for seconds, event in events:
        kind = event.get("type")
        if kind == "turn.started":
            turn_start = seconds
        elif kind in {"turn.completed", "turn.failed"}:
            turn_end = seconds
        elif kind in {"item.started", "item.completed"}:
            item = event["item"]
            if item["type"] != "command_execution" or "id" not in item:
                continue
            identity = item["id"]
            if kind == "item.started":
                pending[identity] = seconds
            elif identity in pending:
                commands[identity] = (pending.pop(identity), seconds)
    timing = {
        "startup_seconds": turn_start,
        "turn_seconds": turn_end - turn_start
        if turn_start is not None and turn_end is not None
        else None,
        "shell_seconds": interval_seconds(commands.values()),
        "timed_shell_calls": len(commands),
    }
    if commands and timing["turn_seconds"] is not None:
        timing["before_first_shell_seconds"] = max(
            0, min(start for start, _ in commands.values()) - turn_start
        )
        timing["after_last_shell_seconds"] = max(
            0, turn_end - max(end for _, end in commands.values())
        )
    return timing, commands
