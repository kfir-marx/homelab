import pytest

from job_assistant import human_codes
from job_assistant.human_codes import ALPHABET, generate_human_code
from job_assistant.states import (
    APPLICATION_TRANSITIONS,
    ApplicationStatus,
    InvalidTransition,
    ensure_transition,
)


def test_human_alphabet_excludes_ambiguous_characters() -> None:
    assert not set("0O1IL") & set(ALPHABET)


def test_human_code_retries_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    choices = iter("AAAAABBBBB")
    monkeypatch.setattr(human_codes.secrets, "choice", lambda _: next(choices))
    assert generate_human_code(lambda code: code == "AAAAA") == "BBBBB"


def test_invalid_transition_fails_closed() -> None:
    with pytest.raises(InvalidTransition):
        ensure_transition(
            ApplicationStatus.SELECTED, ApplicationStatus.SUBMITTED, APPLICATION_TRANSITIONS
        )
