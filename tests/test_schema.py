"""The extraction contract: full schema, strict-mode schema, and the typed record."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from extraction_pipeline.schema import (
    API_SCHEMA,
    FULL_SCHEMA,
    GROUNDED_FIELDS,
    SCORED_FIELDS,
    UNSUPPORTED_BY_STRICT_MODE,
    StudyRecord,
    StudyType,
    extraction_tool,
    strip_for_strict_mode,
)
from tests.conftest import PROJECT_ROOT


def _keys(schema: Any, inside_properties: bool = False) -> set[str]:
    """Every JSON Schema keyword used anywhere (property names excluded)."""
    if isinstance(schema, list):
        return set().union(*(_keys(item) for item in schema)) if schema else set()
    if not isinstance(schema, dict):
        return set()
    found: set[str] = set()
    for key, value in schema.items():
        if inside_properties:
            found |= _keys(value)
        else:
            found.add(key)
            found |= _keys(value, inside_properties=key == "properties")
    return found


def _objects(schema: Any) -> list[dict[str, Any]]:
    if isinstance(schema, list):
        return [o for item in schema for o in _objects(item)]
    if not isinstance(schema, dict):
        return []
    here = [schema] if schema.get("type") == "object" else []
    return here + [o for value in schema.values() for o in _objects(value)]


def test_api_schema_uses_no_keyword_strict_mode_rejects():
    assert not (_keys(API_SCHEMA) & UNSUPPORTED_BY_STRICT_MODE)


def test_stripping_keeps_a_property_named_title():
    assert "title" in API_SCHEMA["properties"]
    assert "title" not in API_SCHEMA  # the schema's own title is dropped


def test_every_object_forbids_additional_properties():
    for obj in _objects(API_SCHEMA):
        assert obj["additionalProperties"] is False


def test_full_schema_keeps_the_constraints_enforced_locally():
    props = FULL_SCHEMA["properties"]
    assert props["doi"]["anyOf"][0]["pattern"].startswith("^10\\.")
    assert props["sample_size"]["anyOf"][0]["minimum"] == 1
    assert props["key_findings"]["maxItems"] == 5


def test_required_optional_and_nullable_fields():
    required = set(FULL_SCHEMA["required"])
    assert {"title", "authors", "study_type", "key_findings", "cited_works"} <= required
    assert {"keywords", "trial_registration"}.isdisjoint(required)  # optional
    for name in GROUNDED_FIELDS:  # required key, nullable value
        assert name in required
        assert {"type": "null"} in FULL_SCHEMA["properties"][name]["anyOf"]


def test_study_type_enum_has_other_plus_detail_pattern():
    assert "other" in FULL_SCHEMA["properties"]["study_type"]["enum"]
    assert "study_type_detail" in FULL_SCHEMA["properties"]


def test_confidence_and_evidence_cover_their_fields():
    props = FULL_SCHEMA["properties"]
    assert set(props["field_confidence"]["required"]) == set(SCORED_FIELDS)
    assert set(props["evidence"]["required"]) == set(GROUNDED_FIELDS)


def test_tool_definition_is_strict_by_default():
    assert extraction_tool()["strict"] is True
    assert "strict" not in extraction_tool(strict=False)


def test_published_schema_file_matches_the_code():
    published = json.loads((PROJECT_ROOT / "schemas" / "study_record.schema.json").read_text())

    assert published == FULL_SCHEMA


def test_strip_is_recursive_and_pure():
    schema = {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}}}

    stripped = strip_for_strict_mode(schema)

    assert stripped == {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert schema["properties"]["n"]["minimum"] == 1


def _record(**overrides: Any) -> dict[str, Any]:
    base = {
        "title": "A study",
        "authors": ["Ana Silva"],
        "publication_year": 2020,
        "doi": None,
        "study_type": "cohort_study",
        "study_type_detail": None,
        "sample_size": 10,
        "funding_source": None,
        "primary_outcome": None,
        "key_findings": ["It worked."],
        "cited_works": [],
        "field_confidence": dict.fromkeys(SCORED_FIELDS, 0.9),
        "evidence": dict.fromkeys(GROUNDED_FIELDS),
    }
    return base | overrides


def test_record_requires_detail_for_other():
    with pytest.raises(ValidationError, match="study_type_detail is required"):
        StudyRecord.model_validate(_record(study_type="other"))

    record = StudyRecord.model_validate(_record(study_type="other", study_type_detail="Delphi"))
    assert record.study_type is StudyType.OTHER


def test_record_rejects_confidence_outside_unit_interval():
    confidence = dict.fromkeys(SCORED_FIELDS, 0.9) | {"doi": 1.4}

    with pytest.raises(ValidationError, match="out of"):
        StudyRecord.model_validate(_record(field_confidence=confidence))
