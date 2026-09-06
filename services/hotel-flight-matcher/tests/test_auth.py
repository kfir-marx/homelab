from cryptography.fernet import Fernet

from hotel_flight_matcher.auth import TokenCipher, bearer_token


def test_tokens_are_encrypted_and_decrypted() -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt("refresh-secret")
    assert encrypted != "refresh-secret"
    assert cipher.decrypt(encrypted) == "refresh-secret"


def test_bearer_parser_fails_closed() -> None:
    assert bearer_token("Bearer token") == "token"
    assert bearer_token("Basic token") == ""
    assert bearer_token(None) == ""
