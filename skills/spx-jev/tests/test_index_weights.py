"""The index weights file: dated entries, the newest on or before a day read, never a later one, an entry on file
never rewritten, and the megacap labels reading it through the same rule."""
from __future__ import annotations

import json

import pytest

from conftest import DAY
from spx_jev import index_weights
from spx_jev.index_weights import WEIGHTS_FILE, add, entries, pick, validate

SEPTEMBER = {"NVDA": 0.08, "MSFT": 0.07, "AAPL": 0.065}
OCTOBER = {"NVDA": 0.082, "MSFT": 0.068, "AAPL": 0.064}
DOC = {"note": "hand-kept", "entries": [{"as_of": "2026-09-01", "source": "a", "weights": SEPTEMBER},
                                        {"as_of": "2026-10-06", "source": "b", "weights": OCTOBER}]}


def test_a_day_reads_the_newest_entry_on_or_before_it_and_refuses_a_later_one():
    assert pick(DOC, DAY)["weights"] == SEPTEMBER                 # 09-18: October's weights are not known yet
    assert pick(DOC, "2026-10-06")["weights"] == OCTOBER           # on its own day
    assert pick(DOC, "2026-11-01")["weights"] == OCTOBER
    assert pick(DOC, "2026-08-31") is None
    assert pick({"as_of": "2026-09-17", "weights": SEPTEMBER}, DAY)["weights"] == SEPTEMBER     # the older one-entry shape
    assert pick({"as_of": "2026-09-19", "weights": SEPTEMBER}, DAY) is None
    assert pick(None, DAY) is None and pick({"entries": [{"weights": SEPTEMBER}]}, DAY) is None  # undated: never read


def test_validate_names_a_bad_date_a_share_out_of_range_a_sum_past_the_index_and_a_day_twice():
    assert validate(DOC) == []
    assert validate({"entries": []}) == ["no entries"]
    assert validate({"as_of": "10/06/2026", "weights": SEPTEMBER}) == ["as_of '10/06/2026' is not an ISO date"]
    assert validate({"as_of": "2026-10-06", "weights": {"NVDA": 1.2, "MSFT": True, "AAPL": 0.5, "AMZN": 0.5}}) == [
        "2026-10-06: NVDA = 1.2 is not a share between 0 and 1", "2026-10-06: MSFT = True is not a share between 0 and 1",
        "2026-10-06: the weights sum to 2.200, the whole index or more"]
    twice = {"entries": [{"as_of": "2026-10-06", "weights": SEPTEMBER}, {"as_of": "2026-10-06", "weights": {}}]}
    assert validate(twice) == ["as_of 2026-10-06 is on file twice", "2026-10-06: no weights"]


def test_a_later_set_of_weights_is_a_new_entry_and_the_day_on_file_is_never_rewritten(tmp_path):
    path = tmp_path / WEIGHTS_FILE
    add(path, "2026-09-01", SEPTEMBER, "a", note="hand-kept")
    doc = add(path, "2026-10-06", OCTOBER, "b")
    assert doc == DOC and json.loads(path.read_text()) == DOC and not list(path.parent.glob("*.tmp"))
    with pytest.raises(ValueError, match="2026-10-06 is already on file"):
        add(path, "2026-10-06", {"NVDA": 0.09}, "c")
    with pytest.raises(ValueError, match="not a share between 0 and 1"):
        add(path, "2026-11-01", {"NVDA": 1.5}, "c")
    assert json.loads(path.read_text()) == DOC                      # a refused add leaves the file as it was
    assert entries(json.loads(path.read_text()))[0]["weights"] == SEPTEMBER


def test_the_command_adds_and_shows(tmp_path, capsys):
    assert index_weights.main(["--state-dir", str(tmp_path), "--add", "2026-10-06", "--source", "memory", "--approximate", "NVDA=0.078", "BRK/B=0.016"]) == 0
    assert "1 entries, newest 2026-10-06, 2 names" in capsys.readouterr().out
    assert index_weights.main(["--state-dir", str(tmp_path), "--add", "2026-10-06", "NVDA=0.08"]) == 1
    assert "already on file" in capsys.readouterr().err
    assert index_weights.main(["--state-dir", str(tmp_path), "--show"]) == 0
    out = capsys.readouterr().out
    assert '"BRK/B": 0.016' in out and '"approximate": true' in out and out.rstrip().endswith("sound")
