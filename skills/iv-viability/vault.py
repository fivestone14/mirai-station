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

# THE VALUE, NOT JUST THE LABEL (2026-09-09). The original pattern matched the
# WORDS — so `refresh_token=eyJhbGci...` scrubbed to `[REDACTED]=eyJhbGci...` and
# the credential itself sailed through, in a log line and in a traceback (both
# measured). That was survivable while the only secret was a Schwab key nothing
# ever put in a message body; it is not survivable now that an OAuth refresh
# token rides in a POST body httpx can be asked to log.
#
# Two additions, both shaped tightly enough to leave ordinary market data alone:
#   * a JWT is unmistakable — three base64url runs separated by dots, opening
#     with the `eyJ` that every {"alg": header encodes to;
#   * `secret=value` / "secret": "value" — the value is replaced first, then the
#     older word pass takes the label, so the pair reads `[REDACTED]=[REDACTED]`.
SECRET_WORDS = (r"api_key|app_secret|access_token|refresh_token|fernet_key|"
                r"client_secret|authorization|code_verifier")

SECRET_PATTERNS = re.compile(
    r"("
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"   # a JWT, whole
    r"|bearer\s+[\w\-\.]+"                                         # Bearer <value>
    r"|" + SECRET_WORDS +
    r")",
    re.IGNORECASE,
)

# Applied BEFORE the word pattern, so the value dies with the label rather than
# being orphaned next to a [REDACTED] that names it.
SECRET_ASSIGNMENTS = re.compile(
    r"(?P<label>" + SECRET_WORDS + r")"
    r"(?P<sep>\s*[=:]\s*\"?)"
    r"(?P<value>[^\s\"',&}]+)",
    re.IGNORECASE,
)


def _scrub(text: str) -> str:
    """Every redaction path goes through here, so log lines and tracebacks can
    never drift apart on what counts as a secret."""
    text = SECRET_ASSIGNMENTS.sub(lambda m: m.group("label") + m.group("sep") + "[REDACTED]", text)
    return SECRET_PATTERNS.sub("[REDACTED]", text)


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
            "Cassandra MCP not enrolled. Run: python3 native_gex_feed.py --login"
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


def clear_cassandra_token() -> None:
    """Drop the legacy static bearer. Called by the OAuth enrolment so a dead
    long-lived token cannot linger and be picked up by the fallback path."""
    keyring = _import_keyring()
    try:
        keyring.delete_password(CASS_SERVICE, CASS_ACCOUNT)
    except Exception:
        pass


# --- OAuth (2026-09-09) -----------------------------------------------------
# The endpoint stopped honouring long-lived bearers on 2026-09-09 and moved to
# AuthKit OAuth (the loopback authorization-code flow — its registration endpoint
# refuses device-code clients) whose ACCESS tokens live 300 seconds. A static token
# in CASS_ACCOUNT is therefore dead five minutes after it is minted, however
# carefully it was copied. What survives is the REFRESH token, so that is what
# is enrolled; the access token is a cache with an expiry beside it, and the
# client registration (RFC 7591 dynamic registration) is kept so re-enrolling
# does not register a new client every time.
#
# All three live in the same Keychain service as the token they replace, so a
# rotation still touches exactly one place and `security delete-generic-password
# -s iv-viability-cassandra` still wipes everything in one command.

CASS_ACCOUNT_REFRESH = "cassandra_edge_refresh"   # the durable credential
CASS_ACCOUNT_ACCESS = "cassandra_edge_access"     # {"token": ..., "expires_at": epoch}
CASS_ACCOUNT_CLIENT = "cassandra_edge_client"     # {"issuer", "client_id", ...}


def get_cassandra_refresh_token() -> str:
    """The durable OAuth credential. Absent = not enrolled, which is a setup
    problem and not a transient one, so it raises rather than returning None."""
    tok = _cass_get(CASS_ACCOUNT_REFRESH)
    if tok is None:
        raise CredentialsNotEnrolled(
            "Cassandra OAuth not enrolled. Run: python3 native_gex_feed.py --login"
        )
    return tok


def set_cassandra_refresh_token(token: str) -> None:
    if not token:
        raise VaultError("set_cassandra_refresh_token: empty token")
    _cass_set(CASS_ACCOUNT_REFRESH, token)


def has_cassandra_refresh_token() -> bool:
    return _cass_get(CASS_ACCOUNT_REFRESH) is not None


def get_cassandra_access() -> dict | None:
    """The cached access token as {"token", "expires_at"}, or None when there is
    nothing cached or the cache is unreadable. A torn cache is not an error —
    the caller simply refreshes."""
    raw = _cass_get(CASS_ACCOUNT_ACCESS)
    if not raw:
        return None
    try:
        blob = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return blob if isinstance(blob, dict) and blob.get("token") else None


def set_cassandra_access(token: str, expires_at: float) -> None:
    _cass_set(CASS_ACCOUNT_ACCESS, json.dumps({"token": token, "expires_at": expires_at}))


def get_cassandra_client() -> dict | None:
    """The dynamic client registration + discovered endpoints, or None."""
    raw = _cass_get(CASS_ACCOUNT_CLIENT)
    if not raw:
        return None
    try:
        blob = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return blob if isinstance(blob, dict) and blob.get("client_id") else None


def set_cassandra_client(blob: dict) -> None:
    _cass_set(CASS_ACCOUNT_CLIENT, json.dumps(blob))


def wipe_cassandra_oauth() -> None:
    """Forget the whole OAuth enrolment (used by --logout and by a failed login
    so a half-finished enrolment never half-works)."""
    keyring = _import_keyring()
    for account in (CASS_ACCOUNT_REFRESH, CASS_ACCOUNT_ACCESS, CASS_ACCOUNT_CLIENT):
        try:
            keyring.delete_password(CASS_SERVICE, account)
        except Exception:
            pass


def _cass_get(account: str) -> str | None:
    keyring = _import_keyring()
    try:
        return keyring.get_password(CASS_SERVICE, account)
    except Exception as e:
        raise VaultError(f"Keychain read failed for {account}: {type(e).__name__}") from e


def _cass_set(account: str, value: str) -> None:
    keyring = _import_keyring()
    try:
        keyring.set_password(CASS_SERVICE, account, value)
    except Exception as e:
        raise VaultError(f"Keychain write failed for {account}: {type(e).__name__}") from e


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
            scrubbed = _scrub(msg)
            if scrubbed != msg:
                record.msg = scrubbed
                record.args = ()
        except Exception:
            record.msg = "[REDACTED log record]"
            record.args = ()
        return True


def _redacting_excepthook(exc_type, exc_value, tb):
    lines = traceback.format_exception(exc_type, exc_value, tb)
    scrubbed = [_scrub(line) for line in lines]
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
