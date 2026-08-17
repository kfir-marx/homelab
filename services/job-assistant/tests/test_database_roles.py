import pytest
from pydantic import SecretStr

from job_assistant.database_roles import provision_generation_role


def test_generation_role_rejects_unexpected_identity_before_connecting() -> None:
    with pytest.raises(ValueError, match="job_assistant_generation"):
        provision_generation_role(
            SecretStr("postgresql+psycopg://owner:owner@db/jobs"),
            SecretStr("postgresql+psycopg://wrong:restricted@db/jobs"),
        )
