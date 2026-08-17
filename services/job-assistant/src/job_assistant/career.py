from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
    text: str
    metrics: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    evidence: Literal["verified", "documented", "self_reported", "homelab", "study"]
    allowed_variations: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    employer: str
    start_date: date
    end_date: date | None = None
    official_title: str
    allowed_display_titles: list[str] = Field(default_factory=list)
    responsibilities: list[InventoryFact]
    achievements: list[InventoryFact] = Field(default_factory=list)


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str
    facts: list[InventoryFact]


class CareerInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    person: dict[str, str]
    experiences: list[Experience]
    projects: list[Project] = Field(default_factory=list)
    education: list[InventoryFact] = Field(default_factory=list)
    certifications: list[InventoryFact] = Field(default_factory=list)
    languages: list[InventoryFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_fact_ids(self) -> CareerInventory:
        ids: list[str] = []
        for experience in self.experiences:
            ids.extend(fact.id for fact in experience.responsibilities + experience.achievements)
        for project in self.projects:
            ids.extend(fact.id for fact in project.facts)
        ids.extend(
            fact.id
            for group in (self.education, self.certifications, self.languages)
            for fact in group
        )
        if len(ids) != len(set(ids)):
            raise ValueError("career inventory fact IDs must be globally unique")
        return self

    def fact_ids(self) -> set[str]:
        return (
            {
                fact.id
                for experience in self.experiences
                for fact in experience.responsibilities + experience.achievements
            }
            | {fact.id for project in self.projects for fact in project.facts}
            | {
                fact.id
                for group in (self.education, self.certifications, self.languages)
                for fact in group
            }
        )


def load_inventory(path: Path) -> CareerInventory:
    if not path.is_file():
        raise FileNotFoundError(
            f"career inventory is missing at {path}; install the real private inventory "
            "before generation"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CareerInventory.model_validate(data)
