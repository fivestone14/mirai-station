"""Put the skill dir on sys.path (left-eye conftest discipline) and keep the
sidecar inert on import — nothing here should warm a model or open a socket."""
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))


@pytest.fixture(autouse=True)
def _server_stays_down(monkeypatch):
    monkeypatch.setenv("MIRAI_VOICE_DISABLE", "1")
