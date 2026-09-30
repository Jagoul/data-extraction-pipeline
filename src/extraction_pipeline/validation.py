"""Validate an extraction and classify every problem by whether a retry can fix it.

Three layers run in order:

1. **JSON Schema** (`FULL_SCHEMA`, Draft 2020-12) catches everything strict mode can't enforce
   server-side: patterns, numeric bounds, array sizes, the `other` + detail rule.
2. **Typed record** (`StudyRecord`) catches cross-field rules the schema can't express.
3. **Grounding** checks each non-null value against the source document. A value with no
   verbatim quote, or a quote that isn't in the document, is treated as possibly fabricated.

Each issue gets an `IssueKind`, which decides the retry policy:

| Kind | Typical cause | Retry? |
|---|---|---|
| FORMAT | wrong shape: "https://doi.org/10.x", "240 participants", year 21 | yes |
| UNGROUNDED | value not supported by the text | yes, once: quote it or return null |
| MISSING_REQUIRED | a required field the document simply doesn't contain | no: route to a human |
| INCOMPLETE | far fewer cited works than the document has numbered references | no: chunk it |
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from pydantic import ValidationError as PydanticError

from extraction_pipeline.schema import FULL_SCHEMA, GROUNDED_FIELDS, StudyRecord


class IssueKind(StrEnum):
    FORMAT = "format"
    UNGROUNDED = "ungrounded"
    MISSING_REQUIRED = "missing_required"
    INCOMPLETE = "incomplete"

    @property
    def retryable(self) -> bool:
        """MISSING_REQUIRED can't be fixed by asking again. INCOMPLETE is fixed by chunking, not
        by a retry: the same request would come back just as short."""
        return self not in (IssueKind.MISSING_REQUIRED, IssueKind.INCOMPLETE)


@dataclass(frozen=True)
class Issue:
    field: str
    kind: IssueKind
    message: str

    def render(self) -> str:
        return f"- {self.field} [{self.kind}]: {self.message}"


@dataclass(frozen=True)
class ValidationOutcome:
    record: StudyRecord | None
    issues: tuple[Issue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    @property
    def retryable(self) -> bool:
        """True when at least one issue is of a kind a retry can plausibly fix."""
        return any(issue.kind.retryable for issue in self.issues)

    def kinds(self) -> set[IssueKind]:
        return {issue.kind for issue in self.issues}


_SCHEMA_VALIDATOR = Draft202012Validator(FULL_SCHEMA)
# Typographic characters folded to ASCII before comparing quotes with the document.
SMART_QUOTES = "".join(map(chr, (0x2018, 0x2019, 0x201C, 0x201D)))
DASHES = "".join(map(chr, range(0x2010, 0x2016)))
PLACEHOLDERS = frozenset(
    {"", "unknown", "n/a", "na", "none", "not reported", "not stated", "anonymous", "untitled"}
)


# --- Text normalisation for grounding checks ---------------------------------------------------


def normalise(text: str) -> str:
    """Lower-case, unify quotes and dashes, drop table pipes, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(f"[{SMART_QUOTES}]", "'", text).replace('"', "'")
    text = re.sub(f"[{DASHES}]", "-", text)
    text = text.replace("|", " ").replace("*", " ")
    return re.sub(r"\s+", " ", text).strip()


def _digits(value: int) -> set[str]:
    """Ways an integer may be written in a document: 1204, 1,204, 1 204."""
    plain = str(value)
    return {plain, f"{value:,}", f"{value:,}".replace(",", " ")}


DOI_OR_URL = re.compile(r"https?://\S+|doi:\s*\S+|\b10\.\d{4,9}/\S+")


def _consistent(field: str, value: Any, quote: str) -> bool:
    """Does the verbatim quote actually support the value?"""
    q = normalise(quote)
    if field == "publication_year":
        # A year buried in a DOI or URL (10.5555/jace.2014.7) is an identifier, not a date.
        return re.search(rf"(?<!\d){int(value)}(?!\d)", DOI_OR_URL.sub(" ", q)) is not None
    if field == "sample_size":
        return any(form in q for form in _digits(int(value)))
    if field == "doi":
        return normalise(str(value)) in q
    return True  # free-text fields: presence of the quote in the document is the check


# --- Layers ------------------------------------------------------------------------------------


def _field_of(error: SchemaError) -> str:
    path = [str(part) for part in error.absolute_path]
    if error.validator == "required" and isinstance(error.message, str):
        match = re.match(r"'([^']+)' is a required property", error.message)
        if match:
            path.append(match.group(1))
    return ".".join(path) or "(root)"


def _actionable_message(error: SchemaError) -> str:
    """Say which constraint failed and what is expected, so a retry can fix it.

    For a nullable field (`anyOf: [<schema>, null]`) jsonschema only reports "not valid under
    any of the given schemas". The useful message is the one from the non-null branch.
    """
    message = error.message
    if error.validator == "anyOf" and error.context:
        non_null = [e for e in error.context if e.schema != {"type": "null"}]
        if non_null:
            message = non_null[0].message
    description = error.schema.get("description") if isinstance(error.schema, Mapping) else None
    return f"{message}. Expected: {description}" if description else message


def schema_issues(data: dict[str, Any]) -> list[Issue]:
    issues = []
    for error in sorted(_SCHEMA_VALIDATOR.iter_errors(data), key=lambda e: list(e.path)):
        if error.validator == "allOf":
            continue  # reported more precisely by the record's other-requires-detail rule
        issues.append(Issue(_field_of(error), IssueKind.FORMAT, _actionable_message(error)))
    return issues


def record_issues(data: dict[str, Any]) -> tuple[StudyRecord | None, list[Issue]]:
    try:
        return StudyRecord.model_validate(data), []
    except PydanticError as exc:
        issues = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "(record)"
            issues.append(Issue(field, IssueKind.FORMAT, err["msg"]))
        return None, issues


def missing_required_issues(data: dict[str, Any]) -> list[Issue]:
    """Required fields filled with a placeholder because the document doesn't contain them."""
    issues = []
    title = data.get("title")
    if isinstance(title, str) and title.strip().lower() in PLACEHOLDERS:
        issues.append(Issue("title", IssueKind.MISSING_REQUIRED, "the document states no title"))
    authors = data.get("authors")
    if isinstance(authors, list) and (
        not authors or all(str(a).strip().lower() in PLACEHOLDERS for a in authors)
    ):
        issues.append(Issue("authors", IssueKind.MISSING_REQUIRED, "the document names no authors"))
    return issues


def grounding_issues(
    data: dict[str, Any], document: str, skip: frozenset[str] = frozenset()
) -> list[Issue]:
    """`skip` holds fields already failing format checks: a malformed value can't be grounded."""
    evidence = data.get("evidence")
    if not isinstance(evidence, dict):
        return []  # structural problem, already reported by the schema layer
    source = normalise(document)
    issues = []
    for field in GROUNDED_FIELDS:
        value = data.get(field)
        if value is None or field in skip:
            continue
        quote = evidence.get(field)
        if not isinstance(quote, str) or not quote.strip():
            issues.append(
                Issue(field, IssueKind.UNGROUNDED, "non-null value with no evidence quote")
            )
        elif normalise(quote) not in source:
            issues.append(
                Issue(
                    field,
                    IssueKind.UNGROUNDED,
                    f"evidence quote {quote!r} does not appear in the document",
                )
            )
        elif not _consistent(field, value, quote):
            issues.append(
                Issue(
                    field,
                    IssueKind.UNGROUNDED,
                    f"value {value!r} is not supported by its evidence quote {quote!r}",
                )
            )
    return issues


MIN_NUMBERED_REFERENCES = 10
COMPLETENESS_RATIO = 0.95


def completeness_issues(data: dict[str, Any], document: str) -> list[Issue]:
    """Flag a `cited_works` list far shorter than the document's numbered reference list.

    A model can stop listing references early and still finish normally (no `max_tokens`), so
    the response gives no truncation signal. Counting numbered entries in the source is a cheap
    independent check. It only applies to long numbered lists, so short ones never trigger it.
    """
    cited = data.get("cited_works")
    if not isinstance(cited, list):
        return []
    entries = len(re.findall(r"^\s*\d+\.\s", document, flags=re.MULTILINE))
    if entries >= MIN_NUMBERED_REFERENCES and len(cited) < COMPLETENESS_RATIO * entries:
        return [
            Issue(
                "cited_works",
                IssueKind.INCOMPLETE,
                f"{len(cited)} cited works extracted, but the document has about {entries} "
                "numbered reference entries",
            )
        ]
    return []


def _dedupe(issues: Iterable[Issue]) -> tuple[Issue, ...]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for issue in issues:
        key = (issue.field, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return tuple(unique)


def validate_extraction(data: dict[str, Any], document: str) -> ValidationOutcome:
    """Run every layer. `record` is set only when there are no issues at all."""
    issues = schema_issues(data)
    record, typed = record_issues(data)
    # The typed layer re-reports schema violations in its own words; keep only what's new.
    seen_fields = {issue.field.split(".")[0] for issue in issues}
    issues += [issue for issue in typed if issue.field.split(".")[0] not in seen_fields]
    issues += missing_required_issues(data)
    malformed = frozenset(i.field.split(".")[0] for i in issues if i.kind is IssueKind.FORMAT)
    issues += grounding_issues(data, document, skip=malformed)
    issues += completeness_issues(data, document)
    unique = _dedupe(issues)
    return ValidationOutcome(record=record if not unique else None, issues=unique)
