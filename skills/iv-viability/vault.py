"""
Credential vault for iv-viability skill.

Stores Schwab API credentials in the macOS Keychain (local, not iCloud)
and encrypts the OAuth token file at rest with Fernet. Designed so that
no plaintext secret ever touches the filesystem.

Public surface:
    get_api_key()        -> str
    get_app_secret()     -> str
    get_callback_url()   -> str
    load_token()         -> dict            (decrypts token file)
    save_token(token)    -> None            (encrypts + chmod 600)
    store_credentials(api_key, app_secret, callback_url) -> None
    wipe()               -> None
    rotate_key()         -> None
    has_credentials()    -> bool
    token_file_exists()  -> bool
    install_runtime_hardening() -> None     (redaction, rlimit, excepthook)
"""
from __future__ import annotations

import json
import logging
import os
import re
import resource
import stat
import sys
import traceback
from pathlib import Path

SERVICE = "iv-viability-schwab"
ACCOUNT_API_KEY = "api_key"
ACCOUNT_APP_SECRET = "app_secret"
ACCOUNT_CALLBACK_URL = "callback_url"
ACCOUNT_FERNET_KEY = "fernet_key"

SKILL_DIR = Path(__file__).resolve().parent
TOKEN_FILE = SKILL_DIR / ".schwab_token.json.enc"
LOCK_FILE = SKILL_DIR / ".schwab_token.lock"

SECRET_PATTERNS = re.compile(
    r"(api_key|app_secret|access_token|refresh_token|fernet_key|"
    r"authorization|bearer\s+[\w\-\.]+)",
    re.IGNORECASE,
)


class VaultError(RuntimeError):
    """Base class for vault failures with a user-facing message."""


class CredentialsNotEnrolled(VaultError):
    pass


class TokenDecryptFailed(VaultError):
    pass


class KeyringUnavailable(VaultError):
    pass


def _import_keyring():
    try:
        import keyring
        import keyring.errors
        return keyring
    except ImportError as e:
        raise KeyringUnavailable(
            "Missing dependency: pip install -r requirements.txt"
        ) from e


def _import_fernet():
    try:
        from cryptography.fernet import Fernet, InvalidToken
        return Fernet, InvalidToken
    except ImportError as e:
        raise KeyringUnavailable(
            "Missing dependency: pip install -r requirements.txt"
        ) from e


def _import_filelock():
    try:
        from filelock import FileLock
        return FileLock
    except ImportError as e:
        raise KeyringUnavailable(
            "Missing dependency: pip install -r requirements.txt"
        ) from e


def _get(account: str) -> str | None:
    keyring = _import_keyring()
    try:
        return keyring.get_password(SERVICE, account)
    except Exception as e:
        raise VaultError(f"Keychain read failed for {account}: {type(e).__name__}") from e


def _set(account: str, value: str) -> None:
    keyring = _import_keyring()
    try:
        keyring.set_password(SERVICE, account, value)
    except Exception as e:
        raise VaultError(f"Keychain write failed for {account}: {type(e).__name__}") from e


def _delete(account: str) -> None:
    keyring = _import_keyring()
    try:
        keyring.delete_password(SERVICE, account)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception as e:
        raise VaultError(f"Keychain delete failed for {account}: {type(e).__name__}") from e


def _require(account: str) -> str:
    value = _get(account)
    if value is None:
        raise CredentialsNotEnrolled(
            f"Credentials not enrolled ({account} missing). "
            "Run: python3 iv_fetcher.py --setup"
        )
    return value


def get_api_key() -> str:
    return _require(ACCOUNT_API_KEY)


def get_app_secret() -> str:
    return _require(ACCOUNT_APP_SECRET)


def get_callback_url() -> str:
    return _require(ACCOUNT_CALLBACK_URL)


def has_credentials() -> bool:
    return (
        _get(ACCOUNT_API_KEY) is not None
        and _get(ACCOUNT_APP_SECRET) is not None
        and _get(ACCOUNT_CALLBACK_URL) is not None
        and _get(ACCOUNT_FERNET_KEY) is not None
    )


def token_file_exists() -> bool:
    return TOKEN_FILE.exists()


# ---------------------------------------------------------------------------
# Cassandra's Edge / market-research MCP bearer (kept in its own Keychain
# service so it can be rotated independently of the Schwab credentials).
# ---------------------------------------------------------------------------

CASS_SERVICE = "iv-viability-cassandra"
CASS_ACCOUNT = "cassandra_edge_token"


def get_cassandra_token() -> str:
    """The market-research MCP bearer, read from Keychain only — never from
    ~/.claude.json at runtime (that file holds all seven tokens in plaintext)."""
    keyring = _import_keyring()
    try:
        tok = keyring.get_password(CASS_SERVICE, CASS_ACCOUNT)
    except Exception as e:
        raise VaultError(f"Keychain read failed for cassandra token: {type(e).__name__}") from e
    if tok is None:
        raise CredentialsNotEnrolled(
            "Cassandra MCP token not enrolled. Run: python3 native_gex_feed.py --setup"
        )
    return tok


def set_cassandra_token(token: str) -> None:
    keyring = _import_keyring()
    if not token:
        raise VaultError("set_cassandra_token: empty token")
    try:
        keyring.set_password(CASS_SERVICE, CASS_ACCOUNT, token)
    except Exception as e:
        raise VaultError(f"Keychain write failed for cassandra token: {type(e).__name__}") from e


def has_cassandra_token() -> bool:
    keyring = _import_keyring()
    try:
        return keyring.get_password(CASS_SERVICE, CASS_ACCOUNT) is not None
    except Exception:
        return False


def _get_fernet():
    Fernet, _ = _import_fernet()
    key = _get(ACCOUNT_FERNET_KEY)
    if key is None:
        raise CredentialsNotEnrolled(
            "Fernet key missing from Keychain. Run --setup to re-enroll."
        )
    return Fernet(key.encode("ascii"))


def _generate_fernet_key() -> str:
    Fernet, _ = _import_fernet()
    return Fernet.generate_key().decode("ascii")


def _enforce_perms(path: Path) -> None:
    if not path.exists():
        return
    current = stat.S_IMODE(path.stat().st_mode)
    if current != 0o600:
        os.chmod(path, 0o600)
        logging.getLogger(__name__).warning(
            "fixed permissions on %s (was %o, now 600)", path.name, current
        )


def store_credentials(api_key: str, app_secret: str, callback_url: str) -> None:
    """Store the three long-lived credentials in Keychain + generate Fernet key."""
    if not api_key or not app_secret or not callback_url:
        raise VaultError("store_credentials: all three fields required")
    _set(ACCOUNT_API_KEY, api_key)
    _set(ACCOUNT_APP_SECRET, app_secret)
    _set(ACCOUNT_CALLBACK_URL, callback_url)
    if _get(ACCOUNT_FERNET_KEY) is None:
        _set(ACCOUNT_FERNET_KEY, _generate_fernet_key())


def load_token() -> dict:
    """Decrypt the token file into a dict. Raises if missing or corrupt."""
    _, InvalidToken = _import_fernet()
    FileLock = _import_filelock()
    if not TOKEN_FILE.exists():
        raise CredentialsNotEnrolled(
            "Encrypted token file missing. Run --setup first."
        )
    _enforce_perms(TOKEN_FILE)
    with FileLock(str(LOCK_FILE)):
        ciphertext = TOKEN_FILE.read_bytes()
    fernet = _get_fernet()
    try:
        plaintext = fernet.decrypt(ciphertext)
    except InvalidToken as e:
        raise TokenDecryptFailed(
            "Token file corrupt or Fernet key mismatch. "
            "Run --setup to re-authorize."
        ) from e
    try:
        return json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise TokenDecryptFailed(f"Token payload malformed: {type(e).__name__}") from e


def save_token(token: dict) -> None:
    """Encrypt token dict and write to disk with 0600 perms."""
    FileLock = _import_filelock()
    fernet = _get_fernet()
    plaintext = json.dumps(token).encode("utf-8")
    ciphertext = fernet.encrypt(plaintext)
    with FileLock(str(LOCK_FILE)):
        tmp = TOKEN_FILE.with_suffix(TOKEN_FILE.suffix + ".tmp")
        tmp.write_bytes(ciphertext)
        os.chmod(tmp, 0o600)
        os.replace(tmp, TOKEN_FILE)
        _enforce_perms(TOKEN_FILE)


def rotate_key() -> None:
    """Re-encrypt the existing token with a newly generated Fernet key."""
    token = load_token()  # decrypt with old key
    _set(ACCOUNT_FERNET_KEY, _generate_fernet_key())
    save_token(token)  # encrypt with new key


def wipe() -> None:
    """Delete every Keychain entry and the encrypted token file."""
    for account in (
        ACCOUNT_API_KEY,
        ACCOUNT_APP_SECRET,
        ACCOUNT_CALLBACK_URL,
        ACCOUNT_FERNET_KEY,
    ):
        _delete(account)
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()


# ---------------------------------------------------------------------------
# Runtime hardening
# ---------------------------------------------------------------------------


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if SECRET_PATTERNS.search(msg):
                record.msg = SECRET_PATTERNS.sub("[REDACTED]", msg)
                record.args = ()
        except Exception:
            record.msg = "[REDACTED log record]"
            record.args = ()
        return True


def _redacting_excepthook(exc_type, exc_value, tb):
    lines = traceback.format_exception(exc_type, exc_value, tb)
    scrubbed = [SECRET_PATTERNS.sub("[REDACTED]", line) for line in lines]
    sys.stderr.write("".join(scrubbed))


def install_runtime_hardening() -> None:
    """Block core dumps, redact logs, scrub tracebacks. Idempotent."""
    # Block core dumps so a crash cannot persist in-memory secrets.
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass

    # Install redaction filter on every existing and future logger we care about.
    redactor = _RedactingFilter()
    for name in ("", "httpx", "httpcore", "schwab", "authlib", "urllib3"):
        log = logging.getLogger(name)
        log.addFilter(redactor)
        if name in ("httpx", "httpcore", "schwab", "authlib", "urllib3"):
            log.setLevel(logging.WARNING)

    # Scrub tracebacks.
    sys.excepthook = _redacting_excepthook

    # Refuse to read credentials from environment variables — fail loud if set.
    leaked = [k for k in os.environ if k.startswith("SCHWAB_")]
    if leaked:
        raise VaultError(
            f"Refusing to run: SCHWAB_* environment variables detected ({leaked}). "
            "This skill only reads credentials from the macOS Keychain."
        )
