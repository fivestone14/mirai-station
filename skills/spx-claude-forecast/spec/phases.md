# spx-claude-forecast: implementation phases

Each phase ends with a check that proves it is done. **The phone app is not changed until Phase 9.** Nothing starts without Will's go,
and commits go on main with `TZ=UTC`. The full detail is in `build_plan.md` §13 and in
`storage_design.md`.

| # | Phase | What gets built | Done when |
|---|---|---|---|
| ✓ | Design | plan, payload mockup, storage design, rulebook draft | finished 10-09 |
| ✓ 0 | Save at-risk data (live 10-09: 181 sessions of 5-min bars back to 01-22, 59 siege days, 22 raw-tape days, the Friday book, VIX1D closes; iCloud mirror) | `recorder/` copies of the dated options book, the siege SPY minutes, the SPX 5-min bars and the VIX1D close, plus a nightly backup | the dated book is copied before **Mon 10-12 08:17 ET**, and the siege copy is taken before Tue 10-13 |
| ✓ 1 | Clear field names (locked 10-09, see `record_formats.md`) | rename every property in the reads and outcomes files, and in Claude's reply, so a newcomer can read a line cold; the rename map and example lines go in `record_formats.md`, and the rulebook's OUTPUT section uses the new names | a blind reader who has never seen the system explains every field correctly from the example lines alone, and the names are locked before Phase 2 writes any file |
| ✓ 2 | Foundation (10-09) | package skeleton, `state/spx_claude_forecast/`, `control.json`, kill switch, append-only writers, `STORE_MAP.json` | the "never writes Pool 2" test passes, and with the switch on only a `paused` line is written |
| ✓ 3 | Payload builder (10-09; 15 blocks, no builder errors on the real 14:30 read) | the 18 blocks cut at read time, plus leak checks | rebuilding 10-09 14:30 reproduces `payload_mockup.json`, and every leak check is made to fail once |
| ✓ 4 | History + grading (10-09; grades match the station's own on every live read; seed runs 10-10) | grader, seed from 07-20, `library/rows.parquet`, base rate, 8 cards | the 14:30 base rate matches the plan's counts, and the grader matches `integral_grades` on every live read |
| ✓ 5 | Claude call (10-09; two real calls on the subscription, every check passed) | rulebook, `claude -p` call, checks, 2 samples averaged | 5 frozen payloads all parse, sum to 100 and are grounded, the canary call shows no CLAUDE.md or memory leaking in, and the call refuses to run when an API key is set (subscription only) |
| ✓ 6 | Go live (hook wired into spx-jev/service.py 10-09; first live reads Mon 10-12 09:32 ET) | one guarded line in `spx-jev/service.py` | each day has 13 payload lines, the SPX read time is unchanged, and the kill switch works mid-session |
| ✓ 7 | Nightly + scorecard (10-09; launchd 17:15 ET; first real run Mon 10-12) | seal, library rebuild, `scores.parquet`, `scorecard.json`. The phone app is not touched | the nightly integrity checks pass and `scorecard.json` is written each night |
| 8 | Review | test arms and the checkpoints at 150 / 300 / 600 reads | Will decides whether to show the base rate, how many samples to take, which model to use, and whether to run a Pool 2 trial |
| 9 | Phone view (last) | once the build is done, a mockup of the SPX phone page (`jev-spx.html`) showing exactly where the Claude read appears, with every change highlighted, checked at 360 px. The page is only built after Will approves the mockup | Will signs off on the mockup; then the tile is built, the 360 px tests pass, and nothing else on the page moves |
