from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from core.config import get_settings


class CredentialStoreError(RuntimeError):
    pass


class LocalEncryptedCredentialStore:
    """Encrypted local credential store for development/pilot use.

    Database rows keep only a credential reference. The OAuth token payload is
    encrypted on disk. Production deployments can replace this implementation
    with a managed secrets service without changing calendar connection rows.
    """

    prefix = "local-google:"

    def __init__(self) -> None:
        settings = get_settings()
        self.root = Path(settings.credential_store_path)
        key_material = settings.credential_encryption_key or settings.jwt_secret_key
        digest = hashlib.sha256(key_material.encode("utf-8")).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(digest))

    def _path(self, tenant_id: UUID) -> Path:
        return self.root / "google" / f"{tenant_id}.json.enc"

    def _tenant_from_ref(self, credential_ref: str) -> UUID:
        if not credential_ref.startswith(self.prefix):
            raise CredentialStoreError("Unsupported credential reference")
        try:
            return UUID(credential_ref.removeprefix(self.prefix))
        except ValueError as exc:
            raise CredentialStoreError("Invalid credential reference") from exc

    def put(self, tenant_id: UUID, payload: dict) -> str:
        path = self._path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        encrypted = self.fernet.encrypt(encoded)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(encrypted)
        os.replace(temp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return f"{self.prefix}{tenant_id}"

    def get(self, credential_ref: str) -> dict:
        tenant_id = self._tenant_from_ref(credential_ref)
        path = self._path(tenant_id)
        try:
            encrypted = path.read_bytes()
            raw = self.fernet.decrypt(encrypted)
            value = json.loads(raw.decode("utf-8"))
        except FileNotFoundError as exc:
            raise CredentialStoreError("Stored Google credentials were not found") from exc
        except (InvalidToken, json.JSONDecodeError) as exc:
            raise CredentialStoreError("Stored Google credentials could not be decrypted") from exc
        if not isinstance(value, dict):
            raise CredentialStoreError("Stored Google credentials are invalid")
        return value

    def delete(self, credential_ref: str | None) -> None:
        if not credential_ref or not credential_ref.startswith(self.prefix):
            return
        tenant_id = self._tenant_from_ref(credential_ref)
        path = self._path(tenant_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
