"""Link original turns around a topic match to a spoken revision exchange."""

import re

_DECISION_PREFERENCE = re.compile(
    r"\b(?:reject\w*|drop\w*|decis\w*|decid\w*|keep|final|confirm\w*|approv\w*|agr\w*)\b"
    r"|最终|确认|批准|通过|敲定",
    re.I,
)
_CHANGE = re.compile(
    r"\b(?:chang\w*|reduc\w*|adjust\w*|increas\w*|switch\w*|replac\w*)\b"
    r"|调整|改成|改为|换成|增至|减至",
    re.I,
)
_QUANTITY = re.compile(
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|twenty|half|zero)\b|[零一二三四五六七八九十百两]+\s*[片项个件套份名人日天]",
    re.I,
)
_ACK = re.compile(r"(?:yes|yeah|agreed|okay|ok|right|sure|同意|好|确认|可以)[\s.!。！]*", re.I)
_APPLIED = re.compile(
    r"\b(?:it|we|i|they|this|that)\s+(?:did\s+change|"
    r"(?:have\s+|has\s+|was\s+|were\s+)?(?:already\s+)?"
    r"(?:changed|reduced|revised|adjusted))\b|"
    r"(?:已|已经)(?:改为|改成|调整|变更|下调|上调)",
    re.I,
)
_NEGATED = re.compile(r"\b(?:not|never|\w+n['’]t)\b|尚未|没有|不曾|并未|不要|不能", re.I)


def _utterance(line):
    if line.who:
        return line.who.casefold(), line.text
    # Plain source-line imports can retain a dialogue label verbatim.
    name, sep, text = line.text.partition(":")
    if not sep:
        name, sep, text = line.text.partition("：")
    name = " ".join(name.split())
    if sep and len(name) <= 40 and name.replace(" ", "").isalpha():
        return name.casefold(), text
    return "", line.text


def find_revision_exchanges(store, queries, line_matches):
    """Find same-recording revisions, replies, and reported changes."""
    exchanges = {}
    for index, query in enumerate(queries):
        if not any(_DECISION_PREFERENCE.search(cue) for cue in query.get("prefer", ())):
            continue
        matches = line_matches.get(index, set())
        for subject in sorted(matches):
            recording = store.lines_by_unit[store.lines[subject].unit]
            # Line IDs hold the 1-based original position in this store.
            offset = int(subject.rpartition("L")[2]) - 1
            for pos in range(offset + 1, min(offset + 3, len(recording) - 1)):
                if any(line in matches for line in recording[offset + 1 : pos]):
                    continue
                revision, reply = recording[pos : pos + 2]
                author, text = _utterance(store.lines[revision])
                listener, response = _utterance(store.lines[reply])
                if (
                    not author
                    or not listener
                    or author == listener
                    or not _CHANGE.search(text)
                    or not _QUANTITY.search(text)
                    or _NEGATED.search(text)
                    or not _ACK.fullmatch(response.strip())
                ):
                    continue
                for followup in recording[pos + 2 : pos + 6]:
                    if followup in matches:
                        break
                    speaker, words = _utterance(store.lines[followup])
                    if (
                        speaker == listener
                        and _APPLIED.search(words)
                        and not _NEGATED.search(words)
                    ):
                        exchanges.setdefault((subject, revision, reply, followup), set()).add(index)
                        break
                    if speaker == listener and _NEGATED.search(words):
                        break
    return exchanges
