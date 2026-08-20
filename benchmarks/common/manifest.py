"""Record which code produced a result directory.

A score is only meaningful next to the tree that produced it. Without this, a
number outlives the commit it was measured on and gets compared against a later
tree whose binding constants no longer produce it.

`dirty: true` alone cannot restore that tree, so an uncommitted run also stores
the working diff next to the manifest and hashes it here.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from benchmarks.common import settings
from memoket_kite.pipeline.compile_plan import _provider_cache_identity
from memoket_kite.pipeline.ledger import tokenizer_name as _tokenizer_name

# Binding constants whose value changes what the pipeline does. Recording them
# turns "this run scored X" into "this configuration scored X".
#: The knobs a run records, taken from the settings table so the two lists
#: cannot drift: a binding cannot set something the manifest would not report.
RECORDED = settings.RECORDED

# Prompts are the other half of the configuration: a reworded template changes
# behaviour without changing a single flag.
HASHED_PROMPTS = (
    "ANSWER_PROMPT",
    "COMPILE_PROMPT",
    "EXTRACT_PROMPT",
    "INFER_PROMPT",
)

#: The library modules whose templates reach a model.
HASHED_LIBRARY_PROMPTS = (
    "memoket_kite.prompts.answer",
    "memoket_kite.prompts.extract",
    "memoket_kite.prompts.recall",
)

#: A template long enough to carry instructions, rather than a label or a key.
_TEMPLATE_FLOOR = 40


def _library_prompt_shas() -> dict[str, str]:
    """Every template the library can send, digested under its own name.

    The modules are named and their contents enumerated: naming each template
    instead would fingerprint whichever ones someone remembered to list.
    """
    import importlib

    shas = {}
    for module_name in HASHED_LIBRARY_PROMPTS:
        module = importlib.import_module(module_name)
        for attribute in dir(module):
            if attribute.startswith("__"):
                continue
            text = getattr(module, attribute, None)
            if isinstance(text, str) and len(text) >= _TEMPLATE_FLOOR:
                shas[f"{module_name}:{attribute}"] = _digest(text)
    return shas


def working_diff() -> str:
    """The uncommitted diff, exactly as it is stored and digested.

    `_git()` strips, and `git apply` rejects a patch with no trailing newline.
    Restoring it in one place keeps `diff_sha` a digest OF THE STORED FILE
    rather than of a slightly different string.
    """
    diff = _git("diff", "HEAD")
    return diff + "\n" if diff else ""


def untracked_files() -> tuple[str, ...]:
    """Non-ignored paths git is not tracking."""
    return tuple(
        line for line in _git("ls-files", "--others", "--exclude-standard").splitlines() if line
    )


class GitUnavailable(RuntimeError):
    """Raised when the tree's identity cannot be established."""


#: The tree every provenance question is asked about.
_REPO = Path(__file__).resolve().parent


def _git(*args: str) -> str:
    """Ask git, or refuse to answer.

    Returning an empty string on failure would produce a manifest asserting a
    clean run at commit "", which is worse than no manifest: two runs on
    unrelated trees then record the same empty identity, and the resume check
    compares them as equal. A run whose provenance cannot be established has to
    say so, so failure raises instead of degrading.
    """
    try:
        completed = subprocess.run(
            ("git", *args),
            capture_output=True,
            text=True,
            cwd=_REPO,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GitUnavailable(
            f"cannot record provenance: `git {' '.join(args)}` failed ({error}). "
            f"A score whose tree cannot be identified must not be published."
        ) from error
    return completed.stdout.strip()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def corpus_digest(paths) -> str:
    """Content digest of the dataset and codebooks a run reads.

    Code identity does not cover the corpus: rebuilding a codebook changes the
    answers without changing a line of source.

    Both evaluators open codebooks as `<dir>/<id>.xml` and never recurse, so
    the digest covers exactly that flat set — dotfiles and nested directories
    are outside what a run can read. Hashing the relative path alongside the
    bytes is what makes a *move* visible: over basenames alone, `q.xml` and
    `.trash/q.xml` are indistinguishable, so a codebook the run needs could
    disappear from the flat set without the corpus identity changing.
    """
    running = hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        if path.is_dir():
            members = sorted(
                member
                for member in path.glob("*.xml")
                if member.is_file() and not member.name.startswith(".")
            )
        else:
            members = [path]
        for member in members:
            running.update(str(member.relative_to(path.parent)).encode())
            running.update(hashlib.sha256(member.read_bytes()).digest())
    return running.hexdigest()[:16]


def plan_cache_digest(path) -> str:
    """Content identity of the compiled-plan cache a run reads.

    A run compiles a plan it does not find, and writes it into the cache it is
    reading. Recording only the path leaves that invisible, so two runs can
    claim the same cache and have read different things. A replicate that
    claims the same cache has to be able to prove it.
    """
    if not path:
        return ""
    folder = Path(path)
    if not folder.is_dir():
        return ""
    running = hashlib.sha256()
    for entry in sorted(folder.glob("*.json")):
        running.update(entry.name.encode())
        running.update(hashlib.sha256(entry.read_bytes()).digest())
    return running.hexdigest()[:32]


def question_digest(question_ids) -> str:
    """Identity of the exact question set a run covers.

    A count cannot tell a complete run from one that answered the right number
    of the wrong questions.
    """
    ordered = sorted(str(qid) for qid in question_ids)
    payload = "".join(f"{len(qid)}:{qid}" for qid in ordered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def describe(profile, **extra) -> dict:
    """The configuration fingerprint, without touching disk."""
    diff = working_diff()
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "diff_sha": _digest(diff) if diff else "",
        "library_prompt_sha": _library_prompt_shas(),
        "binding": profile.__name__,
        # Sequences are normalised to lists: a tuple survives `json.dumps` as a
        # list, so recording one verbatim makes every later resume compare a
        # tuple against a list and refuse a run that did not change.
        "config": {
            name: list(value) if isinstance(value, tuple) else value
            for name in dict.fromkeys(RECORDED)
            if not callable(value := getattr(profile, name, None)) and value is not None
        },
        "prompt_sha": {
            name: _digest(text)
            for name in HASHED_PROMPTS
            if isinstance(text := getattr(profile, name, None), str)
        }
        # A binding's own predicates change which questions the refusal rules
        # and the recency channel treat specially, so a score names them the
        # same way it names the prompts it ran under.
        | {
            name: _digest(pattern.pattern)
            for name in ("ADVICE_QUESTION", "_BACKREFERENCE")
            if (pattern := getattr(profile, name, None)) is not None and hasattr(pattern, "pattern")
        }
        # A controlled vocabulary bounds what the extractor may emit, so moving
        # one changes the Codebook a score was computed over as surely as
        # rewording a prompt does. Fingerprinted rather than copied: the lists
        # are long, and what a run has to state is that they did not move.
        | {
            # Order is part of the value for a sequence: the taxonomy roots and
            # the fact kinds are rendered into the extraction prompt in the
            # order they are declared, so a permutation changes what the model
            # sees. A set has no order to preserve, so it is sorted to make the
            # digest deterministic across runs.
            name: _digest(
                " ".join(
                    sorted(map(str, vocabulary))
                    if isinstance(vocabulary, (set, frozenset))
                    else map(str, vocabulary)
                )
            )
            for name in settings.FINGERPRINTED
            if (vocabulary := getattr(profile, name, None))
        },
        "answerable_predicate": callable(getattr(profile, "ANSWERABLE_BY_CONSTRUCTION", None)),
        "env_overrides": {
            key: value for key, value in sorted(os.environ.items()) if key.startswith("KITE_")
        },
        # The model name does not identify the service that answers to it: the
        # same name on two OpenAI-compatible endpoints is two systems.
        "provider": _provider_cache_identity(),
        # The counter that decides which rows fit the budget. `chars//4` admits
        # a different pack than `o200k_base`, so two runs under different
        # tokenizers are different systems, not one system measured twice.
        "tokenizer": _tokenizer_name(),
        **extra,
    }


# Everything that can make two halves of one JSONL come from different
# systems. Anything recorded but left out here is a way to resume across a
# change without noticing.
_IDENTITY = (
    "commit",
    "diff_sha",
    "config",
    "prompt_sha",
    "library_prompt_sha",
    "env_overrides",
    "model",
    "answer_model",
    "plan_cache",
    "n",
    "samples",
    "provider",
    "corpus_sha",
    "effective_n",
    "selected_sha",
    "tokenizer",
    "plan_cache_sha",
)

#: Identity fields a legitimate resume is allowed to have changed.
#:
#: The plan cache is written BY the run: any question that misses the cache
#: compiles a plan and stores it, so a run interrupted after even one miss
#: resumes against a digest its own first half produced. Treating that as a
#: different system would make `--resume --plan-cache` refuse exactly the
#: interrupted runs the pair of flags exists for. The digest is still recorded
#: on every write; it simply does not take part in the identity comparison.
_SELF_WRITTEN = frozenset({"plan_cache_sha"})


def _comparable(manifest: dict) -> dict:
    return {key: manifest.get(key) for key in _IDENTITY if key not in _SELF_WRITTEN}


def write(result_dir: Path, profile, *, resuming: bool = False, **extra) -> dict:
    """Write `manifest.json` beside the results and return what it recorded.

    Call this only once the run is committed to writing, so a mistaken
    invocation on an existing tag cannot replace the provenance of results it
    never produced. Resuming into a directory whose manifest describes a
    different configuration is refused: the two halves of the JSONL would come
    from different systems and nothing downstream could tell them apart.
    """
    if untracked := untracked_files():
        # `working.diff` is `git diff HEAD`, which sees tracked files only. A
        # tree holding an untracked module would record `dirty: true` with an
        # empty patch and nothing to rebuild it from, so the run stops here —
        # before the first question is paid for — rather than producing a
        # score no one can reproduce.
        listed = ", ".join(untracked[:5]) + ("..." if len(untracked) > 5 else "")
        raise SystemExit(
            f"cannot record provenance: {len(untracked)} untracked file(s) in the tree "
            f"({listed}). The stored patch covers tracked files only. Commit them, "
            f"remove them, or ignore them, then start the run."
        )
    current = describe(profile, **extra)
    path = result_dir / "manifest.json"
    if resuming:
        if not path.exists():
            # Results without a manifest have unknown provenance. Signing them
            # with today's configuration would invent the evidence.
            raise SystemExit(
                f"cannot resume {result_dir.name}: it holds results but no manifest, "
                f"so what produced them is unknown. Use a new --tag."
            )
        previous = json.loads(path.read_text())
        differing = sorted(
            key
            for key, value in _comparable(current).items()
            if _comparable(previous).get(key) != value
        )
        if differing:
            # Refuse WITHOUT rewriting: the old manifest still describes the
            # rows already in the file.
            raise SystemExit(
                f"cannot resume {result_dir.name}: {', '.join(differing)} changed since "
                f"it was started (see {path}). Use a new --tag."
            )
    from benchmarks.common.publish import atomic_write

    # A half-written manifest is a run with no recoverable identity, and
    # `--resume` rewrites this file on every restart.
    atomic_write(path, json.dumps(current, indent=2) + "\n")
    if current["dirty"] and (diff := working_diff()):
        # Stored with the trailing newline `_git()` strips, because `git apply`
        # rejects a patch that lacks one ("corrupt patch") and reconstructing
        # the tree that produced a score is the only thing this file is for.
        atomic_write(result_dir / "working.diff", diff)
    return current


def seal_plan_cache(result_dir: Path, plan_cache) -> None:
    """Re-record the plan-cache digest once the run has stopped writing to it.

    The digest taken at run start describes the cache the run READ; compilation
    then writes newly compiled plans into that same cache, so what the release
    ships is the cache the run LEFT. Only the second one can be verified
    against the shipped artifact, and `plan_cache_sha` is in `_SELF_WRITTEN`
    precisely so re-recording it cannot disturb the --resume identity check.
    """
    from benchmarks.common.publish import atomic_write

    path = result_dir / "manifest.json"
    if not path.exists():
        return
    recorded = json.loads(path.read_text(encoding="utf-8"))
    recorded["plan_cache_sha"] = plan_cache_digest(plan_cache)
    atomic_write(path, json.dumps(recorded, indent=2, sort_keys=True) + "\n")
