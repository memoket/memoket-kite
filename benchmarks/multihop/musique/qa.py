"""Run native whole-passage retrieval and one Answer on a frozen MuSiQue index."""

from benchmarks.multihop import compile_contract, passage_qa, reader_contract
from benchmarks.multihop.compile_contract import decode, response_format
from benchmarks.multihop.passage_index import render_sources


INSTRUCTION = (
    compile_contract.INSTRUCTION
    + "Grep matching is case-insensitive. Do not add global inline flags such as (?i). "
    "In a stage's units filter, use an explicit earlier-stage projection such as $s1.units.\n"
)
__all__ = ["INSTRUCTION", "decode", "response_format"]


def render_compile_feedback(rows, store):
    return [
        *(row["text"] for row in rows if row["type"] == "count"),
        *(f"[{source['id']}]\n{source['text']}" for source in render_sources(rows, store)),
    ]


def configure_profile(profile):
    from benchmarks.multihop.musique import qa

    profile.COMPILE_CONTRACT = qa
    profile.COMPILE_REVIEW_SUBJECT_SCOPE = True
    profile.COMPILE_MAX_ROUNDS = 2
    profile.DEFAULT_BUDGET = 40
    profile.COMPILE_FEEDBACK_RENDERER = qa.render_compile_feedback
    profile.BIND_STAGE_DATES = False
    profile.RANK_STAGE_ROWS = True
    profile.RESERVE_STAGE_UNITS = True
    profile.COMPILE_RETAIN_PREVIOUS_CANDIDATES = True


def render_answer(result, store, question, schema):
    prompt, response_format, sources = passage_qa.render_distinct_passages(result, store, question, schema)
    reader_contract.constrain_document_answer(question, response_format)
    return prompt, response_format, sources


def initialize(root, env_file):
    passage_qa.init_worker(
        root, env_file, configure_profile=configure_profile,
        render_answer=render_answer,
    )


if __name__ == "__main__":
    from benchmarks.multihop.musique.qa import initialize as initialize_worker

    passage_qa.cli(initializer=initialize_worker)
