# System map

One project, one folder: `~/mirai_awakening`. Every folder is named for what it holds. The code
never assumes where the repo sits (each module finds the root from its own position), while the
launchd plists spell the final home as `$HOME/mirai_awakening`. `station_paths.py` at the root
names every data store once; `data/` (git-ignored) is the data root; `docs/data_map.md` is the one
page to look a store up on (its path, what one row holds, who writes and reads it). The retired
`skills/sndk-jev/` is the one folder left from the old layout, until it is removed.

## Folders

| folder | holds | tests |
|---|---|---|
| `station_paths.py` | every data store's name and its place under `data/`; the one root resolver (`--state-dir`, else `MIRAI_STATE_DIR`, else `MIRAI_STATION_STATE`, else `<repo>/data`) | every suite's fixtures use it with a temporary root |
| `pipelines/spx_options_scanner/` | the SPX options scanner (the left eye): the hunter, the Gravity Engine, the three heads, the Watchtower, the dated book, the grader, the bridges to the two sensors | `pipelines/spx_options_scanner/tests` |
| `pipelines/spx_jev_questions_and_pools/` | the SPX JEV decision service (the `spx_jev` package): its lanes, feeds, questions, the Mirai Prediction System; its plist templates under `launchd/` | `pipelines/spx_jev_questions_and_pools/tests` |
| `pipelines/spx_claude_forecast/` | Claude's own SPX forecast (the `spx_claude_forecast` package): the read hook, the recorder, the nightly job | `pipelines/spx_claude_forecast/tests` |
| `pipelines/sndk_options_scanner_and_reader/` | the SNDK scanner, its bar sidecar, the reader and the reader's memory | `pipelines/sndk_options_scanner_and_reader/tests` |
| `pipelines/shared_text_embedder_for_note_search/` | `embed.py`, the bge-small embedder the two memories (`lefteye_rag`, `sndk_rag`) rank their notes with | none |
| `data_collectors/schwab_live_stream/` | the station's one Schwab streaming connection (SPY book, SPXW focus set, `$SPX`'s live price file for Kaze), run inside the lob-collector | `data_collectors/spx_options_tape_collector/tests` (through the collector) |
| `data_collectors/schwab_market_data_client/` | the Schwab sign-in, token vault and fetchers every pipeline imports (`iv_fetcher`, `vault`) | `data_collectors/schwab_market_data_client/tests` |
| `data_collectors/spx_options_tape_collector/` | the `lob_flow` package: the same-day SPX options tape and depth collector, run through the scanner's `lob_bridge.py` | `data_collectors/spx_options_tape_collector/tests` |
| `data_collectors/spy_volume_at_walls_sensor/` | the `siege` package: SPY volume spent at dealer walls, run inside the scan through `siege_bridge.py` | `data_collectors/spy_volume_at_walls_sensor/tests` |
| `screens/desktop_and_phone_server/` | the read-only HTTP server on :8787, the desktop page and the phone pages under `static/m/` | `screens/desktop_and_phone_server/tests` |
| `screens/desktop_options_trades_3d_view/` | the `book_flow` package behind the desktop's Day Visual LOB board | `screens/desktop_options_trades_3d_view/tests` |
| `screens/android_phone_app/` | the Android shell that opens the phone pages | none (Gradle) |
| `screens/voice_desk/` | the spoken SNDK conversation sidecar on :8788 | `screens/voice_desk/tests` |
| `alerts_and_phone_pushes/` | the `watch` package: the alert pass, the phone pushes, the market-hours gate, the Schwab login check, the two dead-man's switches; run as `python -m watch...` from this folder | `alerts_and_phone_pushes/tests` |
| `settings/` | `limits-and-cooldowns.json`, the tunable numbers `watch.intraday.settings` and the Watchtower read | checked by the watch suite |
| `scheduled_jobs/launchd_job_definitions/` | the 25 LaunchAgent plists, symlinked into `~/Library/LaunchAgents` by the installer | `alerts_and_phone_pushes/tests/test_station_jobs.py` |
| `scheduled_jobs/job_start_scripts/` | `env.sh`, `start_job_with_logs.sh`, the `run-*.sh` runners, the installer and uninstaller, the venv bootstrap | the same test, which runs the gated runners in a stand-in station |
| `health_checks/` | the live health review (`MIRAI_LIVE=1 pytest health_checks`) and its always-on unit tests | `health_checks` |
| `docs/` | `data_map.md` (every store), `how_to_run/` (install, runbook), `glossary/`, `research_and_reviews/`, this map | none |
| `data/` | the data root, git-ignored: `market/`, `spx/`, `sndk/`, `voice_desk/`, `screen_files/`, `operations/`, `archive/`, the layout `station_paths.py` draws and `docs/data_map.md` explains | none |

## How a job runs

Every plist's command is
`bash -lc "$HOME/mirai_awakening/scheduled_jobs/job_start_scripts/start_job_with_logs.sh <job> <run script> [args]"`.
The start script makes `data/operations/logs/`, sends what the job prints to `<job>_output_log.txt`
and `<job>_errors_log.txt` there (`<job>` is the launchd label without `com.mirai-station.`, hyphens
written as underscores) and becomes the run script. First it rotates the job's two logs: a log last
written on an earlier day goes to `data/operations/logs/older/<that day>/`, gzipped, and the rotated
days are kept 14 days. The run script sources `env.sh` (the repo root
from its own position, the venv at `~/.local/share/mirai-station/venv`, `MIRAI_STATION_STATE` as
`<repo>/data`), asks the market gate in `alerts_and_phone_pushes/` where one applies, then moves to
the working folder below and starts the entry with the venv's python. launchd expands nothing in a
plist path, which is why the plists cannot name the log files themselves.

| job (label without `com.mirai-station.`) | run script | working folder | entry |
|---|---|---|---|
| left-eye | run-watch-left-eye.sh | alerts_and_phone_pushes | `pipelines/spx_options_scanner/hunter.py`, then `python -m watch.cli gex-alerts` |
| sndk · sndk-read · sndk-bars | run-sndk.sh · run-sndk-read.sh · run-sndk-bars.sh | pipelines/sndk_options_scanner_and_reader | `sndk_hunter.py` · `sndk_read.py` · `sndk_bars.py` |
| sndk-deadman · spx-premarket-deadman · auth-watch | run-sndk-deadman.sh · run-spx-premarket-deadman.sh · run-auth-check.sh | alerts_and_phone_pushes | `python -m watch.intraday.sndk_deadman` · `.spx_premarket_deadman` · `.auth_check` |
| lob-collector · dated-book · gex-polarity | run-lob-collector.sh · run-dated-book.sh · run-gex-polarity.sh | pipelines/spx_options_scanner | `lob_bridge.py --run-collector` · `dated_gex_feed.py --pull` · `gex_polarity_ab.py` then `lob_bridge.py --nightly` |
| spx-jev · spx-jev-tape | run-spx-jev.sh · run-spx-jev.sh --lane tape | pipelines/spx_jev_questions_and_pools | `python -m spx_jev.service` |
| spx-jev-premarket | run-spx-jev-premarket.sh | pipelines/spx_jev_questions_and_pools | `python -m spx_jev.premarket` |
| spx-jev-bars · -context · -save-day · -overnight · -headlines · -store | run-spx-jev-*.sh | pipelines/spx_jev_questions_and_pools | `python -m spx_jev.bars` · `.market_context` · `.save_day` · `.overnight` · `.headlines` · `.store`, then `station_store_files.py --compress-finished-days` (gzips old day files) |
| spx-jev-prediction-nightly | run-spx-jev-prediction-nightly.sh | pipelines/spx_jev_questions_and_pools | `python -m spx_jev.mirai_prediction.nightly_job` |
| spx-claude-forecast-recorder · -nightly | run-spx-claude-forecast-*.sh | pipelines/spx_claude_forecast | `python -m spx_claude_forecast.recorder` · `.nightly` |
| viewstation | run-viewstation.sh | the server's own folder | `screens/desktop_and_phone_server/server.py` |
| voice | run-voice.sh | the desk's own folder | `screens/voice_desk/voice_server.py` |
| caffeinate | none | none | `/usr/bin/caffeinate -imsu` |

## How the code finds things

- `station_paths.py` is imported by name after each module puts the repo root on `sys.path` from
  its own position (`Path(__file__).resolve().parents[N]`); a package does it once.
- The `watch` package is imported with `alerts_and_phone_pushes/` on `sys.path`: the market gate
  the pipelines call, the dead-man's switches, the health check.
- Pipelines reach each other by folder, never by install: the scanner's bridges add the two
  collectors' folders, the SNDK scanner adds the SPX scanner's, the voice desk adds the SNDK
  scanner's, the JEV service adds the Claude forecast's, the Claude forecast adds the JEV service's.
- The venv stays at `~/.local/share/mirai-station/venv` (`venv-bootstrap.sh`), with an editable
  install of `data_collectors/spx_options_tape_collector` for the `lob_flow` package.
