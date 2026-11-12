"""Reserve verified terminal title results in a whole-passage evidence pack."""

import re

from memoket_kite.pipeline import ledger


def _unit(row):
    return row.get("unit") or row["id"]


def _terminal_title_rows(plan, stage_results, store):
    stages = plan.get("stages", [])
    scopes = [str(stage.get("where", {}).get("units", "")) for stage in stages]
    for stage in stages:
        name = stage["name"]
        if stage["select"] != "units" or any(f"${name}.unit" in scope for scope in scopes):
            continue
        grep = stage.get("where", {}).get("grep")
        if not grep:
            continue
        pattern = re.compile(grep, re.I)
        rows = stage_results.get(name, [])
        matches = []
        for row in rows:
            title = store.units[_unit(row)].title
            bare_title = re.sub(r"\s*\([^()]+\)$", "", title)
            if pattern.fullmatch(title) or pattern.fullmatch(bare_title):
                matches.append(row)
        if len(matches) == 1 and matches[0] in rows[:2]:
            yield matches[0]


def reserve_terminal_titles(result, store, stage_results, plan_rows, fused_rows, render):
    """Swap lexical-only passages for terminal title hits under the original prompt cap.

    `render` renders a result with the existing reader template. The result's
    original rows are restored on exit, and each swap is returned for auditing.
    """
    original = result.rows
    prompt, fmt, sources = render(result)
    swaps = []
    if any(row["type"] == "count" for row in original):
        return prompt, fmt, sources, swaps

    ledger.require_exact_tokenizer("whole-passage terminal title reservations")
    budget = ledger.text_tokens(prompt)
    protected = {_unit(row) for row in plan_rows if row["type"] != "count"}
    lexical_tail = dict.fromkeys(
        _unit(row) for row in reversed(fused_rows) if _unit(row) not in protected
    )
    try:
        for hit in _terminal_title_rows(result.plan, stage_results, store):
            current_units = {source["unit"] for source in sources}
            if _unit(hit) in current_units:
                continue
            previous = result.rows
            for drop in lexical_tail:
                if drop not in current_units:
                    continue
                result.rows = sorted(
                    [row for row in previous if _unit(row) != drop] + [hit],
                    key=lambda row: (row.get("date") or "", row["id"]),
                )
                candidate = render(result)
                if ledger.text_tokens(candidate[0]) <= budget:
                    prompt, fmt, sources = candidate
                    swaps.append({"added": _unit(hit), "removed": drop})
                    break
            else:
                result.rows = previous
    finally:
        result.rows = original
    return prompt, fmt, sources, swaps
