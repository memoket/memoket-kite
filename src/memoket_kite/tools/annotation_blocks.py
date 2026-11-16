"""Suggest contiguous source blocks for a host to annotate."""

from collections import Counter
from math import sqrt

from .context import _tokens


def annotation_blocks(conversation: dict, target_chars: int = 1800) -> list[dict]:
    segments = conversation["segments"]
    sizes = [0]
    words = []
    for segment in segments:
        sizes.append(sizes[-1] + len(segment["text"]))
        words.append(Counter(_tokens(segment["text"]).split()))

    def cohesion(split):
        before = sum(words[max(0, split - 5) : split], Counter())
        after = sum(words[split : split + 5], Counter())
        if min(before.total(), after.total()) < 8:
            return 1.0
        left, right = segments[split - 1], segments[split]
        if left.get("end_ms") is not None and right.get("start_ms", 0) - left["end_ms"] >= 15_000:
            return -1.0
        return sum(count * after[word] for word, count in before.items()) / sqrt(
            sum(n * n for n in before.values()) * sum(n * n for n in after.values())
        )

    blocks = []
    start = 0
    while start < len(segments):
        stop = start + 1
        while stop < len(segments) and sizes[stop] - sizes[start] < target_chars:
            stop += 1
        if stop < len(segments):
            # Pick a transition near the budget. Keep a short reply attached
            # to its preceding turn, and leave enough material on both sides.
            options = [
                pos
                for pos in range(start + 1, stop + 1)
                if sizes[pos] - sizes[start] >= target_chars * 0.55 and words[pos].total() >= 4
            ]
            if options:
                stop = min(options, key=lambda pos: (cohesion(pos), -pos))
        blocks.append(
            {
                "segments": segments[start:stop],
                "before": segments[max(0, start - 2) : start],
                "after": segments[stop : stop + 2],
            }
        )
        start = stop
    return blocks
