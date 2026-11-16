"""Envelope encryption for the provider API keys workspaces bring (BYOK).

Each secret is sealed with its own random data key (AES-256-GCM). The data key is then sealed with the service's
master key. Both seals are bound to a context string (workspace id and credential id), so a row copied into
another workspace, or another row's data key, will not decrypt.

Master keys come from the environment:

    NEXUS_SECRET_KEY        the current key: 32 random bytes, base64 or hex
    NEXUS_SECRET_KEYS_OLD   comma-separated earlier keys, still accepted for reading

A key is identified by a fingerprint of its bytes, stored next to each secret, so rotation is: set the new key,
keep the old one in NEXUS_SECRET_KEYS_OLD, run ``nexus admin rotate-keys``, then drop the old one. The master-key
seal is the single place to swap in a cloud KMS: replace ``_wrap`` and ``_unwrap``.

For local use with no key configured, a key file is created in the data directory with owner-only permissions.
On Postgres there is no data directory, so a missing key is an error: credentials cannot be stored without one.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .models import ValidationError

KEY_BYTES = 32
NONCE_BYTES = 12


class SecretsUnavailable(ValidationError):
    """No master key is configured, so secrets cannot be stored or read."""


def _decode_key(text: str) -> bytes:
    text = text.strip()
    for decode in (lambda s: bytes.fromhex(s), lambda s: base64.b64decode(s, validate=True),
                   lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))):
        try:
            raw = decode(text)
        except (ValueError, binascii.Error):
            continue
        if len(raw) == KEY_BYTES:
            return raw
    raise ValueError("a NEXUS secret key must be 32 bytes, written as base64 or hex")


def fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"nexus-key-id:" + key).hexdigest()[:12]


def generate_key() -> str:
    return base64.b64encode(os.urandom(KEY_BYTES)).decode()


def _seal(key: bytes, plaintext: bytes, context: bytes) -> str:
    nonce = os.urandom(NONCE_BYTES)
    return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, plaintext, context)).decode()


def _open(key: bytes, sealed: str, context: bytes) -> bytes:
    raw = base64.b64decode(sealed)
    return AESGCM(key).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], context)


class KeyRing:
    def __init__(self, primary: bytes, old: list[bytes] | None = None):
        self.primary_id = fingerprint(primary)
        self._keys = {fingerprint(k): k for k in [primary, *(old or [])]}

    @classmethod
    def from_environment(cls, data_dir: Path | None = None) -> "KeyRing | None":
        """The configured keys; a generated key file for local data directories; None when nothing is available."""
        primary = os.environ.get("NEXUS_SECRET_KEY")
        old = [_decode_key(k) for k in os.environ.get("NEXUS_SECRET_KEYS_OLD", "").split(",") if k.strip()]
        if primary:
            return cls(_decode_key(primary), old)
        if data_dir is None:
            return None
        path = data_dir / "secret.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(generate_key())
        return cls(_decode_key(path.read_text()), old)

    # The two functions a KMS would replace.

    def _wrap(self, data_key: bytes, context: bytes) -> str:
        return _seal(self._keys[self.primary_id], data_key, context)

    def _unwrap(self, wrapped: str, key_id: str, context: bytes) -> bytes:
        master = self._keys.get(key_id)
        if master is None:
            raise SecretsUnavailable("This secret was sealed with a master key that is no longer configured.")
        return _open(master, wrapped, context)

    def seal(self, plaintext: str, context: str) -> dict[str, str]:
        ctx = context.encode()
        data_key = os.urandom(KEY_BYTES)
        return {"ciphertext": _seal(data_key, plaintext.encode(), ctx),
                "wrapped_key": self._wrap(data_key, ctx), "key_version": self.primary_id}

    def open(self, ciphertext: str, wrapped_key: str, key_version: str, context: str) -> str:
        ctx = context.encode()
        try:
            return _open(self._unwrap(wrapped_key, key_version, ctx), ciphertext, ctx).decode()
        except InvalidTag:
            raise SecretsUnavailable("This secret could not be decrypted: it was altered or belongs elsewhere.") from None

    def needs_rotation(self, key_version: str) -> bool:
        return key_version != self.primary_id
