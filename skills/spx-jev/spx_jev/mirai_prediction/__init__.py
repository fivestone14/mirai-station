"""The Mirai Prediction System: a second learning loop that runs beside the SPX JEV service and learns on its own.

It never touches the live read, the grader, pool_v1 or the phone's call. Its pieces, named as on the design diagram:

    code_features      the code feature builder: the 51 ready code questions answered from each read's stored data (layers 1-2)
    answer_matrix      one row per past read: every answer (layers 1-3), the historical odds, JEV's own call, the result
    additive_scorer    each answer pushes the historical odds; one vote per group; a layer volume per layer and stage
    matcher            the 20 past reads most like today, tallied by how far each beat its own time-of-day odds
    jev_corrected      JEV's own call fixed with four fitted numbers
    pool_v2            today's voices plus the three learners, weighted by track record; pool_v1 stays as it is
    read_hook          after every live read: forecast with every voice, write the lines (never on the read's path)
    nightly_job        after the close: build the tables, score the voices, update pool_v2, refit for tomorrow, scoreboard
    scoreboard         the phone card's file: after N reads, how right each voice is and how it compares with the odds

Data lives under state/spx_jev/mirai_prediction/ (paths.py). Old store names are mapped once in name_map.py.
"""
