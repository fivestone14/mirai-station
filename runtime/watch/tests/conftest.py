"""Test isolation for the watch suite.

The tick graph's alert path calls ``watch.push.send``. Integration tests that
drive the graph (e.g. ``TestTickGraphIntegration``) fire the 0DTE dead-zone
alert on their fixed-clock fixture bet, which must never reach a real channel.

This autouse fixture forces the channel to ``None``, so tests still verify push
*intent* without ever paging anyone. monkeypatch reverts it after each test.
"""
from __future__ import annotations

import pytest

from watch import push


@pytest.fixture(autouse=True)
def _isolate_push_channel(monkeypatch):
    monkeypatch.setattr(push, "_channel", None, raising=False)
