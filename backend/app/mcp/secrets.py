"""Encrypt MCP secrets (stdio env values, HTTP headers) before they're stored in the database.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from SECRET_KEY. Changing SECRET_KEY makes
stored secrets unreadable; re-enter them on each server afterwards.
"""

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.errors import MCPConnectionError, SecretKeyMissingError


def _fernet(secret_key: str | None) -> Fernet:
    if not secret_key:
        raise SecretKeyMissingError("Set SECRET_KEY to store MCP secrets (env values or headers).")
    digest = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secrets(values: dict[str, str], secret_key: str | None) -> str | None:
    """`{"API_TOKEN": "..."}` → an encrypted string, or None when there's nothing to store."""
    if not values:
        return None
    return _fernet(secret_key).encrypt(json.dumps(values).encode()).decode()


def decrypt_secrets(token: str | None, secret_key: str | None) -> dict[str, str]:
    """The reverse of `encrypt_secrets`. {} when nothing is stored."""
    if not token:
        return {}
    try:
        return json.loads(_fernet(secret_key).decrypt(token.encode()))
    except InvalidToken as exc:
        raise MCPConnectionError(
            "Stored MCP secrets can't be decrypted: SECRET_KEY changed. Re-enter the server's secrets."
        ) from exc
