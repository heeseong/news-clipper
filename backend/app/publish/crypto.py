"""Encrypt channel tokens at rest (spec 7: oauth_token encrypted). Key: TOKEN_ENCRYPTION_KEY (Fernet)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class TokenKeyMissing(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if key is None or not key.get_secret_value().strip():
        raise TokenKeyMissing(
            "TOKEN_ENCRYPTION_KEY가 설정되지 않았습니다 (채널 토큰 암호화 키, .env.example 참고)"
        )
    try:
        return Fernet(key.get_secret_value().strip().encode())
    except ValueError as e:
        raise TokenKeyMissing("TOKEN_ENCRYPTION_KEY 형식이 잘못됐습니다 (Fernet 키, 44자 base64)") from e


def encrypt(value: str | None) -> str | None:
    return None if value is None else _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as e:
        raise TokenKeyMissing("저장된 토큰을 풀 수 없습니다 (TOKEN_ENCRYPTION_KEY가 바뀌었나요?)") from e
