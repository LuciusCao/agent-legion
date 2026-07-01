"""Pydantic models for comprehension info schema version v1."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Position(BaseModel):
    start: int | None
    end: int | None


class SocraticOption(BaseModel):
    label: str
    text: str
    is_correct: bool


class SocraticQuestion(BaseModel):
    text: str
    options: list[SocraticOption]


class GivenContent(BaseModel):
    text: str
    position: Position


class HiddenContent(BaseModel):
    derived_text: str
    source_text: str
    position: Position
    derivation: str


class KeyInfoItem(BaseModel):
    key_info_id: str
    type: Literal["given", "hidden"]
    content: GivenContent | HiddenContent
    question: SocraticQuestion
    question_comprehension_ability: str


class PossibleErrorItem(BaseModel):
    error_id: str
    error_type: Literal["question_comprehension"]
    position: int
    error_answer: list[str]
    error_description: str
    cognitive_basis: str
    related_key_info_ids: list[str]


class ComprehensionDataV1(BaseModel):
    fingerprint: str | None
    comprehension_difficulty: int = Field(ge=1, le=99)
    key_info_list: list[KeyInfoItem]
    possible_error_list: list[PossibleErrorItem]
