"""Test isolation for the watch suite.

The tick graph's alert path calls ``watch.push.send``, which appends to a daily
JSONL log under ``paths.LOG_DIR`` — the *real* production log dir, snapshotted
into ``push._LOG_DIR`` at import time. Integration tests that drive the graph
(e.g. ``TestTickGraphIntegration``) fire the 0DTE dead-zone alert on their
fixed-clock fixture bet, which would otherwise leak a fake
``QQQ $500 ... opened 2026-06-11 ... regime: ?`` row into the live push log on
every run (dispatched:false, because no channel is wired in tests).

This autouse fixture redirects the push log to a throwaway per-test temp dir and
forces the channel to ``None``, so tests still verify push *intent* without ever
touching production state. monkeypatch reverts both after each test.
"""
from __future__ import annotations

import pytest

from watch import push


@pytest.fixture(autouse=True)
def _isolate_push_log(tmp_path, monkeypatch):
    monkeypatch.setattr(push, "_LOG_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(push, "_channel", None, raising=False)
