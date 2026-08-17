from pathlib import Path

import pytest

from job_assistant.artifacts import (
    FilesystemArtifactStorage,
    render_docx,
    render_markdown,
    render_pdf,
)
from job_assistant.interfaces import GenerationResult
from job_assistant.security import UnsafeInput


def generated() -> GenerationResult:
    return GenerationResult.model_validate(
        {
            "professional_summary": "Platform engineer",
            "skill_groups": {"Platform": ["Kubernetes"]},
            "experience_bullets": [{"text": "Operated Linux", "inventory_ids": ["fact-1"]}],
            "requirement_evidence": [],
            "unsupported_requirements": [],
            "recruiter_message": "Hello",
            "contact_resolution_hints": [],
            "claims_used": {"Operated Linux": ["fact-1"]},
            "warnings": [],
        }
    )


def test_artifact_rendering_and_checksum(tmp_path: Path) -> None:
    value = generated()
    assert render_markdown(value).startswith(b"Platform engineer")
    assert render_docx(value).startswith(b"PK")
    assert render_pdf(value).startswith(b"%PDF")
    store = FilesystemArtifactStorage(tmp_path)
    stored = store.put("A2345/cv.pdf", render_pdf(value), "application/pdf")
    assert len(stored.sha256) == 64
    assert store.get(stored.key).startswith(b"%PDF")


def test_artifact_path_escape_rejected(tmp_path: Path) -> None:
    with pytest.raises(UnsafeInput):
        FilesystemArtifactStorage(tmp_path).put("../escape", b"x", "text/plain")
