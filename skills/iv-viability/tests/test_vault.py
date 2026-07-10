"""
Hermetic tests for vault.py with a mocked keyring backend.

These tests never touch the real macOS Keychain. They verify the vault's
invariants: round-trip encrypt/decrypt, chmod 600 enforcement, wipe, and
rotate_key correctness.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeKeyring:
    """Minimal in-memory keyring replacement."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, account):
        return self.store.get((service, account))

    def set_password(self, service, account, value):
        self.store[(service, account)] = value

    def delete_password(self, service, account):
        if (service, account) not in self.store:
            from keyring.errors import PasswordDeleteError
            raise PasswordDeleteError("no such entry")
        del self.store[(service, account)]


@pytest.fixture
def vault_mod(monkeypatch, tmp_path):
    """Load vault with a redirected skill dir and a fake keyring."""
    import importlib
    import vault as real_vault

    importlib.reload(real_vault)
    monkeypatch.setattr(real_vault, "SKILL_DIR", tmp_path, raising=False)
    monkeypatch.setattr(real_vault, "TOKEN_FILE", tmp_path / ".schwab_token.json.enc", raising=False)
    monkeypatch.setattr(real_vault, "LOCK_FILE", tmp_path / ".schwab_token.lock", raising=False)

    fake = FakeKeyring()

    import keyring
    import keyring.errors  # noqa: F401

    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)

    return real_vault


def test_store_credentials_puts_all_four_in_keychain(vault_mod):
    vault_mod.store_credentials("key1", "secret1", "https://127.0.0.1:8182")
    assert vault_mod.get_api_key() == "key1"
    assert vault_mod.get_app_secret() == "secret1"
    assert vault_mod.get_callback_url() == "https://127.0.0.1:8182"
    # fernet key auto-generated
    assert vault_mod._get(vault_mod.ACCOUNT_FERNET_KEY) is not None


def test_has_credentials_reflects_state(vault_mod):
    assert vault_mod.has_credentials() is False
    vault_mod.store_credentials("k", "s", "https://127.0.0.1:8182")
    assert vault_mod.has_credentials() is True


def test_token_roundtrip(vault_mod):
    vault_mod.store_credentials("k", "s", "https://127.0.0.1:8182")
    token = {"access_token": "AAA", "refresh_token": "BBB", "expires_at": 12345}
    vault_mod.save_token(token)
    loaded = vault_mod.load_token()
    assert loaded == token


def test_token_file_is_chmod_600(vault_mod):
    vault_mod.store_credentials("k", "s", "https://127.0.0.1:8182")
    vault_mod.save_token({"access_token": "x"})
    mode = stat.S_IMODE(vault_mod.TOKEN_FILE.stat().st_mode)
    assert mode == 0o600


def test_load_token_missing_file_raises(vault_mod):
    vault_mod.store_credentials("k", "s", "https://127.0.0.1:8182")
    with pytest.raises(vault_mod.CredentialsNotEnrolled):
        vault_mod.load_token()


def test_load_token_corrupt_raises(vault_mod):
    vault_mod.store_credentials("k", "s", "https://127.0.0.1:8182")
    vault_mod.TOKEN_FILE.write_bytes(b"not-a-valid-fernet-blob")
    os.chmod(vault_mod.TOKEN_FILE, 0o600)
    with pytest.raises(vault_mod.TokenDecryptFailed):
        vault_mod.load_token()


def test_load_token_fixes_bad_perms(vault_mod):
    vault_mod.store_credentials("k", "s", "https://127.0.0.1:8182")
    vault_mod.save_token({"access_token": "x"})
    os.chmod(vault_mod.TOKEN_FILE, 0o644)
    _ = vault_mod.load_token()
    mode = stat.S_IMODE(vault_mod.TOKEN_FILE.stat().st_mode)
    assert mode == 0o600


def test_rotate_key_re_encrypts_with_new_key(vault_mod):
    vault_mod.store_credentials("k", "s", "https://127.0.0.1:8182")
    token = {"access_token": "payload"}
    vault_mod.save_token(token)
    old_key = vault_mod._get(vault_mod.ACCOUNT_FERNET_KEY)
    old_ciphertext = vault_mod.TOKEN_FILE.read_bytes()

    vault_mod.rotate_key()

    new_key = vault_mod._get(vault_mod.ACCOUNT_FERNET_KEY)
    new_ciphertext = vault_mod.TOKEN_FILE.read_bytes()
    assert old_key != new_key
    assert old_ciphertext != new_ciphertext
    # old key no longer decrypts; new key does
    assert vault_mod.load_token() == token


def test_wipe_clears_everything(vault_mod):
    vault_mod.store_credentials("k", "s", "https://127.0.0.1:8182")
    vault_mod.save_token({"access_token": "x"})
    assert vault_mod.TOKEN_FILE.exists()

    vault_mod.wipe()

    assert vault_mod.has_credentials() is False
    assert not vault_mod.TOKEN_FILE.exists()


def test_require_missing_raises_with_actionable_message(vault_mod):
    with pytest.raises(vault_mod.CredentialsNotEnrolled) as ei:
        vault_mod.get_api_key()
    assert "--setup" in str(ei.value)


def test_redacting_filter_scrubs_secrets(vault_mod):
    import logging

    log = logging.getLogger("test_vault_redact")
    log.addFilter(vault_mod._RedactingFilter())
    # Build a fake record — we just need to verify the filter mutates the msg
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="my api_key is leaking",
        args=(),
        exc_info=None,
    )
    vault_mod._RedactingFilter().filter(record)
    assert "[REDACTED]" in record.msg


def test_install_runtime_hardening_rejects_schwab_env(vault_mod, monkeypatch):
    monkeypatch.setenv("SCHWAB_API_KEY", "leaked")
    with pytest.raises(vault_mod.VaultError) as ei:
        vault_mod.install_runtime_hardening()
    assert "SCHWAB_" in str(ei.value)


def test_install_runtime_hardening_clean_env(vault_mod, monkeypatch):
    for k in list(os.environ):
        if k.startswith("SCHWAB_"):
            monkeypatch.delenv(k, raising=False)
    # should not raise
    vault_mod.install_runtime_hardening()
