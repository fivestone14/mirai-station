"""Mirai Watch — runtime package for the mirai-station intraday loop (gex-only: hunter scan, macro-mood, alerts, auth watch)."""
# Cosmetic warning suppression. Must run before any submodule imports urllib3,
# whose OpenSSL/LibreSSL notice fires on first import.
import warnings


def _silence_warnings() -> None:
    warnings.filterwarnings("ignore", message=r".*NotOpenSSLWarning.*")
    warnings.filterwarnings("ignore", message=r".*OpenSSL 1\.1\.1\+.*")
    warnings.filterwarnings("ignore", message=r".*LibreSSL.*")
    warnings.filterwarnings("ignore", message=r"The default value of `allowed_objects`.*")
    warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
    try:
        from urllib3.exceptions import NotOpenSSLWarning  # type: ignore
        warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
    except Exception:
        pass


_silence_warnings()
