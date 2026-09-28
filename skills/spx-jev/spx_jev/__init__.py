"""spx-jev — the JEV decision service beside the SPX diary: labels, requests, the sums,
grading, and the phone's card.

Read-only over the station's stored files; writes only under state/spx_jev/ (the service,
and the two feeds that keep today's bars and the market around SPX). Nothing here touches
the live loop.

The names below load on first use, not when the package is imported: ``python3 -m
spx_jev.market_context`` imports the package before it runs the module, and the labels
import market_context and overnight, so importing them here put each feed in sys.modules
ahead of its own run, which runpy warned about on every run.
"""

from importlib import import_module

_HOMES = {"Scene": ".state_builder", "make_scene": ".state_builder", "build_labels": ".labels.registry",
          "build_requests": ".ask", "load_questions": ".ask", "send": ".ask"}

__all__ = ["Scene", "build_labels", "make_scene", "build_requests", "load_questions", "send"]


def __getattr__(name: str):
    if name not in _HOMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(_HOMES[name], __name__), name)
