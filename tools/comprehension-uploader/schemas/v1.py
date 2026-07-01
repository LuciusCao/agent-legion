"""Pydantic models for comprehension info schema version v1."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int | None
    end: int | None


class SocraticOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    text: str
    is_correct: bool


class SocraticQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    options: list[SocraticOption]


class GivenContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    position: Position


class HiddenContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    derived_text: str
    source_text: str
    position: Position
    derivation: str


class KeyInfoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key_info_id: str
    type: Literal["given", "hidden"]
    content: GivenContent | HiddenContent
    question: SocraticQuestion
    question_comprehension_ability: str


class PossibleErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_id: str
    error_type: Literal["question_comprehension"]
    position: int
    error_answer: list[str]
    error_description: str
    cognitive_basis: str
    related_key_info_ids: list[str]


class ComprehensionDataV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str | None
    comprehension_difficulty: int = Field(ge=1, le=99)
    key_info_list: list[KeyInfoItem]
    possible_error_list: list[PossibleErrorItem]
