"""Map fixed-size chunks to original turns and short display previews."""

from bisect import bisect_right

CHUNK_CHARACTERS = 600
PREVIEW_CHARACTERS = 16


def chunk_geometry(workspace, checksums: set[str] | None = None) -> dict:
    records = workspace.records() if workspace is not None else []
    sources = []
    total = 0
    for record in records:
        if checksums is not None and record["checksum"] not in checksums:
            continue
        starts = []
        parts = []
        size = 0
        for segment in record["conversation"]["segments"]:
            starts.append(size)
            text = segment["text"]
            parts.append(text)
            size += len(text) + 1
        transcript = "\n".join(parts)
        chunks = [
            (
                bisect_right(starts, start) - 1,
                " ".join(transcript[start : start + CHUNK_CHARACTERS].split())[:PREVIEW_CHARACTERS],
            )
            for start in range(0, len(transcript), CHUNK_CHARACTERS)
        ]
        sources.append({"checksum": record["checksum"], "chunks": chunks})
        total += len(chunks)
    return {
        "source_count": len(sources),
        "total_chunks": total,
        "chunk_characters": CHUNK_CHARACTERS,
        "search_scope": checksums is not None,
        "sources": sources,
    }
