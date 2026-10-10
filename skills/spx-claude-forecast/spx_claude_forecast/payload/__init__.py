"""The payload: one frozen scene of the market per SPX read, built from saved files only.

    frozen_inputs.py   loads everything a block may read about one read, cut at the read's time
    units.py           how numbers are written (sig, ranks, percents)
    block_result.py    what a block returns: its facts plus every fact it left out and why
    blocks/            one module per block, ``build_<name>_block(inputs) -> BlockResult``
    build.py           assembles the blocks in order, declares absences, runs the leak checks, hashes the scene
"""
