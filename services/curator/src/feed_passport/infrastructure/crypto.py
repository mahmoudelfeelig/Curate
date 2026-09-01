from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_FORBIDDEN_CREDENTIAL_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "oauth_token",
        "token",
        "client_secret",
        "client_assertion",
        "authorization_code",
        "password",
        "api_key",
        "private_key",
    }
)


class SecretMaterialRejected(ValueError):
    pass


def assert_no_stored_credentials(value: Any, *, path: str = "metadata") -> None:
    """Reject known credential-bearing fields before serialization or encryption."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
            child_path = f"{path}.{key}"
            if normalized in _FORBIDDEN_CREDENTIAL_KEYS:
                raise SecretMaterialRejected(f"credential material is forbidden at {child_path}")
            assert_no_stored_credentials(item, path=child_path)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_stored_credentials(item, path=f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class EncryptedJson:
    key_id: str
    nonce: bytes
    ciphertext: bytes


class AesGcmKeyring:
    """External-key AES-GCM envelope used for local encrypted metadata.

    Key material is supplied by the caller and is never persisted by this
    component. Production can replace this boundary with KMS envelope
    encryption while preserving the registry contract.
    """

    def __init__(
        self,
        *,
        active_key_id: str,
        keys: Mapping[str, bytes],
        index_key: bytes,
    ) -> None:
        if not active_key_id.strip() or active_key_id not in keys:
            raise ValueError("an active AES-GCM key ID present in the keyring is required")
        normalized: dict[str, bytes] = {}
        for key_id, key in keys.items():
            if not str(key_id).strip() or len(key) != 32:
                raise ValueError("AES-GCM key IDs and 32-byte keys are required")
            normalized[str(key_id)] = bytes(key)
        if len(index_key) < 32:
            raise ValueError("the external blind-index key must contain at least 32 bytes")
        self.active_key_id = active_key_id
        self._keys = MappingProxyType(normalized)
        self._index_key = bytes(index_key)

    def encrypt_json(self, value: Any, *, associated_data: bytes) -> EncryptedJson:
        assert_no_stored_credentials(value)
        plaintext = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._keys[self.active_key_id]).encrypt(nonce, plaintext, associated_data)
        return EncryptedJson(self.active_key_id, nonce, ciphertext)

    def decrypt_json(self, value: EncryptedJson, *, associated_data: bytes) -> Any:
        key = self._keys.get(value.key_id)
        if key is None:
            raise ValueError(f"unknown encrypted metadata key ID: {value.key_id}")
        try:
            plaintext = AESGCM(key).decrypt(value.nonce, value.ciphertext, associated_data)
        except InvalidTag as exc:
            raise ValueError("encrypted metadata authentication failed") from exc
        try:
            decoded = json.loads(plaintext)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("encrypted metadata is not canonical JSON") from exc
        assert_no_stored_credentials(decoded)
        return decoded

    def blind_index(self, namespace: str, value: str) -> str:
        if not namespace.strip() or not value:
            raise ValueError("blind-index namespace and value are required")
        payload = f"{namespace}\0{value}".encode("utf-8")
        return hmac.new(self._index_key, payload, hashlib.sha256).hexdigest()
