from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from .models import User, UserSearchProfile
from .ranking import SearchCriteria, load_criteria

DEFAULT_NOTIFICATION_PREFERENCES: dict[str, object] = {
    "recommendations": True,
    "reminders": True,
    "draft_reminders": True,
    "submission_reminders": True,
    "outcome_reminders": True,
}


def load_user_criteria(
    session: Session, user: User, shared_path: Path, artifact_root: Path
) -> tuple[SearchCriteria, str]:
    profile = session.get(UserSearchProfile, user.id)
    if profile is not None:
        criteria = SearchCriteria.model_validate(profile.criteria)
        digest = hashlib.sha256(criteria.model_dump_json().encode()).hexdigest()[:16]
        return criteria, f"profile-{profile.version}-{digest}"
    path = artifact_root / user.search_criteria_key if user.search_criteria_key else shared_path
    return load_criteria(path)


def notification_preferences(session: Session, user: User) -> dict[str, object]:
    profile = session.get(UserSearchProfile, user.id)
    return {
        **DEFAULT_NOTIFICATION_PREFERENCES,
        **(profile.notification_preferences if profile else {}),
    }
