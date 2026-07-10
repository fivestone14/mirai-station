---
name: iv-viability
description: Score an option contract's purchase or sale viability from IV rank, skew, term structure, OI/gamma, and vol risk premium — standalone Schwab-backed skill
argument-hint: <ticker> [strike] [expiry YYYY-MM-DD] [call|put]
allowed-tools: Bash, Read
---

# IV Viability — Options Contract Mispricing Scorer

Evaluates whether an options contract is viable to **buy** (cheap vol, favorable skew) or **sell** (rich vol, unfavorable skew) using live Schwab data. Fetches the chain + 1-year price history, computes IV rank, VRP, skew, term structure, OI/volume flow, and dealer gamma exposure, then emits a bulleted verdict.

Credentials live **only** in the macOS Keychain. The OAuth token is encrypted at rest with Fernet; the Fernet key also lives in Keychain. No plaintext secret ever touches disk.

## Instructions

1. **Before any tool calls**, print this exact line as the very first line of output, then a blank line:

```
IV Analysis skill in use.....
```

2. Parse `$ARGUMENTS`. First token is the ticker (required). Optional trailing tokens `<strike> <expiry> <call|put>` target a specific contract for contract-level metrics.
   - Uppercase the ticker.
   - If no ticker is provided, tell the user: `Usage: /iv-viability <ticker> [strike] [expiry YYYY-MM-DD] [call|put]` and stop.

3. **Check credentials are enrolled before running**:
```bash
python3 ~/.claude/skills/iv-viability/iv_fetcher.py --ticker <TICKER>
```
   - Pass `--strike`, `--expiry`, `--type` only if the user provided them.
   - If the script exits with `Credentials not enrolled`, instruct the user to run:
     ```
     python3 ~/.claude/skills/iv-viability/iv_fetcher.py --setup
     ```
   - The setup flow is interactive and requires: App Key, App Secret, and the Callback URL registered on the Schwab developer portal. The user's Schwab app must be in `Ready For Use` status — not `Approved - Pending`.

4. **Display the script's output verbatim** to the user. It already contains:
   - The announcement line
   - The bulleted metric list (Absolute IV / Term structure / Skew / Contract pricing / OI flow / Gamma / Timing)
   - The aggregate verdict (`BUY VOL`, `SELL VOL`, `MIXED`, or `INSUFFICIENT DATA`)
   - Any Pre-Trade IV Check gate flags (which override BUY verdicts only, never SELL)
   - The GOOGL Apr 9 origin callback when the gate fires

5. **After the verdict**, append **one single short sentence** summarizing the trader takeaway — no more, no less. Format: `<TICKER> → <one-line actionable read that names the dominant signal and the recommended action>`. Examples:
   - `AMZN → rich wing IV + bullish flow = fade the crash hedges via a 0.75Δ LEAP call.`
   - `GOOGL → cheap ATM but suppressive GEX = wait for flip break before long premium.`
   - `BE → pre-event backwardation = sit out the front month, let the crush play out.`

   Do NOT write multi-paragraph takeaways, "Two things worth noting" sections, or PMCC/cross-check recommendations. The slim output already carries the context the user needs; your job is a one-liner that names what matters and what to do.

6. **Stale data warning**: if the script output includes `(stale)` on the quote timestamp line, tell the user explicitly: "Quote is stale (>24h old or market closed) — treat this as a regime read, not an execution signal."

7. **If the script exits with an error**, surface the error message verbatim. Common errors:
   - `UNKNOWN TICKER OR NO LISTED OPTIONS` — ticker is invalid or has no options listed
   - `contract not found` — the requested strike/expiry doesn't exist; the script prints nearest strikes/available expiries
   - `Refresh token expired` — instruct the user to re-run `--setup`
   - `ERROR: Schwab fetch failed` — usually a 429 rate limit or 401; retry in a minute

## Subcommands

- `setup` → run `python3 iv_fetcher.py --setup` (interactive enrollment)
- `rotate-key` → run `python3 iv_fetcher.py --rotate-key` (re-encrypt token)
- `wipe` → run `python3 iv_fetcher.py --wipe-credentials` (delete all credentials)
- `reset-cache` → run `python3 iv_fetcher.py --reset-cache` (wipe IV history)

## Alignment with CLAUDE.md Pre-Trade IV Check

The skill reads the four rules from `CLAUDE.md` at runtime and applies each as a **BUY-only override** on the aggregate verdict:

1. **Time of day**: first 30-60 min of session → flag morning IV premium
2. **IV level**: elevated IV rank → flag vega tax
3. **Catalyst proximity**: event inside the contract's life → flag pre-crush
4. **Vega direction**: position increases vega into elevated IV → flag

A rich-IV SELL verdict (e.g., jade lizard into earnings) is never gated — that's the entire playbook.

## Credential Security Summary

- `api_key`, `app_secret`, `callback_url`, `fernet_key` → macOS Keychain, service `iv-viability-schwab`, local only (no iCloud sync)
- OAuth token → Fernet-encrypted blob at `~/.claude/skills/iv-viability/.schwab_token.json.enc`, chmod 600
- No secrets in env vars, config files, shell history, or logs
- Tracebacks and logs scrubbed via redaction filter
- Core dumps disabled via `RLIMIT_CORE=0`

To inspect Keychain entries without printing values:
```bash
security find-generic-password -s iv-viability-schwab -a api_key
```
