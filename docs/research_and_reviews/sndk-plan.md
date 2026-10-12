# SNDK Pro — the plan, reorganised around the observer

Superseded: the Phase 1-5 numbering. Those were written when the model made a
directional call, and half of what they scheduled has been overtaken by
measurement rather than completed.

## What the system now is

The model is an OBSERVER of the whole board. It reads the payload, decides for
itself what matters, and says it in two plain sentences plus a few price levels
worth watching. It makes no forecast, because the forecast was measured at zero
forward value across 391 readings and 368 level arrivals.

Python does exactly one thing the model cannot: it supplies TIME. Ranks against
prior closed sessions, and what changed since the last option book. Facts, never
verdicts.

## Done

  A. Payload reduction        4,118 -> 2,681 bytes, 144 -> 112 leaf names
  B. Wake gate (wk-1)         false alarms 26.0% -> 11.8%, bursts 6 -> 3
  C. Observer contract (obs-2){quiet, read, points[]}; model reasons, Python
                              supplies history; 3.4-10.8s per call
  D. Effort pinned            the 100s timeouts were a global settings edit,
                              not the contract
  E. Tool grant actually shut Bash executed under both old and new argv until
                              Bash/WebSearch went on the DISALLOW list
  F. One lane (obs-3)         Lane A deleted (direction surface measured
                              worthless); every read opens with the
                              since-last-read frame; plain-english renames
  G. Honest frame (obs-4)     the since-last-read window seeded with spot_then,
                            walls compared ladder-to-ladder, no diary wall frozen
                            beside an empty ladder, a stem + position + semantic
                            guard, and two new measured blocks: the day's boxes
                            (context.ranges) and where the weight sits
                            (structure). Crossings/approaches measured and NOT
                            built (a $2 event at a $2 noise floor).
  H. Minute bars (obs-5)     a sidecar job keeps the 1-min bars the scanner
                            already fetched; extremes, boxes and the prior
                            range read the wicks and label the witness.

## Next, in order

  1. MEASUREMENT (was Phase 5). The decoy control: every ~20th call, hand the
     model a random prior session's scene, unlabelled. If it finds as much to
     say about old tape as live tape, it is pattern-matching noise. Plus the
     nightly scorecard: chosen-vs-forced abstain split, prose-survival rate,
     repeat rate, wake-conditioned quiet.
     WHY FIRST: obs-3 is brand new and we know only that it runs.

  2. LEVEL EVIDENCE. Ship `gross_gex` as dollars of stock per 1% move — the
     board has no absolute magnitude at all today, only relative shares — and
     get stock VOLUME onto the diary row so that number has a denominator.
     Without ADV, "$239M of delta per 1% move" is a figure a reader cannot size.

  3. LAB ETL BACKFILL. Last ingested 07-31, holds 4 usable days while
     state/sndk_reads has been writing continuously. The "5-7 effective days"
     constraint that shaped every prior audit is stale.

  4. THE SCREEN (was Phase 4). Tier 0's eight numbers, novelty by rank-among-
     days, the four visual states. Deliberately after 1: the screen renders the
     model's output and that output is still settling.

  5. PATTERN RECALL. The diary now stores quiet reads and the levels the model
     named, which is the precondition. Needs sessions before it can say
     anything; build the pipe, expect it silent for weeks.

## Closed by measurement — do not re-file these as bugs

  Dealer positioning        the sign is a convention written for the index, and
                            the studies that measured single stocks found it
                            backwards. Never assert who holds what.
  The gamma flip as a level exists only because the convention puts dealers on
                            opposite sides of calls and puts; under either
                            same-sign convention there is no flip at any price.
                            Its uncertainty (0.46 sigma) exceeds its distance
                            from spot (0.27 sigma).
  Amplify / dampen          within-day, day-clustered: no effect. At 30 minutes
                            the only estimate separating from zero says negative
                            gamma MEAN-REVERTS, the opposite of the textbook.
  Pinning at strikes        the strike grid is 27-54x finer than one day's move
                            on this name, and weeklies show no significant pin.
  Walls as levels           defined as the heaviest strike on their side of
                            spot, so they relabel with probability 1.000 on a
                            crossing. Describable as structure, never as a place
                            price reaches.
  Direction ML              lag-1 autocorrelation 0.918 gives ~8 independent
                            observations a day; a 0.60-AUC classifier needs ~30
                            months of them.
