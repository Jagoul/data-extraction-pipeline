"""The extraction contract: one field catalogue, two JSON Schemas, and the typed record.

`FULL_SCHEMA` is the complete contract, used for client-side validation and published to
downstream consumers (`schemas/study_record.schema.json`). `API_SCHEMA` is the same schema with
the keywords that strict tool use does not support removed (numeric and length bounds, patterns,
array size limits, conditionals). The API guarantees the structure of `API_SCHEMA`; this package
enforces everything else after the response arrives.
"""

from __future__ import annotations

import copy
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

TOOL_NAME: Final = "record_study"


class StudyType(StrEnum):
    RANDOMIZED_CONTROLLED_TRIAL = "randomized_controlled_trial"
    COHORT_STUDY = "cohort_study"
    CASE_CONTROL_STUDY = "case_control_study"
    CROSS_SECTIONAL_STUDY = "cross_sectional_study"
    SYSTEMATIC_REVIEW = "systematic_review"
    META_ANALYSIS = "meta_analysis"
    QUALITATIVE_STUDY = "qualitative_study"
    OTHER = "other"


# Fields the model scores with a confidence in [0, 1]; these drive human-review routing.
SCORED_FIELDS: Final = (
    "title",
    "authors",
    "publication_year",
    "doi",
    "study_type",
    "sample_size",
    "funding_source",
    "primary_outcome",
)

# Nullable fields that must quote the sentence they came from. A value without a verbatim quote
# from the document is treated as possibly fabricated.
GROUNDED_FIELDS: Final = (
    "publication_year",
    "doi",
    "sample_size",
    "funding_source",
    "primary_outcome",
)

DOI_PATTERN: Final = r"^10\.\d{4,9}/\S+$"
TRIAL_REGISTRATION_PATTERN: Final = r"^NCT\d{8}$"


def _nullable(schema: dict[str, Any], description: str) -> dict[str, Any]:
    return {"description": description, "anyOf": [schema, {"type": "null"}]}


def _build_full_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "title": {
            "type": "string",
            "minLength": 1,
            "description": "Full title of the study, copied verbatim.",
        },
        "authors": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
            "description": "Every author, in document order, as written (e.g. 'Maria Silva').",
        },
        "publication_year": _nullable(
            {"type": "integer", "minimum": 1900, "maximum": 2100},
            "Four-digit year of publication. null if the document does not state it.",
        ),
        "doi": _nullable(
            {"type": "string", "pattern": DOI_PATTERN},
            "Bare DOI starting with '10.' (strip any https://doi.org/ prefix). "
            "null if the document has no DOI.",
        ),
        "study_type": {
            "type": "string",
            "enum": [t.value for t in StudyType],
            "description": "Study design. Use 'other' only when no listed design fits, "
            "and then describe it in study_type_detail.",
        },
        "study_type_detail": _nullable(
            {"type": "string", "minLength": 3},
            "Required when study_type is 'other': the design as the document names it "
            "(e.g. 'Delphi consensus study'). Otherwise null.",
        ),
        "sample_size": _nullable(
            {"type": "integer", "minimum": 1},
            "Number of participants analysed, as an integer. null if not reported.",
        ),
        "funding_source": _nullable(
            {"type": "string", "minLength": 2},
            "Who funded the study. null if the document does not say.",
        ),
        "primary_outcome": _nullable(
            {"type": "string", "minLength": 3},
            "The primary outcome measure. null if not stated.",
        ),
        "key_findings": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string", "minLength": 3},
            "description": "One to five main findings, each one sentence.",
        },
        "cited_works": {
            "type": "array",
            "description": "Every work the document cites, whether as inline author-year "
            "citations or as a numbered reference list. Empty if it cites nothing.",
            "items": {
                "type": "object",
                "properties": {
                    "first_author": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Surname of the first author.",
                    },
                    "year": _nullable(
                        {"type": "integer", "minimum": 1900, "maximum": 2100},
                        "Year of the cited work, or null if not given.",
                    ),
                },
                "required": ["first_author", "year"],
                "additionalProperties": False,
            },
        },
        "keywords": {
            "type": "array",
            "maxItems": 10,
            "items": {"type": "string", "minLength": 1},
            "description": "Optional. Keywords the document lists explicitly.",
        },
        "trial_registration": _nullable(
            {"type": "string", "pattern": TRIAL_REGISTRATION_PATTERN},
            "Optional. ClinicalTrials.gov ID such as NCT01234567, or null.",
        ),
        "field_confidence": {
            "type": "object",
            "description": "Your confidence from 0.0 to 1.0 that each field is correct, "
            "including when the correct value is null.",
            "properties": {
                name: {"type": "number", "minimum": 0, "maximum": 1} for name in SCORED_FIELDS
            },
            "required": list(SCORED_FIELDS),
            "additionalProperties": False,
        },
        "evidence": {
            "type": "object",
            "description": "For each field, the exact sentence or table cell from the document "
            "that supports its value, copied verbatim. null when the field is null.",
            "properties": {
                name: _nullable({"type": "string", "minLength": 3}, f"Verbatim quote for {name}.")
                for name in GROUNDED_FIELDS
            },
            "required": list(GROUNDED_FIELDS),
            "additionalProperties": False,
        },
    }
    optional = {"keywords", "trial_registration"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://github.com/Jagoul/structured-data-extraction-pipeline"
            "/schemas/study_record.schema.json"
        ),
        "title": "StudyRecord",
        "type": "object",
        "properties": properties,
        "required": [name for name in properties if name not in optional],
        "additionalProperties": False,
        "allOf": [
            {
                "if": {"properties": {"study_type": {"const": "other"}}},
                "then": {"properties": {"study_type_detail": {"type": "string"}}},
            }
        ],
    }


# Keywords strict tool use does not accept. They stay in FULL_SCHEMA and are enforced locally.
UNSUPPORTED_BY_STRICT_MODE: Final = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "allOf",
        "if",
        "then",
        "else",
    }
)


def strip_for_strict_mode(schema: Any, *, root: bool = True) -> Any:
    """Return a copy of `schema` without the keywords strict tool use rejects.

    `title` is only dropped at the root, where it names the schema; inside `properties` a field
    called "title" is data and must be kept.
    """
    if isinstance(schema, list):
        return [strip_for_strict_mode(item, root=False) for item in schema]
    if not isinstance(schema, dict):
        return schema
    stripped: dict[str, Any] = {}
    for key, value in schema.items():
        if key in UNSUPPORTED_BY_STRICT_MODE and (key != "title" or root):
            continue
        if key == "properties":
            stripped[key] = {
                name: strip_for_strict_mode(sub, root=False) for name, sub in value.items()
            }
        else:
            stripped[key] = strip_for_strict_mode(value, root=False)
    return stripped


FULL_SCHEMA: Final[dict[str, Any]] = _build_full_schema()
API_SCHEMA: Final[dict[str, Any]] = strip_for_strict_mode(copy.deepcopy(FULL_SCHEMA))

TOOL_DESCRIPTION: Final = (
    "Record the structured data extracted from ONE research document. Call this tool exactly "
    "once per document, with every required field. Use null for any nullable field the "
    "document does not state; never infer, estimate, or invent a value. For every non-null "
    "field listed under `evidence`, copy the supporting sentence or table cell verbatim."
)


def extraction_tool(*, strict: bool = True) -> dict[str, Any]:
    """The tool definition sent to the Messages API."""
    tool: dict[str, Any] = {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": API_SCHEMA,
    }
    if strict:
        tool["strict"] = True
    return tool


# --- Typed record: what downstream systems consume ---------------------------------------------


class CitedWork(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    first_author: str = Field(min_length=1)
    year: int | None = Field(default=None, ge=1900, le=2100)


class StudyRecord(BaseModel):
    """A validated extraction. Mirrors FULL_SCHEMA; construction fails on any violation."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    publication_year: int | None = Field(ge=1900, le=2100)
    doi: str | None = Field(pattern=DOI_PATTERN)
    study_type: StudyType
    study_type_detail: str | None
    sample_size: int | None = Field(ge=1)
    funding_source: str | None
    primary_outcome: str | None
    key_findings: list[str] = Field(min_length=1, max_length=5)
    cited_works: list[CitedWork]
    keywords: list[str] = Field(default_factory=list, max_length=10)
    trial_registration: str | None = Field(default=None, pattern=TRIAL_REGISTRATION_PATTERN)
    field_confidence: dict[str, float]
    evidence: dict[str, str | None]

    @model_validator(mode="after")
    def other_requires_detail(self) -> StudyRecord:
        if self.study_type is StudyType.OTHER and not (self.study_type_detail or "").strip():
            raise ValueError("study_type_detail is required when study_type is 'other'")
        return self

    @model_validator(mode="after")
    def confidence_covers_scored_fields(self) -> StudyRecord:
        missing = [name for name in SCORED_FIELDS if name not in self.field_confidence]
        if missing:
            raise ValueError(f"field_confidence is missing {missing}")
        out_of_range = [k for k, v in self.field_confidence.items() if not 0.0 <= v <= 1.0]
        if out_of_range:
            raise ValueError(f"field_confidence out of [0, 1] for {out_of_range}")
        return self
