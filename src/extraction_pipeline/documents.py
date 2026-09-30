"""Loading source documents and their ground truth, and splitting oversized ones."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TokenCounter = Callable[[str], int]


def approx_tokens(text: str) -> int:
    """Conservative offline estimate (~3.5 characters per token for English prose)."""
    return max(1, round(len(text) / 3.5))


@dataclass(frozen=True)
class Document:
    doc_id: str
    doc_type: str
    text: str

    @classmethod
    def from_path(cls, path: Path, doc_type: str = "unknown") -> Document:
        return cls(doc_id=path.stem, doc_type=doc_type, text=path.read_text(encoding="utf-8"))


def load_ground_truth(data_dir: Path) -> dict[str, dict[str, Any]]:
    path = data_dir / "ground_truth.jsonl"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as lines:
        return {row["doc_id"]: row for row in map(json.loads, lines) if row}


def load_corpus(data_dir: Path, limit: int | None = None) -> list[Document]:
    truth = load_ground_truth(data_dir)
    paths = sorted((data_dir / "documents").glob("*.md"))[:limit]
    return [Document.from_path(p, truth.get(p.stem, {}).get("doc_type", "unknown")) for p in paths]


def split_into_chunks(
    text: str, budget_tokens: int, count: TokenCounter = approx_tokens
) -> list[str]:
    """Split on line boundaries into chunks of at most `budget_tokens` each.

    Lines are never cut in half, so a table row or a reference entry always lands whole in one
    chunk. A single line longer than the budget becomes its own chunk.
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        cost = count(line) + 1
        if current and size + cost > budget_tokens:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += cost
    if current:
        chunks.append("\n".join(current))
    return chunks
