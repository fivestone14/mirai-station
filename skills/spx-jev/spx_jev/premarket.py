"""The premarket lane (lane.PREMARKET): a read at each checkpoint before the open, on a scene without a diary row.

    python3 -m spx_jev.premarket                                # the launchd job's run at a checkpoint
    python3 -m spx_jev.premarket --send                         # the key comes from .env
    python3 -m spx_jev.premarket --day 2026-09-25 --at 09:28    # a replay, into a scratch folder

Not built yet: the scene, the run, the gate that refuses a late fire and the card are still to come. Until
then the module states only the scene the label families, the grader and the replay are built against.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .state_builder import Scene


def make_premarket_scene(state_dir: Path, now: datetime) -> Scene:
    """The moment ``now`` before the open: a synthetic row (``ts`` now, ``spot`` SPX's prior close times /ES now
    over /ES at that close, ``sigma`` the pre-open ruler in points, ``prior_close``), no bars today, the prior
    sessions' bars and rulers, and the night's overnight store rows finished by ``now`` (Scene.night)."""
    raise NotImplementedError("the premarket scene is not built yet")


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError("the premarket lane is not built yet")


if __name__ == "__main__":
    raise SystemExit(main())
