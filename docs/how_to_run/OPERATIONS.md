# mirai-station — Operations runbook

> Architecture: see the top-level `README.md` and `docs/glossary/gex-glossary.md`. Live
> shape: hunter (Shift Manager) → Flow Sensor + Gravity Engine → three heads
> (Fade Lens, Watchtower, Break Lens) → diary → nightly report cards → viewstation + ntfy.

## Start / stop / restart

```bash
# Stop a single agent
launchctl bootout gui/$UID/com.mirai-station.left-eye

# Start it again
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.mirai-station.left-eye.plist

# Restart the whole fleet
~/mirai_awakening/scheduled_jobs/job_start_scripts/uninstall-launchd.sh
~/mirai_awakening/scheduled_jobs/job_start_scripts/install-launchd.sh
```

## Logs

| What | Where |
|---|---|
| Each job's stdout/stderr | `~/mirai_awakening/data/operations/logs/<job>_output_log.txt` and `<job>_errors_log.txt`, the job named as in its launchd label with hyphens as underscores (`left_eye`, `spx_jev_tape`); `start_job_with_logs.sh` appends on every run, and nothing rotates them, so they grow until deleted by hand |
| Runtime/env messages   | `~/mirai_awakening/data/operations/logs/station_runtime_log/YYYY-MM-DD.log` |
| Hunter's own jsonl     | `~/mirai_awakening/data/spx/scanner/spx_options_scanner_signal_checks_by_scan/YYYY-MM-DD.jsonl` |
| SPX JEV decision service, eleven jobs (`spx-jev`: the same beside the SPX diary at :02 and :32; `spx-jev-tape`, the opening lane; its feeds `spx-jev-bars`, `spx-jev-context`, `spx-jev-save-day`, `spx-jev-overnight` and `spx-jev-headlines`; `spx-jev-premarket`, the lane before the open; `spx-jev-store`, `spx-jev-prediction-nightly` and `spx-premarket-deadman`, below). `launchctl list \| grep -E 'spx-jev\|spx-premarket'` shows all eleven | `data/operations/logs/spx_jev*_{output,errors}_log.txt` and `spx_premarket_deadman_{output,errors}_log.txt`; its records under `~/mirai_awakening/data/spx/jev/` (the three lanes' folders, the shared archive and the tables), its learning loops under `data/spx/prediction/base_pool/`, its feeds under `data/market/` and the phone's cards under `data/screen_files/phone/` |
| SPX JEV overnight futures (`spx-jev-overnight`: /ES, /ZN, /BTC and /MBT bars from the prior close, saved at 09:26 and 16:20 ET on market days, with a manifest line of checks for each save that changed a night, and the roll table) | `data/operations/logs/spx_jev_overnight_{output,errors}_log.txt`; `data/market/futures_price_bars_by_minute/{date}.jsonl`, `futures_price_bar_change_counts_by_save.jsonl`, `futures_contracts_and_past_rolls_latest.json` |
| SPX JEV premarket lane (`spx-jev-premarket`: the night's labels at 02:35, 03:35 (04:35 in Frankfurt's winter-time week), 08:05, 08:48, 09:05 and 09:28 ET, JEV asked at 08:48 and 09:28, the close-out at 10:06 grading from the settled open) | `data/operations/logs/spx_jev_premarket_{output,errors}_log.txt`; `data/spx/jev/premarket_lane/` |
| SPX JEV learning store (`spx-jev-store`: the last week's market days rebuilt from the raw files at 16:40 ET into checked Parquet, one line per day with its row counts and anything quarantined) | `data/operations/logs/spx_jev_store_{output,errors}_log.txt`; `data/spx/jev/tables/` and `data/market/tables/`, the counts and the refused rows under `data/operations/health/` |
| SPX JEV premarket dead-man (`spx-premarket-deadman`: every 5 minutes on a market day, pages the phone once for a checkpoint with no read 15 minutes after it, and once for a 10:06 close-out that has not landed by 10:21 ET or landed with a pre-market call still ungraded, which a later live read tries to grade again) | `data/operations/logs/spx_premarket_deadman_{output,errors}_log.txt`; `data/operations/jev_premarket_outage_alert_state.json` |

Quick health check:
```bash
tail -20 ~/mirai_awakening/data/operations/logs/left_eye_errors_log.txt
```

## Force-run on demand

```bash
~/mirai_awakening/scheduled_jobs/job_start_scripts/run-watch-left-eye.sh
```

This bypasses launchd entirely; useful for debugging. `mirai-watch tick` runs the same script, and its `--dry-run` flag is accepted and ignored: that is a real tick too, phone pushes included.

The overnight futures by hand, the way the job saves them (`--day YYYY-MM-DD` saves one night, named for the day it leads into; `--backfill` takes every night Schwab still serves and re-detects the rolls; a rerun adds only what is not on disk). A save that changed a night writes its checks as a line in `data/market/futures_price_bar_change_counts_by_save.jsonl`, so the newest line for a day is the last save that added to it (a rerun that added nothing writes none):

```bash
cd ~/mirai_awakening/pipelines/spx_jev_questions_and_pools
~/.local/share/mirai-station/venv/bin/python -m spx_jev.overnight --state-dir ~/mirai_awakening/state
```

## ntfy alert channel

ntfy has no API key, but the **topic name is the credential** — the channel is
unauthenticated, so anyone holding it can read every alert and publish fakes. It
lives in the Keychain under service `mirai-station-ntfy`, and
`scheduled_jobs/job_start_scripts/env.sh` exports it as `MIRAI_NTFY_TOPIC` (which beats the config
file, whose `topic` key stays empty because that file is tracked).

Rotate it — after any suspected exposure, and note that a topic that ever reached
the repo is exposed permanently, since git history keeps it:

```bash
# 1. new topic, generated straight into the Keychain (never printed, never in a file)
security add-generic-password -U -a "$USER" -s "mirai-station-ntfy" \
  -w "mirai-$(openssl rand -hex 16)"

# 2. read it once to re-subscribe the phone app
security find-generic-password -a "$USER" -s "mirai-station-ntfy" -w

# 3. the agents re-read env.sh on their next run — no restart needed, but to
#    prove it end to end:
./scheduled_jobs/job_start_scripts/run-auth-check.sh
```

Until the phone is re-subscribed it receives nothing; the old topic keeps working
for whoever else knows it, which is exactly why rotation is the fix rather than
editing history.

## Market-data credential rotation

```bash
# Schwab (the live quotes and minute bars, SPX and SNDK alike). The login lasts
# 7 days; the auth-check job pings the phone as it nears the end. To sign in
# again, at the mini:
~/.local/share/mirai-station/venv/bin/python ~/mirai_awakening/data_collectors/schwab_market_data_client/iv_fetcher.py --reauth
# Press Enter to open the browser. Schwab redirects to https://127.0.0.1:8182,
# which answers with a self-signed certificate: pass the browser's warning, sign
# in and approve. "Re-auth complete. New 7-day refresh token stored." means it
# took; nothing needs a restart. Confirm (prints the login's age and days left):
~/mirai_awakening/scheduled_jobs/job_start_scripts/run-auth-check.sh
# (The check this replaced read a Keychain item, mirai-station/schwab-token-path,
# that no code writes.)

# ThetaData / Cassandra's Edge (the native SPX chain AND the SNDK chain).
# There is nothing to paste: the endpoint moved to AuthKit OAuth on 2026-09-09
# and its ACCESS tokens live 300 seconds, so a static bearer is dead five
# minutes after it is minted. Enrol once, interactively, at a browser:
python3 pipelines/spx_options_scanner/native_gex_feed.py --login           # at the mini
python3 pipelines/spx_options_scanner/native_gex_feed.py --login --manual  # over SSH
python3 pipelines/spx_options_scanner/native_gex_feed.py --status          # confirm
# The refresh token goes to the Keychain and every scan spends it for a short
# access token. No restart needed — the vault reads Keychain on every call.
#
# --login opens a browser and catches the redirect on 127.0.0.1:8765.
# --login --manual prints the URL instead and takes the redirected address back
# by paste, for when the browser is on a different machine than the station.
```

> [!warning] The old line here was wrong twice over
> It said `security add-generic-password -a "$USER" -s "iv-viability-cassandra"`,
> but the vault reads the account **`cassandra_edge_token`**, not `$USER` — a
> hand re-key wrote to an item nothing ever read. And since 2026-09-09 no pasted
> bearer survives five minutes anyway. Use `--login`.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `launchctl list` shows `Status: 78` for an agent | exit code != 0; check stderr | `tail ~/mirai_awakening/data/operations/logs/<job>_errors_log.txt` |
| a job's `<job>_errors_log.txt` has a line ending its market check with `FAILED (rc=N)` and the job exits 1 | the market-hours gate itself failed (venv, import). The gate exits 0 when the market is open and 3 when it is closed, and only 3 skips quietly. The gates of spx-jev and spx-jev-bars also exit 4 in the 13 minutes after the close, which runs the job once more: the day's last bars, and the live read's grade-only close-out | re-run `venv-bootstrap.sh`. Gated this way, by runner (`run-<name>.sh`): sndk, sndk-read, sndk-bars, lob-collector, spx-jev (the spx-jev and spx-jev-tape jobs), spx-jev-bars, spx-jev-context, watch-left-eye (the left-eye job, which still runs its alert pass before exiting 1) |
| Phone: "SNDK reader silent" | the reader stopped writing read rows while the scanner still writes; a silent scanner is paged under its own name instead | `tail ~/mirai_awakening/data/operations/logs/sndk_read_errors_log.txt`; check `SNDK_READ_DISABLE`. "SNDK reader back" follows once a read row lands |
| "schwab module not found" | venv not provisioned or wrong python | re-run `venv-bootstrap.sh`; confirm shebang resolves |
| GEX read falls back to SPY-proxy every scan | expired Cassandra/ThetaData login | `native_gex_feed.py --login` (see above); auth-watch pings on this |
| SNDK scanner silent + SPY-proxy at the same moment | one credential, both casualties — it is never two faults | `native_gex_feed.py --status`, then `--login` |
| `--login` says the server issued no refresh token | `offline_access` scope refused | check the account's permitted scopes with the provider |
| `--login` cannot reach the browser (headless/SSH) | nothing can connect to 127.0.0.1:8765 | `--login --manual` and paste the redirected URL back |
| `--login` refused at registration (422) | the provider changed what it accepts | the error quotes the field it disliked; fix `_register`'s body in `cassandra_oauth.py` |
| No ntfy push on a fire | topic unset or phone not subscribed | check the `ntfy` block in `limits-and-cooldowns.json`; re-subscribe the app |
| Mac mini sleeping | caffeinate plist not loaded | re-run `install-launchd.sh` |

## What the mini knows vs. doesn't

The mini operates with:
- The gex-only brain in `pipelines/spx_options_scanner/` (Gravity Engine + three heads)
- Schwab live chain + the ThetaData native SPX chain via the Python venv

The mini does NOT have:
- The interactive investment vault (intentional — stays on the main machine)
- Any real-money order path — everything here is paper/shadow until the Wilson
  promotion gate clears

If a boot step flags a missing vault file, that is expected and not an error.

## Updating the plugin

Edit on main machine, then:
```bash
rsync -avh --exclude '__pycache__' --exclude 'data/operations/logs/' \
  ~/mirai_awakening/ \
  <mini-host>:~/mirai_awakening/
```

The launchd jobs pick up script changes on next fire (no restart needed). plist changes require:
```bash
~/mirai_awakening/scheduled_jobs/job_start_scripts/install-launchd.sh
```

## Disabling temporarily

`launchctl disable` keeps a job off past a reboot or a login; `bootout` stops it now. A job
can also be left loaded and switched off in its environment (`SPX_JEV_DISABLE=1` and
the others in the README's kill-switch table), which makes its runner exit 0.

```bash
# One job
launchctl disable gui/$UID/com.mirai-station.left-eye
launchctl bootout gui/$UID/com.mirai-station.left-eye

# The whole station: every job install-launchd.sh loads (its PLISTS list is the fleet).
# Drop caffeinate from the list to keep the mini awake and reachable while paused.
JOBS="left-eye caffeinate auth-watch gex-polarity viewstation lob-collector dated-book
      sndk sndk-read sndk-deadman sndk-bars
      spx-jev spx-jev-tape spx-jev-premarket spx-premarket-deadman spx-jev-bars spx-jev-context
      spx-jev-save-day spx-jev-overnight spx-jev-store spx-jev-prediction-nightly spx-jev-headlines
      spx-claude-forecast-recorder spx-claude-forecast-nightly voice"
for j in $JOBS; do
  launchctl disable gui/$UID/com.mirai-station.$j
  launchctl bootout gui/$UID/com.mirai-station.$j
done
```

Re-enable:
```bash
for j in $JOBS; do
  launchctl enable gui/$UID/com.mirai-station.$j
  launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.mirai-station.$j.plist
done
```
