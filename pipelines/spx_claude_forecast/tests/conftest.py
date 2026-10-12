"""The pytest fixtures: a temp state root with the forecast folder, and the real 10-09 14:30 read loaded once per session
for the smoke tests (skipped without the station's state). Plain helpers live in payload_fixtures.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payload_fixtures import REAL_READ_ID, state_over_the_station, station_state_dir  # noqa: E402

from spx_claude_forecast import paths as forecast_paths  # noqa: E402
from spx_claude_forecast.payload.frozen_inputs import FrozenInputs, load_frozen_inputs  # noqa: E402


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """A station state root with the forecast folder created, and it plus ``tmp_path/backup`` writable."""
    root = tmp_path / "state"
    forecast_paths.ensure_folders(root, tmp_path / "backup")
    return root


@pytest.fixture(scope="session")
def station_state(tmp_path_factory) -> Path:
    """One temp state root over the real station stores for the whole session: the smoke tests read the station's
    files through it and write only into its temp forecast folder, so every cache warmed by one test serves the next."""
    if station_state_dir() is None:
        pytest.skip("needs the station's state")
    return state_over_the_station(tmp_path_factory.mktemp("station"))


@pytest.fixture(scope="session")
def real_inputs(station_state: Path) -> FrozenInputs:
    """The real 10-09 14:30 read's frozen inputs, loaded once (about 25 seconds cold); tests read it, never change it."""
    return load_frozen_inputs(station_state, forecast_paths.ensure_folders(station_state), REAL_READ_ID)
