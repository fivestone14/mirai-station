"""The package itself: a feed run as ``python3 -m spx_jev.<feed>`` prints nothing but its own lines, and the
package's names still load."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import spx_jev

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", ["spx_jev.market_context", "spx_jev.overnight", "spx_jev.bars", "spx_jev.service"])
def test_a_module_run_by_the_jobs_is_not_already_imported_by_the_package(module):
    """runpy warned "found in sys.modules after import of package" on every context and overnight run, because the
    package imported the labels, which import both feeds. The warning is an error here."""
    run = subprocess.run([sys.executable, "-W", "error::RuntimeWarning", "-m", module, "--help"], cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "RuntimeWarning" not in run.stderr, run.stderr


def test_the_packages_names_load_on_first_use():
    from spx_jev.ask import send
    from spx_jev.state_builder import Scene
    assert spx_jev.send is send and spx_jev.Scene is Scene and callable(spx_jev.build_labels)
    with pytest.raises(AttributeError):
        spx_jev.not_a_name
