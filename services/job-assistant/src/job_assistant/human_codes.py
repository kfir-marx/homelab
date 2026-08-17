from __future__ import annotations

import secrets
from collections.abc import Callable

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def generate_human_code(exists: Callable[[str], bool], length: int = 5, attempts: int = 100) -> str:
    for _ in range(attempts):
        code = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if not exists(code):
            return code
    raise RuntimeError("could not allocate a unique application code")
