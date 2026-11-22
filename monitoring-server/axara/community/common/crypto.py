# Copyright (c) 2026 Axara Pty Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Symmetric encryption for credentials at rest (MASTER_ENCRYPTION_KEY).

Scope: encrypt sensitive values inside monitor_configs.config (db-monitor
passwords and any other secret config fields) at rest in Postgres, and decrypt
them only when materialising monitors.yaml for the agent (which needs plaintext
to connect). API/UI responses mask secrets so plaintext never reaches a browser.

Encrypted values carry a sentinel prefix ("enc:v1:") so encryption is idempotent
and decryption is unambiguous; plaintext/legacy values pass through decrypt
unchanged, which keeps the rollout safe before everything is re-saved.

Key handling: MASTER_ENCRYPTION_KEY from the environment. If it is already a
valid 32-byte urlsafe-base64 Fernet key it is used as-is; otherwise a Fernet key
is derived deterministically from it (SHA-256) so any passphrase works.
Fail-closed: encrypting a secret with no key configured raises — you cannot store
a secret the server could never read back.

Key rotation: set MASTER_ENCRYPTION_KEY to the NEW key and
MASTER_ENCRYPTION_KEY_PREVIOUS to the old one, restart, run
`python manage.py rotate-master-key`, then remove MASTER_ENCRYPTION_KEY_PREVIOUS.
During that window values encrypted under either key decrypt; new values always
use the new key.
"""
import os
import base64
import hashlib
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:v1:"
_MASK = "••••••••"   # bullets shown to the UI

# config keys whose string values are treated as secrets (substring match, ci)
SENSITIVE_CONFIG_KEYS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "api_token", "private_key", "client_secret", "connection_string",
    "conn_string", "dsn", "auth_token",
}

_fernet = None
_init_error = None


def _derive_key(raw: str) -> bytes:
    """Use a ready-made Fernet key as-is; otherwise derive one from the passphrase."""
    try:
        cand = raw.encode()
        if len(base64.urlsafe_b64decode(cand)) == 32:
            return cand
    except Exception:
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


def _load_fernet():
    """The Fernet (or MultiFernet with the previous key) built from MASTER_ENCRYPTION_KEY.

    The result, or the reason it failed, is cached.

    Returns:
        The cipher, or None when no valid key is set.
    """
    global _fernet, _init_error
    if _fernet is not None:
        return _fernet
    if _init_error is not None:
        return None
    try:
        from cryptography.fernet import Fernet, MultiFernet
        raw = os.getenv("MASTER_ENCRYPTION_KEY")
        if not raw:
            _init_error = "MASTER_ENCRYPTION_KEY is not set"
            return None
        keys = [Fernet(_derive_key(raw))]
        prev = os.getenv("MASTER_ENCRYPTION_KEY_PREVIOUS")
        if prev:
            keys.append(Fernet(_derive_key(prev)))   # decrypt-only during rotation
        _fernet = MultiFernet(keys)
    except Exception as e:
        _init_error = str(e)
        logger.error(f"crypto init failed: {e}")
    return _fernet


def encryption_available() -> bool:
    """Whether a valid MASTER_ENCRYPTION_KEY is configured."""
    return _load_fernet() is not None


def is_encrypted(value: Any) -> bool:
    """Whether a value is an encrypted secret (has the enc: prefix)."""
    return isinstance(value, str) and value.startswith(_ENC_PREFIX)


def is_mask(value: Any) -> bool:
    """Whether a value is the mask shown in place of a secret."""
    return value == _MASK


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret (values already encrypted, and None, are returned unchanged).

    Args:
        plaintext: Secret text.

    Returns:
        The prefixed ciphertext.

    Raises:
        RuntimeError: when no encryption key is configured.
    """
    if plaintext is None or is_encrypted(plaintext):
        return plaintext
    f = _load_fernet()
    if f is None:
        raise RuntimeError(f"Encryption unavailable: {_init_error}")
    return _ENC_PREFIX + f.encrypt(plaintext.encode()).decode()


def decrypt_secret(value: str) -> str:
    """Decrypt a secret (plain values are returned unchanged).

    Args:
        value: Prefixed ciphertext or plain text.

    Returns:
        The plain text.

    Raises:
        RuntimeError: when no encryption key is configured.
    """
    if not is_encrypted(value):
        return value
    f = _load_fernet()
    if f is None:
        raise RuntimeError(f"Decryption unavailable: {_init_error}")
    return f.decrypt(value[len(_ENC_PREFIX):].encode()).decode()


def _is_sensitive(key: str) -> bool:
    """Whether a config key holds a secret (its name contains a sensitive word)."""
    k = str(key).lower()
    return any(s in k for s in SENSITIVE_CONFIG_KEYS)


def encrypt_config_secrets(config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Copy of config with sensitive string values encrypted (idempotent)."""
    if not isinstance(config, dict):
        return config
    out = dict(config)
    for k, v in out.items():
        if _is_sensitive(k) and isinstance(v, str) and v and not is_encrypted(v):
            out[k] = encrypt_secret(v)
    return out


def decrypt_config_secrets(config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Copy of config with encrypted sensitive values decrypted."""
    if not isinstance(config, dict):
        return config
    out = dict(config)
    for k, v in out.items():
        if is_encrypted(v):
            out[k] = decrypt_secret(v)
    return out


def rotate_config_secrets(config):
    """Re-encrypt every encrypted value under the current primary key.
    Returns (new_config, number_of_values_rotated)."""
    if not isinstance(config, dict):
        return config, 0
    f = _load_fernet()
    if f is None:
        raise RuntimeError(f"Encryption unavailable: {_init_error}")
    out, n = dict(config), 0
    for k, v in out.items():
        if is_encrypted(v):
            out[k] = _ENC_PREFIX + f.rotate(v[len(_ENC_PREFIX):].encode()).decode()
            n += 1
    return out, n


def mask_config_secrets(config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Copy of config with sensitive values replaced by a mask for UI display."""
    if not isinstance(config, dict):
        return config
    out = dict(config)
    for k, v in out.items():
        if _is_sensitive(k) and isinstance(v, str) and v:
            out[k] = _MASK
    return out
