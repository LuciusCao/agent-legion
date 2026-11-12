"""Validation issue and advisory wire models for studio-agent skill tools."""

from pydantic import BaseModel


class SkillValidationIssue(BaseModel):
    path: str
    error: str


class SkillValidationWarning(BaseModel):
    path: str
    error: str
