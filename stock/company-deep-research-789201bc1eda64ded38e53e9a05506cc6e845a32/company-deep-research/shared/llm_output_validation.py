from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, constr

NonEmptyStr = constr(min_length=1, strip_whitespace=True)


class SourceItem(BaseModel):
    """
    Minimal schema for `sources[]` objects emitted by section/conclusion writers.
    """

    type: Literal["api", "sec", "news"]
    id: str | None = None
    title: str | None = None
    tool: str | None = None

    model_config = ConfigDict(extra="allow")


class SectionContent(BaseModel):
    id: str
    title: str
    body_paragraphs: list[NonEmptyStr] = Field(min_length=1)
    sources: list[SourceItem] = Field(min_length=1)

    model_config = ConfigDict(extra="allow")


class SectionWriterOutput(BaseModel):
    run_id: str | None = None
    agent: str | None = None
    # Some models drift on this; we validate the *section content* deterministically elsewhere.
    schema_version: int | None = None
    section: SectionContent

    model_config = ConfigDict(extra="allow")


class ConclusionContent(BaseModel):
    investment_rating: str | None = None
    target_price_12m: float | str | None = None
    body_paragraphs: list[NonEmptyStr] = Field(min_length=1)
    bull_case: str | None = None
    bear_case: str | None = None
    position_strategy: str | None = None
    sources: list[SourceItem] = Field(min_length=1)

    model_config = ConfigDict(extra="allow")


class ConclusionWriterOutput(BaseModel):
    run_id: str | None = None
    agent: str | None = None
    schema_version: int | None = None
    conclusion: ConclusionContent

    model_config = ConfigDict(extra="allow")


def _compact_pydantic_errors(err: ValidationError, *, max_chars: int = 900) -> str:
    # Keep messages small enough to fit back into the model prompt.
    parts: list[str] = []
    for e in err.errors():
        loc = ".".join(str(x) for x in e.get("loc", []))
        msg = e.get("msg", "")
        parts.append(f"{loc}:{msg}".strip(":"))
    s = "; ".join(parts)
    return s[:max_chars]


def validate_section_writer_output(raw: Any) -> tuple[SectionContent | None, list[str]]:
    """
    Validates the *LLM JSON wrapper* for a section writer output.
    Returns:
      - parsed SectionContent (or None)
      - list of human-readable error strings for retry feedback
    """
    try:
        m = SectionWriterOutput.model_validate(raw)
        return m.section, []
    except ValidationError as e:
        # Common wrapper drift:
        # - {"section_4_1a": {...}}  (single-key)
        # - {"id": "...", "title": "...", "body_paragraphs": [...], "sources": [...]} (section directly)
        if isinstance(raw, dict):
            if "section" not in raw:
                # Direct section object
                if {"id", "title", "body_paragraphs", "sources"}.issubset(raw.keys()):
                    try:
                        m2 = SectionWriterOutput.model_validate({"schema_version": 1, "section": raw})
                        return m2.section, []
                    except ValidationError:
                        pass

                # Single-key wrapper like "section_4_1a"
                candidate_keys = [
                    k
                    for k, v in raw.items()
                    if isinstance(k, str)
                    and isinstance(v, dict)
                    and (k == "section" or k.startswith("section"))
                ]
                if len(candidate_keys) == 1:
                    k = candidate_keys[0]
                    try:
                        m3 = SectionWriterOutput.model_validate(
                            {"schema_version": raw.get("schema_version", 1), "section": raw[k]}
                        )
                        return m3.section, []
                    except ValidationError:
                        pass

        return None, [f"section_output_schema_invalid:{_compact_pydantic_errors(e)}"]


def validate_conclusion_writer_output(raw: Any) -> tuple[ConclusionContent | None, list[str]]:
    try:
        m = ConclusionWriterOutput.model_validate(raw)
        return m.conclusion, []
    except ValidationError as e:
        if isinstance(raw, dict) and "conclusion" not in raw:
            # Direct conclusion object
            if {"investment_rating", "body_paragraphs", "sources"}.issubset(raw.keys()):
                try:
                    m2 = ConclusionWriterOutput.model_validate({"schema_version": 1, "conclusion": raw})
                    return m2.conclusion, []
                except ValidationError:
                    pass

            # Single-key wrapper like "conclusion_x"
            candidate_keys = [
                k
                for k, v in raw.items()
                if isinstance(k, str) and isinstance(v, dict) and k.startswith("conclusion")
            ]
            if len(candidate_keys) == 1:
                k = candidate_keys[0]
                try:
                    m3 = ConclusionWriterOutput.model_validate(
                        {"schema_version": raw.get("schema_version", 1), "conclusion": raw[k]}
                    )
                    return m3.conclusion, []
                except ValidationError:
                    pass

        return None, [f"conclusion_output_schema_invalid:{_compact_pydantic_errors(e)}"]

