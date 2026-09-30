# Project Brief: Structured Data Extraction Pipeline

> The design and implementation are documented in [README.md](README.md).

## Scenario

Building a structured data extraction system using Claude. The system extracts information from
unstructured documents, validates the output using JSON Schemas, and maintains high accuracy. It
must handle edge cases gracefully and integrate with downstream systems.

**Primary domains:** Prompt Engineering & Structured Output, Context Management & Reliability.

## Objective

Practice designing JSON schemas, using `tool_use` for structured output, implementing
validation-retry loops, and designing batch processing strategies.

## Tasks

1. Define an extraction tool with a JSON schema containing required and optional fields, an enum
   with an `"other"` + detail string pattern, and nullable fields for information that may not
   exist in source documents. Process documents where some fields are absent and verify the model
   returns null rather than fabricating values.
2. Implement a validation-retry loop: when Pydantic or JSON Schema validation fails, send a
   follow-up request including the document, the failed extraction, and the specific validation
   error. Track which errors are resolvable via retry (format mismatches) versus which are not
   (information absent from source).
3. Add few-shot examples demonstrating extraction from documents with varied formats (e.g. inline
   citations vs bibliographies, narrative descriptions vs structured tables) and verify improved
   handling of structural variety.
4. Design a batch processing strategy: submit a batch of 100 documents using the Message Batches
   API, handle failures by `custom_id`, resubmit failed documents with modifications (e.g. chunking
   oversized documents), and calculate total processing time relative to SLA constraints.
5. Implement a human review routing strategy: have the model output field-level confidence scores,
   route low-confidence extractions to human review, and analyse accuracy by document type and
   field to verify consistent performance.
6. Start with the design, using diagrams, as the front page of the README. This folder ships to
   GitHub as a standalone project.
