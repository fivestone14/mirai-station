# JEV research brief and v0.2 architecture for short-horizon options and stock trading

Date: 2026-09-18. Status: draft v0.2 for discussion. Paper decisions only, no broker connection. This is system design, not investment advice.

Update, later on 2026-09-18: the goal was clarified as move direction at a given moment for SPX inside Mirai, not stock selection. Sections 1 to 4 and 12 below still hold as JEV research. The trading modules in sections 5 to 11, such as news triage, hold checks and the pre-trade plan grader, are superseded by `jev-overlay.json`, which is built on Mirai's real data, units and grading rule.

Changes from v0.1: the trading style is now known (options and stock, intraday to multi-day) and you have JEV access. The thesis-pillar and fundamentals modules are gone. New in this version are news triage, setup labelling, options context, risk flags, hold checks, a pre-trade plan grader, code baselines, a latency and cost budget, and trade-style outcome labels.

## 1. Summary

JEV is a new AI model from TypeSafe AI, in early access since 2026-09-15. TypeSafe writes the name "Jev". It does not write text. You give it a block of information, called the state, and a list of typed questions. It returns a probability for every answer in one fast pass, about 100 ms per request at $0.042 per million input tokens.

Four things follow from your trading style.

- Short-horizon trading runs on numbers, and JEV cannot do math. Code turns every price, volume and options figure into a plain-English label before JEV sees it.
- JEV's best fit is the text side of trading. That means headlines, filings, call transcripts and your own written trade plans.
- Every JEV question that has a numeric or keyword twin in code must beat that code rule on forward data, or it gets dropped.
- Speed and cost are not the problem. Data is an advantage. Intraday trading produces hundreds of decisions in weeks, and everything that arrives after you pin a JEV version is newer than its training data.

Nothing published shows JEV has an edge in trading. Every headline number comes from TypeSafe itself or from small outside tests on non-financial tasks. The architecture measures JEV before it trusts JEV.

## 2. What JEV is

| Item | Detail |
|---|---|
| Maker | TypeSafe AI, founded 2024. CEO Diogo Almeida, ex-OpenAI, co-author of the InstructGPT paper. $40M seed round. |
| Availability | Hosted API only. Early access behind a waitlist since 2026-09-15. |
| Core idea | A "System One" model. It makes fast, gut-level decisions in one pass, the opposite of "System Two" models that think in long chains of text. |
| How it answers | No token-by-token writing. A parallel sampler fills in every answer at once. Every question in a request is scored independently and in parallel, so extra questions add almost no time. |
| Input | Text only. A string, a JSON object, or an array. 64,000 tokens shared between state and questions. The state plus the longest single question must fit in about 32,000 tokens. |
| Question type: Noul | Yes/no. Returns one probability from 0 to 1. |
| Question type: Choice | Pick one option you define, up to 255. Returns the pick, a probability per option, and a confidence. |
| Question type: Score | Rate on ordered levels you define. Returns a probability-weighted score, the distribution over levels, and a confidence. |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` with a bearer key and model `jev-latest`. The response echoes the exact version used, for example `jev-1.13.0`. Python and JavaScript SDKs exist, plus a LangChain integration. |
| Speed and cost | 70 to 500 ms end to end, about 100 ms typical. Output tokens are free. A 300-token request costs about $0.0000126. TypeSafe says the price may be subsidized. |
| Rate limits at launch | 250,000 tokens per second and 1,200 requests per minute. |
| Training | "Reinforcement Learning for Calibrated Decisions", or RLCD. The goal is that answers given 90% probability come true about 90% of the time. The reward function, architecture and calibration method are not published. |
| Cannot do | Write text or code. Explain a decision. Do arithmetic, count, or compare dates. Read images or audio. Abstain, unless you add an "unknown" option. |
| Known weaknesses | Reads instructions literally. Accuracy falls when the state holds irrelevant material, which TypeSafe calls context rot. |
| Knowledge cutoff | Not disclosed in anything I found. |

## 3. What is proven and what is not

Claims from TypeSafe:

- 40 to 200 times faster and up to 444 times cheaper than frontier models. These come from TypeSafe's own tests. Outside tests confirm the direction but show smaller gaps. One 50-decision test found Jev about 5 times faster and 8.6 times cheaper than Mistral Small 4.
- Quality on par with GPT-5.6 Terra. TypeSafe's table shows Jev at 67.8% against Terra at 67.9%, GPT-5.6 Sol at 74.1% and Claude Opus 5 at 73.1%. Here "accuracy" means agreement with the average of GPT-6 Astra and Claude Fable 5.1, not correctness against a known truth. TypeSafe's own team built the test workflows.
- "0% hallucination." TypeSafe's own note says this number "is not empirical". It means JEV cannot return a malformed answer. JEV can still pick the wrong option from a valid list.

Outside tests I found:

- Near Here, 50 listing-moderation decisions: Jev 96%, Mistral Small 4 84%, Gemini 3.5 Flash-Lite 86%.
- Good Start Labs, 6,003 rubric checks: Jev matched Claude Fable 5.1 91.5% of the time and DeepSeek V4.1 Flash 93.5%. This measures agreement, not correctness.
- Every, planted defects in passages: Jev caught 6 of 7. Fable 5.1 caught all 7.
- Trading: two community repos test Jev as a trader. trade-jev uses Nasdaq-100 futures order-book data from 15 days in June 2026. jev-trader trades a crypto pair on the Monad chain. I found no published performance results for either.

Calibration has not been independently tested. That is the biggest gap for us, because this design turns JEV's probabilities into alerts.

## 4. Constraints that shape the design

1. Numbers stay in code. JEV is unreliable at math, counting and date logic. Code computes every ratio, move and comparison, then hands JEV a label such as "down 4.2% in 3 minutes, volume 5 times normal".
2. JEV has no clock. Timestamps in the state are just text. Code computes elapsed time and passes it as a label.
3. One judgment per question. Questions that hide several steps lose accuracy. To count items, ask one yes/no question per item and count in code.
4. Every Choice gets an escape option such as `unclear`. Without one, JEV picks the least wrong answer instead of saying it does not know.
5. Questions are independent. JEV does not see its own other answers, so chaining and combining happens in code.
6. Retrieve first, then judge. Send only the text a question needs. Long states hurt accuracy.
7. Headlines, filings and social posts are untrusted input. Text inside them can argue for its own classification, and pump-and-dump writers will try. Treat all of it as hostile.
8. No rationale. The audit trail is the logged input, the output and the model version. Reasons come from Claude after the fact.
9. Pin the model version once thresholds are tuned. `jev-latest` can change under us.
10. Calibration describes groups of answers, not any single answer. Several calibrated answers combined with weights and thresholds are not automatically a calibrated decision. We measure the whole pipeline.
11. The training cutoff is unknown. Historical replays can leak the future into JEV's answers. Data that arrives after you pin a version is clean for that version.
12. TypeSafe is a young company with subsidized pricing. Keep JEV behind a swappable interface.

## 5. Where JEV fits in your trading

Good fit, because the input is text and the answer is a bounded judgment:

- Headline and filing triage on every name in the universe. Is it relevant, what kind of event, which direction, how material, is it new.
- Red flags in text. Offering or dilution, halt, short-seller report, going-concern language, regulatory action, delisting notice, pending acquisition at a fixed price.
- Multi-day hold checks. Has news since entry invalidated the reason for the trade.
- Pre-trade plan grading. Does your written plan name an entry trigger, an invalidation level, an exit rule and a reason beyond "it went up".
- Post-trade tagging for the ledger.

Unproven, because the input is numbers turned into labels and code may do as well:

- Day type and setup labels such as trend day, range day, gap-and-go and failed breakout.
- Reaction checks. Does the price move after a headline look under-done, fair or over-done.

Not a fit:

- Prices, Greeks, implied volatility math, strike and expiry selection, position size and stops. Code owns these.
- Speed races. 100 ms is fast for an AI model and slow for a trading system, so alerts are for a human, not for beating the market to a fill.
- Explaining itself. Claude does that afterward.

## 6. Architecture v0.2

```
1 UNIVERSE AND RISK LIMITS   config, code only
   watchlist, max loss per trade, daily loss stop, no-trade windows

triggers                   3 STATE BUILDER          5 JUDGE            6 POLICY              7 ALERT
new headline ──┐           code only                JEV                code only             paper recommendation
scanner hit ───┼─► 2 CANDIDATE ─► numbers to labels ─► one request per ─► hard gates first ─► structure, size,
options flow ──┤             point-in-time stamps      candidate,         view + confidence     why, gates fired,
schedule tick ─┘             text retrieval            6 to 15 questions  structure selector    model version
                                                      (4 QUESTION BANK)  sizing from limits           │
                                                                                                      ▼
                        8 LEDGER: every state, answer, version, alert ──► outcome labels ──► calibration, baselines, drift

Claude runs asynchronously on high-materiality cases. It never blocks an intraday alert.
```

### 1. Universe and risk limits

The watchlist, plus account limits. Max loss per trade, daily loss stop, max open positions, no-trade windows such as the first minutes after the open and scheduled macro releases, and whether options must be defined-risk. All of it is config, all of it is code, and JEV never touches it.

### 2. Triggers and candidates

Event-driven where possible. A new headline or filing for a watched name, a scanner hit such as a gap, relative volume or a range break, unusual options volume, a position crossing a review level, and a slow scheduled sweep every 1 to 5 minutes as a backstop. Each trigger creates a candidate record.

### 3. State builder, code only

For each candidate, fetch price and volume context, an options chain summary, recent headlines and the event calendar. Compute every number in code and write it as a plain-English label, keeping the raw number in the ledger. Example label: "price 0.8% above VWAP, holding above the prior day high, volume 2.1 times normal for this time of day". Stamp each item with the moment it became public. Retrieve only the text relevant to the candidate. Keep states between 500 and 3,000 tokens.

### 4. Question bank

Versioned files. Each question has an id, type, instructions, criteria, module and version. Phrasing rules. Write questions positively, because JEV reads negations literally. Make high values mean yes. Anchor Score levels in observable facts. Give every Choice an escape option. Ask one judgment per question.

### 5. Judge interface

One thin wrapper, `Judge.ask(state, questions) -> answers`. JEV is one implementation. Claude with structured output is a second, used to cross-check JEV, as a fallback, and for cases that need written reasons. Both read the same question bank.

Critical questions run three to five times in parallel with the state reordered or the instructions reworded, and we require agreement. Parallel runs add no latency. The community repo trade-jev used a 0.7 confidence cutoff and four agreeing answers as its default. I did not see results for it. Agreement across runs measures noise, not correctness, and errors that come from the same source text repeat in every run.

### 6. Decision policy, code only

Ordered gates, all deterministic and unit-testable. See section 8.

### 7. Alert

A paper recommendation sent to you. No broker connection in v0.

### 8. Ledger and calibration

Log a hash of the state, the full state with raw numbers, all questions and answers, the model version, the timestamp and the resulting alert. Later, attach outcome labels. Produce a reliability chart per question, baseline comparisons, drift alerts, and side-by-side runs when a new JEV version ships.

### Latency and cost budget

| Step | Budget |
|---|---|
| Trigger to candidate, code | under 50 ms |
| State build, code | under 300 ms, depends on your data feed |
| JEV | 70 to 500 ms |
| Policy, code | under 50 ms |
| Trigger to alert | about 1 second |

At $0.042 per million input tokens, a 2,000-token request costs about $0.00008. Sweeping 100 names every 30 seconds for a 6.5-hour session is 78,000 requests and about $6.50 a day. The launch rate limit of 1,200 requests per minute allows about 20 per second. Subsidized pricing may not last, so treat these as launch numbers.

If JEV or the data feed goes down, the system fails closed. It stops issuing new-entry alerts and keeps position review alerts running through the Claude fallback.

## 7. Question bank v0.2

| Module | Job | Types | Example questions |
|---|---|---|---|
| N. News and catalyst triage | JEV's best fit. Run on every headline and filing. | Noul, Choice, Score | Is the headline about this company itself. Event type: earnings, guidance, deal, analyst action, regulatory, product, legal, macro, other, unclear. Direction on five levels from "clearly negative" to "clearly positive". Materiality from noise to price-moving. Is it new versus the recent headlines. |
| R. Risk flags | Text-based vetoes. One question per flag. | Noul | Offering or dilution announced. Trading halt or halt risk. Short-seller report. Going-concern or non-reliance language. Regulatory or legal action. Delisting notice. Pending acquisition at a fixed price. |
| H. Hold check | Multi-day positions. The trade record holds the reason and the invalidation in one or two sentences each. | Noul, Choice | Has anything since entry invalidated the reason for the trade. Status: intact, weakened, invalidated, insufficient_evidence. |
| P. Pre-trade plan grader | Rubric check on your written plan. | Noul, one per rubric item | Entry trigger stated. Invalidation level stated. Exit rule or target stated. Reason goes beyond "price moved". Catalyst inside the holding window acknowledged. Holding period stated. |
| O. Options context | Explains what code measured. | Noul, Choice | Do the recent headlines plausibly explain the jump in implied volatility. Is the elevated implied volatility explained by an event on the calendar. |
| S. Setup labelling | Names a pattern from a labelled state. Experimental. | Choice, Noul | Day type: trend, range, gap-and-go, gap-fade, unclear. Shadow only until it beats a code rule. |
| X. Reaction check | Compares a headline with the move since. Experimental. | Choice | Given the labelled move: under-reacted, fairly reacted, over-reacted, cannot_tell. Shadow only until it beats a code rule. |
| J. Post-trade tagging | Feeds the ledger. Later phase. | Choice | Followed plan, chased entry, ignored invalidation, exited early, sized too large, none. |

Code only, no JEV: prices, VWAP, ATR, relative volume, implied volatility rank, Greeks, skew, term structure, dealer positioning maps, liquidity and spread checks, days to earnings and expiry, ex-dividend and early-assignment risk on short calls, sizing, stops and portfolio limits.

Example request for a headline. Numbers arrive pre-computed and pre-labelled.

```json
{
  "model": "jev-latest",
  "state": {
    "ticker": "EXAMPLE",
    "headline": "EXAMPLE lowers full-year revenue outlook, cites weaker enterprise demand",
    "source": "newswire",
    "recent_headlines": ["…last five headlines for this ticker, oldest first…"],
    "computed_in_code": {
      "move_since_headline": "down 4.2% in 3 minutes",
      "volume_now": "5 times normal for this time of day",
      "gap_context": "opened flat, no pre-market news"
    }
  },
  "questions": {
    "relevant": {
      "type": "noul",
      "instructions": "Is `headline` about EXAMPLE's own business?"
    },
    "event_type": {
      "type": "choice",
      "instructions": "What kind of event does `headline` report?",
      "criteria": {
        "earnings": "Reported quarterly results",
        "guidance": "A change to the company's own outlook",
        "deal": "Merger, acquisition, partnership or contract",
        "analyst": "A rating or price target change",
        "regulatory_or_legal": "Regulator, court or investigation",
        "other": "A company event that fits none of the above",
        "unclear": "The headline does not say"
      }
    },
    "direction": {
      "type": "score",
      "instructions": "What does `headline` mean for the stock price, judged on the headline alone?",
      "criteria": ["Clearly negative", "Somewhat negative", "Neutral or unclear", "Somewhat positive", "Clearly positive"]
    },
    "is_new": {
      "type": "noul",
      "instructions": "Does `headline` contain information that is missing from `recent_headlines`?"
    }
  }
}
```

## 8. Decision policy sketch

Outputs are paper alerts. Each has a direction, a horizon bucket of intraday, 1 to 3 days or longer, a structure and a size. The structure rules are yours to set. The policy only maps view strength, horizon, volatility context and liquidity to one of your allowed structures. Gates run in this order.

1. Hard gates in code. Liquidity, spread width, halts, banned windows, event inside the holding window, risk budget left, early-assignment risk. Any failure blocks the alert and logs the reason.
2. Text veto gate. If any risk-flag Noul is above its threshold, block new positions and send a review alert for existing ones. Start at 0.7 as a placeholder.
3. View. Combine direction, materiality and novelty from module N into one signed number. Add S and X only if they have earned a place. We set the weights. JEV never does.
4. Confidence gate, set by the cost of being wrong. Watch alerts get a low bar. New entries get a high bar plus agreement across parallel runs. Holding overnight gets a higher bar than staying flat. An exit on an invalidated hold check gets a medium bar, because a wrong exit costs less than a wrong hold.
5. Structure and size, in code. Template from the view, horizon, implied volatility context and liquidity. Size from the risk budget.
6. Alert record. Action, gates that fired, answers that drove it, raw numbers, model version, and a short human-readable summary.

Every threshold is a placeholder. We tune them only on forward data.

## 9. Validation plan

Baselines first. For every JEV question that has a code twin, build the code version before trusting JEV. News event type has a keyword and filing-item baseline. Day type has a rule-based baseline. Then compare three ways on the same forward data.

- B0, the code-only rule.
- B1, a small cheap LLM on the same question bank, such as Claude Haiku 4.5.
- B2, JEV.

JEV keeps a question only if it beats B0 on forward outcomes and is the practical choice against B1 on speed and cost.

Outcome labels:

- Stock, intraday. Return over the next 5, 15 and 60 minutes and to the close, adjusted for the market and sector move. Also a trade label, target hit before stop before the time limit.
- Stock, multi-day. Return over 1, 3 and 5 days, plus the best and worst point during the hold.
- Options. Mark at conservative fills, with slippage set to a share of the bid-ask spread plus commissions. Wide option spreads eat small edges, and a backtest at mid prices will flatter everything.
- News. Abnormal return after the headline, adjusted for the market and sector.

| Stage | Test | What it tells us |
|---|---|---|
| 0. Probes, first day | See the list below. | How noisy JEV is, how it handles labelled numbers, and whether it can be fooled. |
| 1. Reading | Build labelled test sets. SEC 8-K item numbers give free event-type labels. Reported earnings against consensus gives beat or miss labels. Compare B0, B1 and B2. | Whether JEV understands financial text better than a keyword rule or a cheap LLM. |
| 2. Forward calibration | Log every answer and its outcome from day one. Per question, plot claimed probability against actual frequency and compute the Brier score. Count independent events, not rows. Overlapping windows and several headlines on one event count once. | Whether its probabilities mean what they say on your markets. Plan for a few hundred independent events per question before trusting a reliability chart. |
| 3. Freeze and shadow | Freeze the question bank and thresholds, then run at least four weeks of paper alerts with no changes. | Honest out-of-sample results. Tuning on the data you score inflates results, and with dozens of questions and thresholds that is the main way this project could fool itself. |
| 4. Assist mode | Live alerts, you decide, tiny size, daily loss stop and a kill switch. | Real-world behavior at low risk. |

Stage 0 probes, using your access:

1. Send one request 20 times. Record the spread of each probability and the latency percentiles from your machine.
2. Send the same market state as labelled text and as raw numbers. See whether answers change sensibly.
3. Pad a state to 500, 3,000 and 20,000 tokens with irrelevant filler. Watch for accuracy loss.
4. Insert a fake headline that argues for its own classification. See whether the answer moves.
5. Ask the same Choice with and without an escape option.
6. Log the `model` field across a full day to see whether the version changes.

Metrics: Brier score, reliability chart per question, agreement with baselines, run-to-run variance, escalation rate, cost per decision, and whole-pipeline expectancy per trade after slippage and commissions, with maximum drawdown.

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Calibration is untested and vendor-reported. | Stages 1 to 3 before any influence on decisions. Recalibrate per question. |
| Testing many questions and thresholds produces false winners. | Freeze the bank before Stage 3. Count independent events. Keep a final untouched period. |
| Unknown cutoff leaks the future into replays. | Forward data is the real test. Historical replays only for debugging. |
| `jev-latest` changes and silently shifts probabilities. | Pin the version, log it on every call, shadow-run new versions before switching. |
| Pump-and-dump or persuasive text in headlines and posts. | Treat state as hostile. Keep questions narrow. JEV output alone never triggers an alert. |
| JEV reads a labelled number wrong. | Numbers never go to JEV as things to compare. Code pre-computes and labels them. Stage 0 probe 2 checks this. |
| Option spreads and slippage erase a small edge. | Score at conservative fills. Liquidity gates in code. |
| Stale state from a lagging data feed. | Timestamp everything. Block alerts when the newest input is older than a limit. |
| Correlated errors across questions that read the same text. | Measure at the pipeline level. Claude as an independent second judge on critical cases. |
| No rationale, so wrong answers are hard to debug. | Log full inputs. Replay through Claude for reasons. |
| Vendor risk from a young company and subsidized price. | Judge interface with a Claude implementation ready. Fail closed on outages. |

## 11. Assumptions and decisions needed

Assumptions I made so the draft could move:

- US-listed stocks and options.
- Paper alerts only, you place any trade yourself.
- JEV is the fast judge, Claude is the slow second opinion.
- Mirai-Awakening, going by its README, already reads the options market during the trading day, keeps a diary and nightly report cards, and runs a shadow-only second opinion called the Watchtower. I have not read its code. If it fits, JEV could plug in as another shadow judge beside the Watchtower and reuse its data and ledger, instead of building a second system.

Decisions for you:

1. Which understanding and breakdown questions do you already have in mind? We slot them into the modules above.
2. Where do prices, option chains and news come from today, and does Mirai-Awakening already supply them?
3. Should this plug into Mirai-Awakening or stand alone?
4. Which option structures do you trade, and must they be defined-risk?
5. What are your per-trade and daily loss limits? They go in config, and I only need the shape, not your numbers, until we build.

## 12. Sources

- [TypeSafe: Introducing System One models and Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [TypeSafe docs: System One concepts](https://docs.typesafe.ai/concepts/system-one)
- [TypeSafe docs: API reference](https://docs.typesafe.ai/api.md)
- [heise online: AI model Jev to make machines decide faster](https://www.heise.de/en/news/AI-model-Jev-to-make-machines-decide-faster-11457071.html)
- [The Register: TypeSafe AI debuts model for machines that plays Doom](https://www.theregister.com/ai-and-ml/2026/09/16/typesafe-ai-debuts-model-for-machines-that-plays-doom/5296711)
- [DataCamp: Jev, TypeSafe's System One model](https://www.datacamp.com/blog/system-one-models-jev)
- [MindStudio: Jev explained](https://www.mindstudio.ai/blog/jev-system-one-model-launch)
- [LangChain: What is Jev](https://www.langchain.com/blog/building-a-harness-with-jev)
- [Flavio Copes: A deep dive into Jev](https://flaviocopes.com/jev/)
- [DEV Community, Valyu: How to use Jev](https://dev.to/valyuai/how-to-use-jev-a-practical-guide-to-typesafes-system-one-model-g5e)
- [Sean Goedecke: Jev means structured output is interesting again](https://www.seangoedecke.com/jev-means-structured-output-is-interesting-again/)
- [TrueFoundry: What System One models actually are](https://www.truefoundry.com/blog/typesafe-ai-jev)
- [Langfuse: Using Jev for evals](https://langfuse.com/blog/2026-09-18-using-typesafes-jev-for-evals)
- [Anthony Maio: Jev, the language model that won't talk](https://anthonymaio.substack.com/p/jev-the-language-model-that-wont)
- [Cherry Creek News: independent tests of Jev's claims](https://thecherrycreeknews.com/typesafe-jev-system-one-model-claims-evals-independent-tests-cherry_creek/)
- [GIGAZINE: Jev](https://gigazine.net/gsc_news/en/20260916-system-one-jev/)
- [awesome-jev-typesafe, community index](https://github.com/keltokhy/awesome-jev-typesafe)
- [trade-jev, order-book backtest](https://github.com/justinhe16/trade-jev)
