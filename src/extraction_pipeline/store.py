"""Persist a run: the files downstream systems and reviewers consume.

runs/<run_id>/
├── run.json            settings, timings, SLA report, cost
├── results.jsonl       one line per document: status, extraction, attempts, events
├── records.jsonl       auto-accepted records, validated against the published schema
├── review_queue.jsonl  documents routed to a human, with reasons and fields to check
├── evaluation.json     accuracy against ground truth (when ground truth exists)
└── report.md           the human-readable summary
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from extraction_pipeline.config import Settings


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def settings_snapshot(settings: Settings) -> dict[str, Any]:
    return {k: _jsonable(v) for k, v in asdict(settings).items()}


class RunStore:
    def __init__(self, root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.path = root / run_id
        self.path.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, data: Any) -> Path:
        target = self.path / name
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")
        return target

    def write_jsonl(self, name: str, rows: Iterable[dict[str, Any]]) -> Path:
        target = self.path / name
        with target.open("w", encoding="utf-8") as out:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return target

    def write_text(self, name: str, text: str) -> Path:
        target = self.path / name
        target.write_text(text, encoding="utf-8")
        return target

    @staticmethod
    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        with path.open(encoding="utf-8") as lines:
            return [json.loads(line) for line in lines if line.strip()]
