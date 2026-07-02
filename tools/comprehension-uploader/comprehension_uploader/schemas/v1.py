"""Pydantic models for comprehension info schema version v1."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start: int | None
    end: int | None


class SocraticOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str
    text: str
    is_correct: bool


class SocraticQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    options: list[SocraticOption]


class GivenContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    position: Position


class HiddenContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    derived_text: str
    source_text: str
    position: Position
    derivation: str


class KeyInfoItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key_info_id: str
    type: Literal["given", "hidden"]
    content: GivenContent | HiddenContent
    question: SocraticQuestion
    question_comprehension_ability: str
    decision: Literal["approved", "rejected"] | None = None

    @model_validator(mode="after")
    def _content_matches_type(self) -> KeyInfoItem:
        if self.type == "given" and not isinstance(self.content, GivenContent):
            raise ValueError("type 'given' requires GivenContent with 'text' and 'position'")
        if self.type == "hidden" and not isinstance(self.content, HiddenContent):
            raise ValueError(
                "type 'hidden' requires HiddenContent with 'derived_text', 'source_text', "
                "'position' and 'derivation'"
            )
        return self


class PossibleErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    error_id: str
    error_type: Literal["question_comprehension"]
    position: int
    error_answer: list[str]
    error_description: str
    cognitive_basis: str
    related_key_info_ids: list[str]


class ComprehensionDataV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fingerprint: str | None
    comprehension_difficulty: int = Field(ge=1, le=99)
    key_info_list: list[KeyInfoItem]
    possible_error_list: list[PossibleErrorItem]
