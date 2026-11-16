"""Prepare a public execution fixture with the same saved plan before and after a source update."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .context_eval import kite, read_corpus

_INITIAL = (
    "Rollout review for the Orchard project.\n"
    "We approved the North and West regions for the rollout.\n"
    "The approved rollout cap is 15 signups.\n"
)
_REVISION = (
    "Orchard rollout follow-up.\n"
    "The approved rollout regions are now North and East.\n"
    "The approved rollout cap is now 9 signups.\n"
)
_PROPOSAL = (
    "Later Orchard discussion.\n"
    "We discussed proposing South and 40 signups for another rollout.\n"
    "That proposal was not approved. Keep the current policy.\n"
)
_SEED = """def policy():
    return {"regions": [], "max_signups": 0}
"""
_PUBLIC_TEST = """import unittest
from policy import policy


class PolicyContract(unittest.TestCase):
    def test_public_shape(self):
        result = policy()
        self.assertEqual(set(result), {"regions", "max_signups"})
        self.assertIsInstance(result["regions"], list)
        self.assertIs(type(result["max_signups"]), int)
        self.assertIsNot(result, policy())
"""
_QUESTION = "Find the latest approved Orchard rollout regions and signup cap."
_TASK = (
    "Implement the latest approved Orchard rollout policy. Update policy.py, keep the policy() "
    "interface and run python3 -m unittest -v."
)
_RUN = "orchard-rollout"


def prepare(root: Path) -> None:
    root = root.resolve()
    root.mkdir(mode=0o700, parents=True)

    seed = root / "seed"
    seed.mkdir()
    (seed / "policy.py").write_text(_SEED, encoding="utf-8")
    (seed / "test_policy.py").write_text(_PUBLIC_TEST, encoding="utf-8")
    before = root / "before"
    sources = before / "sources"
    sources.mkdir(parents=True)
    (sources / "initial.txt").write_text(_INITIAL, encoding="utf-8")
    memory = before / "memory"
    kite(
        memory,
        "index-sources",
        "--input",
        "-",
        payload={"sources": [source.record for source in read_corpus(sources, "text")]},
    )
    kite(
        memory,
        "symbolic",
        "--input",
        "-",
        "--save",
        _RUN,
        payload={
            "question": _QUESTION,
            "plan": {
                "queries": [
                    {
                        "select": "lines",
                        "where": {"grep": "rollout|signups"},
                        "prefer": ["approved|current"],
                    },
                ]
            },
        },
    )
    after = root / "after"
    shutil.copytree(sources, after / "sources")
    for name, text in (("revision", _REVISION), ("proposal", _PROPOSAL)):
        (after / "sources" / f"{name}.txt").write_text(text, encoding="utf-8")
    kite(memory, "backup", str(after / "memory"))
    kite(
        after / "memory",
        "index-sources",
        "--input",
        "-",
        payload={"sources": [source.record for source in read_corpus(after / "sources", "text")]},
    )
    entries = []
    for state, regions, cap, recording in (
        ("before", ["North", "West"], 15, "initial"),
        ("after", ["North", "East"], 9, "revision"),
    ):
        verifier = root / state / "verify.py"
        verifier.write_text(
            "import json\nfrom pathlib import Path\nimport sys\n"
            "sys.path.insert(0, str(Path.cwd()))\nfrom policy import policy\n"
            "result = policy()\n"
            f"print(json.dumps({{'regions': sorted(result['regions']) == {sorted(regions)!r},"
            f" 'max_signups': result['max_signups'] == {cap}}}))\n",
            encoding="utf-8",
        )
        entries.append(
            {
                "id": f"orchard-{state}",
                "dataset": "synthetic-symbolic",
                "task": _TASK,
                "source_format": "text",
                "sources": f"{state}/sources",
                "workspace": f"{state}/memory",
                "seed": "seed",
                "verifier": f"{state}/verify.py",
                "symbolic_run": _RUN,
                "evidence": {
                    "regions": [f"{recording}#text-line-2"],
                    "max_signups": [f"{recording}#text-line-3"],
                },
            }
        )
    (root / "cases.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(root / "cases.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new directory for generated fixture files")
    prepare(parser.parse_args().output)
