"""Mirai Watch — runtime package for the mirai-station intraday loop (gex-only: hunter scan, macro-mood, alerts, auth watch)."""
# Cosmetic warning suppression. Must run before any submodule imports lancedb
# or langgraph, AND must re-apply after langchain_core loads (because
# `langchain_core.__init__` calls `surface_langchain_deprecation_warnings()`
# which installs a "default" filter that beats earlier "ignore" filters).
import warnings


def _silence_warnings() -> None:
    warnings.filterwarnings("ignore", message=r".*NotOpenSSLWarning.*")
    warnings.filterwarnings("ignore", message=r".*OpenSSL 1\.1\.1\+.*")
    warnings.filterwarnings("ignore", message=r".*LibreSSL.*")
    warnings.filterwarnings("ignore", message=r"The default value of `allowed_objects`.*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langgraph(\.|$)")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langchain(\.|$)")
    warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
    try:
        from urllib3.exceptions import NotOpenSSLWarning  # type: ignore
        warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
    except Exception:
        pass
    try:
        from langchain_core._api.deprecation import (
            LangChainDeprecationWarning,
            LangChainPendingDeprecationWarning,
        )
        warnings.filterwarnings("ignore", category=LangChainDeprecationWarning)
        warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
    except Exception:
        pass


# Initial pass — silences urllib3 etc. that fire on first import.
_silence_warnings()

# Eagerly import langchain_core so its `surface_langchain_deprecation_warnings()`
# runs NOW (with our ignore filters mostly in place), then re-apply our filters
# afterwards so they win against the "default" filters langchain just installed.
try:
    import langchain_core  # noqa: F401
except ImportError:
    pass

_silence_warnings()
