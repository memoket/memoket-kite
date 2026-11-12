"""Output constraints for the multihop document reader."""

import re


_VALUE_QUESTION = re.compile(
    r"^(?:who|whom|whose|when|where|which|what|how (?:many|much|long))\b", re.IGNORECASE
)

# Keep the pattern compatible with schema providers that lack lookaround.
# All lengths except two and three can use the ordinary nonblank rule.
_VALUE_ANSWER_PATTERN = (
    r"^(?:\S|\S[\s\S]{2,}\S"
    r"|[^Nn\s]\S|[Nn][^Oo\s]"
    r"|[^Yy\s][\s\S]\S|[Yy][^Ee]\S|[Yy][Ee][^Ss\s])$"
)


def require_text_answer(response_format):
    """Constrain the final answer field on a per-request schema."""
    answer = response_format["json_schema"]["schema"]["properties"]["answer"]
    answer.update(
        pattern=r"^\S(?:[\s\S]*\S)?$",
        description=(
            "The final answer must contain text. Use No information if the evidence is "
            "insufficient. Never output a literal null, none, or an empty answer."
        ),
    )


def constrain_document_answer(question, response_format):
    require_text_answer(response_format)
    if _VALUE_QUESTION.match(question.lstrip()):
        answer = response_format["json_schema"]["schema"]["properties"]["answer"]
        answer["pattern"] = _VALUE_ANSWER_PATTERN
        answer["description"] += (
            " This question asks for a specific value. Output that value, not a yes/no response."
        )
