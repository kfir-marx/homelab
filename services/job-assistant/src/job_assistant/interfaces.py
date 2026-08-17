from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class NormalizedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    external_job_id: str
    original_url: str
    canonical_url: str
    company: str
    title: str
    location: str | None = None
    workplace_type: str | None = None
    employment_type: str | None = None
    description_html: str | None = None
    description_text: str = ""
    published_at: datetime | None = None
    ats_job_id: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class JobSource(Protocol):
    name: str

    def discover(self) -> Iterable[NormalizedJob]: ...


class GeneratedBullet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    inventory_ids: list[str] = Field(min_length=1)


class RequirementEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement: str
    inventory_ids: list[str]
    strength: str


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    professional_summary: str
    skill_groups: dict[str, list[str]]
    experience_bullets: list[GeneratedBullet]
    requirement_evidence: list[RequirementEvidence]
    unsupported_requirements: list[str]
    recruiter_message: str
    contact_resolution_hints: list[str]
    claims_used: dict[str, list[str]]
    warnings: list[str]


class GenerationProvider(Protocol):
    name: str

    def generate(self, payload: dict[str, Any]) -> GenerationResult: ...


@dataclass(frozen=True)
class ContactCandidate:
    name: str
    role: str | None
    email: str | None
    profile_url: str | None
    source: str
    confidence: str
    verified: bool
    evidence: str


class ContactResolver(Protocol):
    def resolve(self, job: NormalizedJob) -> Iterable[ContactCandidate]: ...


@dataclass(frozen=True)
class Delivery:
    recipient: str
    subject: str
    body: str
    attachments: tuple[Path, ...] = ()
    idempotency_key: str = ""


class DeliveryProvider(Protocol):
    def send(self, delivery: Delivery) -> str: ...


@dataclass(frozen=True)
class StoredArtifact:
    key: str
    sha256: str
    size_bytes: int
    mime_type: str


class ArtifactStorage(Protocol):
    def put(self, key: str, content: bytes, mime_type: str) -> StoredArtifact: ...
    def get(self, key: str) -> bytes: ...


@dataclass(frozen=True)
class Notification:
    recipient: str
    text: str
    buttons: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    idempotency_key: str = ""


class NotificationProvider(Protocol):
    def send(self, notification: Notification) -> str: ...


@dataclass(frozen=True)
class BounceEvent:
    provider_message_id: str
    recipient: str
    status: str
    occurred_at: datetime


class BounceProvider(Protocol):
    def receive(self) -> Iterable[BounceEvent]: ...
