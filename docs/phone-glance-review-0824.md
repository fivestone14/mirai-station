# WORK ORDER — SNDK phone view (`static/m/`), 23 surviving findings

No file was modified. 23 findings collapse to **14 work items** (9 merges).

## MERGE MAP

| Item | Findings folded in | Root cause |
|---|---|---|
| W1 | 3, 8(a) | gate direction taken from the payload's scan-time `w.side` |
| W2 | 14, 10, 5, 6, 18 | `page.js:243-245` — edge-marker queue admits non-book levels, is ordered by solver trial order, and classifies against the padded bounds |
| W3 | 23, 2, 13, 16 | `page.js:510` — footer gated on `withdrawn` instead of on what the ladder actually said |
| W4 | 4 | bracket label drops the call/put qualifier |
| W5 | 8(b) | bracket drawn over a wall of the other pool that price has crossed |
| W6 | 15 | tag cap-7 flattens §9.4's never-drop tiers |
| W7 | 9, 17 | re-anchor has no hysteresis; a fresh window sits inside its own trigger band |
| W8 | 22 | transport failure indistinguishable from an empty station |
| W9 | 11 | `.g-lab` cannot shrink; the measure runs off screen |
| W10 | 1 | gate region is shorter than its own content; `.g-foot` clipped to nothing |
| W11 | 7 | `gMinutes` can print a 60-minute remainder |
| W12 | 12 | zero span → NaN into SVG attributes |
| W13 | 21 | §9.3 o never built; §9.3 n unreachable |
| W14 | 19, 20 | SPEC-only corrections + the doc sweep this work order creates |

W2, W7 and W12 all land inside `page.js:229–263`. **Apply them as the single consolidated block at the end of this order, not as three separate patches.**

---

# TIER 1 — CRITICAL (the screen misrepresents the data)

## W1 — Gate direction must come from the price on screen
**Findings 3 + 8(a).** Same line, same defect, two independent reproductions.

`page.js:459`
```
-  let dir = (w.side === 'call' ? '▲ NEXT ABOVE' : '▼ NEXT BELOW');
+  // Against the price ON SCREEN, like the distance and the mechanism sentence
+  // beside it (§14.15). walls_ladder buckets a cluster by the SCAN spot, so a
+  // live tick through the nearest wall makes the payload's side label name a
+  // direction the plot directly above this card contradicts.
+  let dir = (w.strike > ref ? '▲ NEXT ABOVE' : '▼ NEXT BELOW');
```
`ref` is in scope (line 449) and finite (line 450's `nearestWall(walls, ref)` returns null on a null spot and the function has already returned at 451).

**Do not touch** `$('gStrike').className = 'g-k ' + w.side` (line 478) or `'p-bar ' + near.side` (line 352). §4 defines jade/coral as the side of the *book*, not above/below price; a coral put cluster drawn above price is still correct under the colour law. §14.2 intact.

§14: extends 14.15, authors no English (both strings are already in the §11 inventory), 14.2 and 14.26 untouched. WITHDRAWN branch cannot change — there `ref === scene.price.now === the scan spot`.

**Conflict:** ship with **W5**. W1 fixes the words, W5 fixes the bracket drawn over the same crossed wall; either alone leaves the card and the plot disagreeing in one direction.

**SPEC:** §10D table row `gDir`, source column `the wall's side` → `the wall's strike against the price on screen`.

## W2 — Edge markers: book levels only, ranked by kind then weight, classified by price, laid out by price
**Findings 14 (critical) + 10 + 5 + 6 + 18.** All five are `page.js:243-245`. Four distinct faults, one block:

1. exiled tape points and the session low take both slots, so the heaviest wall reaches no pixel (14, 10 — the named §14.6 regression, reproduced on the unmodified reference payload with a live quote of 1550, 0.9σ above the scan);
2. classification against the *padded* bounds loses any level exiled by less than the pad width, and loses every refused level when a far magnet or `walls[0]` makes the admission test fail for candidates sitting in plain sight (5);
3. the queue inherits the solver's descending-gex trial order, so the stacks run backwards in price (6, 18);
4. `WIN = solveWindow(...)` at 240 is unguarded, so a null re-solve throws on `WIN.refused` at 243.

Full replacement is in the consolidated block below. The four elements it must carry:

- `filter(l => l.kind === 'wall' || l.kind === 'magnet')` — a tape price has no strike; its off-window portion is already carried by `clip-path:url(#pc)` (§14.24).
- `&& !drawnY.has(+l.y)` — required, not optional. `push()` gates only on `inWin`, so a core wall exiled by radius that still lands inside the pad gets a rule **and**, once classification moves to `ref`, would get a contradictory marker as well.
- `.sort(rank desc || gex desc)` — rank by kind **first**. Never compare a wall's `gex` against a magnet's `share`; §14.7 forbids the two denominators sharing a gauge, and a straight gex sort puts a magnet ahead of a 14.8-gex wall.
- `.slice(0,2)` **then** `.sort((x,y) => y.y - x.y)` — descending price on **both** stacks (finding 18's correction to finding 6: the top stack's row 0 at `y=10` is the row *farthest* from the plot, so it takes the highest strike). Slice before sort so the gex-priority pair is what survives.

**Do NOT** narrow `glance.js:224` to min/max of `tapePoints`. §9.1 says min and max, the code pushes all, and finding 10 measured that narrowing it moves `WIN` (at spot 1555, `[1432,1562]` → `[1444,1561]`). The spec text is what is wrong. See W14.

§14: restores 14.6; serves 14.7, 14.21, 14.24. **Residual to log, not fix:** with walls ranked in, a *third* book level off-window on one side is still dropped. §14.6's absolute "nothing can ever be silently dropped" only holds while at most two book levels sit off-window per side. Separate ticket.

**Comment to carry in the code** (finding 5's honest caveat): a refused level that sits *inside* the window is now named at a fixed top/bottom row, which is not where it sits. That is what SPEC row v prescribes for every refused level, and it beats not drawing it at all.

**Conflicts:** W13 depends on W2 (the lead-weight marker can only attach to a marker that actually renders). If anyone ships finding 17's window-widening instead of W7, W2's ref-split becomes *mandatory* rather than merely correct.

---

# TIER 2 — CHANGES WHAT A TRADER SEES

## W6 — Tag cap must respect §9.4's never-drop list
**Finding 15.**

`page.js:359`
```
-    if(l.kind === 'wall') members.push({y:l.y, cls:'p-tag ' + l.side, lvl:l, keep:2});
+    // §9.4's tiers, not a tie broken by strike. The never-drop set is exactly
+    // six members (chip + 2 nearest + 2 heaviest_behind + lead magnet), which
+    // is what the cap of 7 was sized for; walls.*[1] is the one wall that may
+    // drop, and VWAP drops before it.
+    if(l.kind === 'wall')
+      members.push({y:l.y, cls:'p-tag ' + l.side, lvl:l, keep:(l.nearest || l.behind) ? 2 : 1});
```
Leave the lead magnet at `keep:2`, VWAP at `0`, chip at `3`, and the sort/slice as written. `nearest` comes from `_lvlWall`/`_wall`; `behind` from `optionalLevels`' `Object.assign` — both survive `mergeLevels`' shallow copy.

**Do NOT** also strip the rule from a cap-dropped level. §9.4 drops the VWAP tag while §9.3 j draws the VWAP line unconditionally, and §9.3 h draws every magnet runner untagged by design.

§14: closes a second door onto the 14.6 / 14.21 failure (`call_heaviest_behind` renders as the thickest stroke on the plot with its price nowhere on screen). Verified byte-identical on §12 states 1, 2 and 3 — when nothing drops, the pre-sort is unobservable because `kept` is re-sorted by `y` after the slice.

## W3 — The footer speaks when the bracket's label did not
**Findings 23 + 2 + 13 + 16.** One guard, four symptoms: the note stated twice in the withdrawn state (2, 13, 16), the note lost entirely at 320px where the bracket is under 34px tall (23), and the footer silent in the three no-bracket early returns (`LADDER TOO SHORT`, `NO PRICE MEASURED`, `!WIN`, plus `!(b > a)`).

Adopt finding 23's variant — gate on the **label**, not the path. It subsumes the other three: label drawn → footer silent; bracket drawn but label suppressed → footer speaks; no bracket at all → footer speaks.

`page.js:12`
```
-let PAY = null, LIVE = null, DIARY = [], READS = [], WIN = null, LADDER_H = 376;
+let PAY = null, LIVE = null, DIARY = [], READS = [], WIN = null, LADDER_H = 376;
+let CLEAR_SAID = {call:false, put:false};
```
`page.js:197` (first line of `paintLadder`, **above** every early return)
```
+  CLEAR_SAID = {call:false, put:false};
   const svg = $('svg');
```
`page.js:310` (inside the `(b - a) >= 34` block, after `wordRows.push(by);`)
```
       wordRows.push(by);
+      CLEAR_SAID[side] = true;
```
`page.js:507-510`
```
-  // the measured-empty side appears exactly once: as the bracket in the plot,
-  // which also shows its extent. The footer only speaks when the bracket could
-  // not draw.
-  if(!foot && !(st.ref && !st.withdrawn)) foot = farSideNote(walls, w.side) || '';
+  // the measured-empty side appears exactly once. It is normally said by the
+  // bracket's label in the plot, which also shows its extent; the footer speaks
+  // only when that label did not draw — a bracket under 34px, a suppressed
+  // bracket, or a ladder that took an early return.
+  const _far = w.side === 'call' ? 'put' : 'call';
+  if(!foot && !CLEAR_SAID[_far]) foot = farSideNote(walls, w.side) || '';
```
`paintAll` runs `paintLadder` before `paintGate` on both the 60s and 5s ticks, so the flag is always current. `farSideNote` already returns null unless `walls[far+'_side_clear'] === true`, so §14.16 is untouched.

**CONFLICT — HARD DEPENDENCY.** At 320×568 this fix *creates* the state finding 1 measured as fatal: both gauge rows visible **and** `gFoot` populated, in which `.g-foot` resolves to a 2px box holding an 18px line and the recovered sentence renders as nothing. **W3 must not ship without W10 part 3.** Shipping W3 alone converts a silent loss into a different silent loss on the same viewport.

**Note the withdrawn-state interaction with W10:** the duplication W3 removes is also what pushed the *normal* breakpoint over budget in SPEC's own reference state. W10 is still required — the state is reachable without withdrawal (any 320px board with a short bracket, and any `Weight not measured.`/`Next wall at …` footer alongside two rows).

**SPEC:** §10D degradation 5's trigger should read "the clear-side bracket's **label** could not draw", not "no price y".

## W4 — The bracket label must carry the qualifier the flag actually carries
**Finding 4.** `call_side_clear` means no *call-signed* cluster above spot; a wrongly-signed pile there is dropped from both pools and the flag still fires. Re-clustered against the real diary, this is true on **79 of 79 rows**, and on the reference render the words sit across a cluster carrying 34.6% of book |gamma|.

`page.js:308-309`
```
       o += '<text class="p-word dim" x="12" y="' + n1(by) + '">'
-         + (side === 'call' ? 'NO WALL ABOVE' : 'NO WALL BELOW') + '</text>';
+         + (side === 'call' ? 'NO CALL WALL ABOVE' : 'NO PUT WALL BELOW') + '</text>';
```
This is the uppercase form of the strings `glance.js:106` already settled on. Width checked by hand at the narrowest supported viewport: 18 chars × (6.0px mono advance + 0.14em tracking) ≈ 133px from x=12, ending ~145 against `PLOT_R` 206 at 320px — ~60px of slack.

**Do NOT** take the fallback of keeping the bracket and withholding the label; a bare bracket over that cluster asserts open air with no qualifier at all.

§14.14: not authored English (it matches the words `farSideNote` already uses), **but** §11 is a closed inventory — the SPEC edit in W14 must land in the same commit.

**Conflict:** complementary to W5, not redundant. W5 suppresses the bracket when a wall of the *other pool* has been crossed by live price; W4 covers the case the pools never see at all (a cluster dropped for carrying the "wrong" sign for its side). Neither substitutes for the other.

## W5 — Do not draw an emptiness over a wall price has crossed
**Finding 8(b).**

`page.js:301-303`
```
   for(const side of ['call','put']){
     if((sc.walls||{})[side + '_side_clear'] !== true) continue;
+    // The flag was measured against the SCAN spot. If a wall of the other pool
+    // now sits on this side of the price on screen, the side is not empty as
+    // drawn — say nothing here and let the gate footer carry the qualified note.
+    const other = side === 'call' ? 'put' : 'call';
+    const cross = ((sc.walls||{})[other] || [])
+      .concat([(sc.walls||{})[other + '_heaviest_behind']])
+      .some(e => e && e.strike != null &&
+                 (side === 'call' ? Number(e.strike) > ref : Number(e.strike) < ref));
+    if(cross) continue;
     const a = side === 'call' ? plotTop : priceY, b = side === 'call' ? priceY : plotBottom;
```
§14.16 intact: `=== true` remains a necessary condition; this adds a second necessary condition, which is legal (14.16 makes `=== true` necessary, not sufficient). **Requires W3** — without it the suppressed bracket produces no statement anywhere.

**Rejected alternative:** anchoring the bracket's near end at `yFor(sc.price.now)` instead of `priceY`. It keeps the drawn extent equal to the measured extent, but with price above the scan spot the bracket then encloses the price rule. Suppression is the narrower change.

**SPEC:** §9.3 row k's degradation column gains this condition.

## W7 — The frozen window must actually stay frozen
**Findings 9 + 17.** A fresh window seats price `0.06S / 1.12S` = 5.36% inside its own edge, permanently within the 12% re-anchor band, so once price is the outermost core level the window re-solves on **every 5-second quote**. Measured: 296 of 300 ticks move the 1450 rule; a ten-cent oscillation at the exile radius swings it 34px and flips a wall between an in-plot rule and an edge marker.

Adopt **finding 9's travel gate** (in the consolidated block). It changes no displayed geometry and cannot swallow a refused level.

**Rejected:** finding 17's post-solve widening of `WIN.hi/lo` (it moves every level's y away from the solver's own output and needs a `markLo/markHi` shadow so the markers stay honest); re-arming only at 25% (a trending price then leaves the window entirely — no rule, no dot — until the next 60s tick); widening the 6% pad in `solveWindow` (it feeds the `MIN_RANGE_SHARE` admission test and would re-decide which levels are admitted, breaking every §12 golden number).

The constant **must stay below 5.36%** — that is what guarantees price re-anchors before it can leave the window it is anchored in. `0.05` is the value.

§14: the solver is untouched, so 14.6 is unaffected. A first solve never re-anchors, so §12's states stay byte-identical.

## W8 — One dropped request must not blank the board
**Finding 22.** `getJSON`'s catch returns `{status:0, body:null}` — byte-identical to "the station answered with nothing" — and `body.failed` hides all six regions while `PAY.scene` is intact in memory and being repainted underneath. Any non-JSON response (a Cloudflare/Caddy 502 page) takes the same path. `visibilitychange` fires `loadPayload` on wake, i.e. exactly when the radio has just reassociated.

`page.js:34-41`
```
 async function getJSON(url){
   try{
     const r = await fetch(url, {cache:'no-store'});
     const t = await r.text();
-    try { return {status:r.status, body:JSON.parse(t)}; }
-    catch(e){ return {status:r.status, body:null}; }
-  } catch(e){ return {status:0, body:null}; }
+    try { return {status:r.status, body:JSON.parse(t), reached:true}; }
+    catch(e){ return {status:r.status, body:null, reached:true}; }
+  } catch(e){ return {status:0, body:null, reached:false}; }
 }
```
`page.js:55-57`
```
   const pay = r.body;
-  if(!pay || pay.error || !pay.scene)
-    return fail('No SNDK scene yet.', (pay && pay.error) || 'the station returned nothing');
+  if(!pay || pay.error || !pay.scene){
+    // A request that failed is not an empty station. The last good payload keeps
+    // painting and ages honestly on its own row_ts via bookAge() — lantern past
+    // stale_book_min, withdrawal past heartbeat_min (§10A: the levels stay).
+    // Only a board that has never existed goes blank.
+    if(PAY && PAY.scene){ paintAll(); return; }
+    return fail('No SNDK scene yet.', (pay && pay.error) || 'the station returned nothing');
+  }
```
Also clear the stale failure text on the success path — `page.js:59-60`:
```
   document.body.classList.remove('failed');
   $('fail').hidden = true;
+  $('fail1').textContent = ''; $('fail2').textContent = '';
```
Leave the 403 branch (52-54) exactly as is: a 403 is an answer from the station and is authoritative.

§14: **14.26 is not implicated** — a ghost is a level retained after a *newer* payload dropped it; here no newer payload exists. 14.9/10/11/12 are what make this honest: `bookAge()` runs off `row_ts` against the wall clock, so a retained payload cannot look fresh, and 14.19 already withholds the change % across a date boundary.

**Do NOT** add a "the station could not be reached" string to `page.js` alone. `r.reached` is now available, but §11's closed inventory means the string and the §10F amendment must land together or not at all. Scope this order to the blanking.

## W9 — The gate's label row must not push the measure off screen
**Finding 11.** In the withdrawn state `▼ NEXT BELOW · AT THE 12:12 SCAN   $39 · HELD 1H 26M` measures ~370px (SF Mono) / ~361px (Roboto Mono) against a content box of viewport − 28. Both items are `nowrap` with no `min-width:0`, so neither can shrink and `margin-left:auto` collapses under overflow — `gMeas` is the item that runs off the right edge, last character first. On the `unchanged_min_at_least` path that first casualty is the `+`, which §10D calls load-bearing: clipping it turns "held at least 2h40m" into "held exactly 2h40m".

`index.html:169-173`
```
 .g-dir{font:500 10px/1.2 var(--mono);letter-spacing:.14em;text-transform:uppercase;
-       color:var(--ink-dim);white-space:nowrap}
+       color:var(--ink-dim);white-space:nowrap;
+       min-width:0;overflow:hidden;text-overflow:ellipsis}
 .g-meas{margin-left:auto;font:500 11px/1.2 var(--mono);letter-spacing:.02em;
         text-transform:uppercase;font-variant-numeric:tabular-nums;color:var(--ink-dim);
-        white-space:nowrap}
+        white-space:nowrap;flex:none}
```
`.g-meas{flex:none}` makes the measure inviolable; `.g-dir` is the item that gives way. This also brings `.g-lab` into line with `.g-foot`, `.fresh` and `.lastscan`, which all already carry nowrap + overflow:hidden + ellipsis — it is the lone outlier.

**Optional, and it needs the SPEC edit:** shortening `page.js:463` from `' · AT THE ' + et + ' SCAN'` to `' · ' + et + ' SCAN'` drops 52px and lets the whole clause survive intact at 360/375/384/412, ellipsizing only `SCAN` at 320. Costs nothing informationally — the scan time appears twice more in that state (the axis right foot `SCAN 12:12`, the masthead `LAST SCAN 1,489.21 · 3H 46M AGO`) — but it edits a §11 string, so SPEC lines 661, 722 and 795 move with it. **Take it or leave it as one decision; do not change the string without the doc.**

## W10 — The gate region is shorter than its own content
**Finding 1**, the only finding verified in a real engine (WKWebView, measured boxes + screenshots). At 384×780 in the reference withdrawn state `.g-foot` resolves to a 12px box holding an 18px line with its baseline 2px below the clip — the sentence renders sliced through the letterforms. At 320×568 it resolves to **2px** and the sanctioned string renders as nothing at all. Cause: `.g-foot` is the only gate child whose `overflow:hidden` zeroes its automatic minimum, so the entire deficit lands on it.

**Part 1 — normal breakpoint, region grows 16px.**
`index.html:61`
```
-  --r-mast:88px; --r-regime:48px; --r-gate:128px; --r-read:92px; --r-foot:20px;
+  --r-mast:88px; --r-regime:48px; --r-gate:144px; --r-read:92px; --r-foot:20px;
```
`page.js:23`
```
-  const FIXED = SHORT ? 332 : 376;
+  const FIXED = SHORT ? 332 : 392;
```
`FIXED` is defined as A+B+D+E+F and must move with D or the six regions overrun the viewport and `body{overflow:hidden}` clips the footer (§1: no region ever hides).

**Part 2 — the head row must hold its own content.**
`index.html:174`
```
-.g-head{display:flex;align-items:baseline;gap:10px;height:40px;min-width:0}
+.g-head{display:flex;align-items:baseline;gap:10px;height:48px;min-width:0}
```
40px is 8px under a 30px/1.0 mono strike (baseline 14px down) baseline-aligned against a 2-line 13/1.4 clamp; measured `scrollHeight` is 48. Without this, growing the region alone still leaves the mechanism sentence's second line sitting on the gauge bars.

**Part 3 — short breakpoint sheds content instead (REQUIRED, and W3's dependency).** Growth is impossible here: 568 − 28 = 540; mast 76 + regime 44 + read 76 + foot 18 = 214; the ladder's 200px floor caps `--r-gate` at 126, and at 126 `.g-foot` still comes out at 2px. Add inside `@media (max-height:700px)` at `index.html:63-65`:
```
+  .gate{gap:2px;padding:6px 0 6px}
+  .g-head{align-items:flex-start;height:36px}
```
(saves ~10px + ~12px; content then needs ~104px against a 105px box). If the mechanism sentence's second line clips by a fraction, use `height:37px`. This touches no §11 string and no §3 type step — it changes alignment and padding only.

**Not required:** `--r-read` on the short breakpoint. Two independent real-browser passes (WebKit and Chromium) measured the 3-line reading overflowing its region box by ~4–6.7px with **3–5px of ink clearance** to the footer — crowding, not collision. `-webkit-line-clamp:2` there would truncate the shipped sentence mid-clause. If someone wants the boundary rhythm back, `--r-read:80px` on short (4px off a 204px ladder, clear of the 180 guard) is the lever — a taste call, not part of this order.

**Consequences to record (W14):** SPEC §1's D row 128 → 144 and FIXED 376 → 392; the reference results become **384×780 → 360** and **412×915 → 495** (320×568 stays 208 — the short `FIXED` is unchanged). §13 step 1's check line `208 / 376 / 511` becomes `208 / 360 / 495`. §14.23 is untouched: `LADDER_H` stays an explicit pixel value, above the 200 floor and the 180 guard at every supported size.

**The invariant to hold, whatever is chosen:** no line of type on this page renders with its baseline below its own clip.

## W11 — `gMinutes` can print sixty minutes
**Finding 7.** `Math.round(m % 60)` returns 60 for the last 30 seconds of every hour, producing `1H 60M` in the freshness chip and `LAST SCAN 1,489.21 · 1H 60M AGO` beside it — repainted every 5s, so reliably visible. `bookAge` and `modelRead` both feed it continuous minutes.

`glance.js:27-28`
```
   if(m<1) return 'just now';
-  if(m<60) return Math.round(m)+'m';
-  const h=Math.floor(m/60), r=Math.round(m%60);
+  const t=Math.round(m);
+  if(t<60) return t+'m';
+  const h=Math.floor(t/60), r=t%60;
   return r? h+'h '+r+'m' : h+'h';
```
Diverges from today only in `[x*60+59.5, (x+1)*60)`. No twin to keep in parity: `grep -rn "%60"` over the runtime returns this site and `static/index.html:6438`, which takes integer minutes and never splits h/m. §14.10 intact — the warn/bad class is computed from `st.age.min` against `payload.gates`, never from the string.

## W12 — No NaN may reach an SVG attribute
**Finding 12.** With `one_sigma_dollars` absent (`build_scene` emits `round(sig,2) if sig else None`) the degenerate floor cannot fire, and with exactly one distinct core level the padded span is 0 → `k = Infinity` → `y`, `cy`, `height` all NaN on the band, price rule, halo, dot, chip and chip text. Reachable: a torn `net_by_strike` (walls and magnet pruned from the payload entirely), a session's first scan where `session_low == session_high`, or `payload.session` absent so the diary fetch never runs.

Guard is in the consolidated block, placed **after** the span is computed so it covers the first solve and the re-anchor without adding a throw inside `solveWindow` (which `page.js` calls unguarded on the re-anchor path).

**Two notes for whoever lands it.** The copy is slightly off — a price *is* measured here, only the scale is missing; the alternative (a sigma-free fallback half-width, e.g. 0.5% of price, making the degenerate floor unconditional) keeps the price rule honest but puts a fabricated dollar scale on the plot and wants a sign-off the one-line guard does not. Either way, **add a sigma-null fixture to `design/`** — SPEC line 655 promises that degradation and nothing in the fixture set exercises it, which is why the sweep never reached this branch.

---

# TIER 3 — CRAFT AND CONFORMANCE

## W13 — Build §9.3 o, delete §9.3 n
**Finding 21.** Reachable on recorded tape: when both `call[0]` and `put[0]` sit past 1.75σ, `nearestWall` returns an exiled level and the bug triangle correctly does not draw — but the substitute the spec names (the edge marker's strike at weight 600) does not exist. SNDK: 4 of 757 recorded scans; SPX: 329 of 3,355.

`page.js`, just before the two `forEach` loops at 397/401 (`near` is in scope from 349):
```
+  const namedEdge = (near && !inWin(near.strike)) ? Number(near.strike) : null;
+  const edgeCls = l => 'p-edge' + ((namedEdge != null && l.kind === 'wall'
+                     && l.side === near.side && +l.y === namedEdge) ? ' lead' : '');
```
then replace the literal `"p-edge"` in **both** loops with `edgeCls(l)`. Match on kind+side+strike, never strike alone, so an exiled magnet on the same strike cannot steal the emphasis.

`index.html`, beside `.p-edge` at line 161:
```
+.p-edge.lead{font-weight:600}
```
Weight only — `.p-edge` keeps `--ink-faint` so the marker stays subordinate to in-window tags.

§14: marker count untouched (14.6), not a legend (14.21), no English (14.14), no Greek (14.22).

**Depends on W2.** Before W2 the named wall can be sliced out of the queue by exiled tape points, in which case the new class is a no-op rather than a fault.

**Spec deletion:** §9.3 row n's "Off-window → no dot; the tag chip pins to the top/bottom of the tag lane with a ▲/▼ prefix" is dead text — price is an unconditional member of `coreLevels`, so it can never be off-window (200,000 randomised `solveWindow` trials: zero). Replace the clause with the reason; leave the `inWin(ref)` guards at `page.js:342` and `:363` in place as cheap defence.

## W14 — SPEC.md corrections and the doc sweep
No pixels change. `SPEC.md` is a build gate (§13 asserts against §12's numbers), so leaving it wrong invites a future "fix" that re-breaks §14.6.

**Standalone corrections:**
- Line 791: `26M` → `27M`. `reading_ts` 11:47:39.119 against 12:14:10 is 26.515 min and §10E defines `rdAge` as `gMinutes(ageMin)`, which rounds. (Finding 20.)
- Line 797: `PAD_B becomes 31` is wrong — `walls.put[1]` 1180 (29.0%) and `walls.call[1]` 1350 (31.6%) also fail the 50% test, so three levels are refused: bottom markers `▼ 1,150 HEAVIEST` then `▼ 1,180` (`PAD_B` 44) and top marker `▲ 1,350` (`PAD_T` 25). §13 step 3's check line should widen to all three. (Finding 20.)
- Line 720 and line 615: `9:31` → `09:31`. Ratify the padded form; do **not** change the rendering. Optionally `glance.js:420` `hour:'numeric'` → `hour:'2-digit'` so the options object states what Intl actually resolves it to under `hour12:false` — byte-identical output, removes a latent surprise. §9.3 w and §10D already spell the token `HH:MM`, so `9:31` in §11 was the outlier. (Finding 19.)
- §9.1: "min and max of `tapePoints(DIARY)`" → all tape points, matching `glance.js:224`. Row v (line 614) and §14.6 (line 826): "levels **from the book** exiled or refused". (From W2.)

**Generated by this order:** §10D `gDir` source (W1); §10D degradation 5 trigger (W3); line 604, 720 and 781 bracket-label strings (W4); §9.3 row k degradation column (W5); §9.2's RE-ANCHOR paragraph gains the travel gate and why the constant must sit under 5.36% (W7); lines 661/722/795 if W9's optional wording is taken; §1's D row, FIXED, reference results and §13 step 1 (W10); §9.3 row n (W13).

---

# CONSOLIDATED BLOCK — `page.js` 229–263 (W2 + W7 + W12)

Replace lines 229 through 263 in one edit:

```js
  if(!WIN){
    const core = coreLevels(sc, ref, st.vwap, st.points);
    WIN = solveWindow(core, optionalLevels(sc), ref, st.sigma, sessRange);
    if(WIN) WIN.anchor = ref;
  }
  if(!WIN){ svg.innerHTML = '<text class="p-word" x="11" y="34">NO PRICE MEASURED</text>'; return; }

  // re-anchor: price near the edge earns a fresh window, and it JUMPS. A tween
  // would make every ordinary tick as salient as this rare one.
  // The travel gate is what keeps it rare. A fresh window leaves price
  // 6/112 = 5.36% of the span inside its own edge — already inside the 12% band
  // — so without it the test is true again on the very next 5s quote and the
  // frozen board slides on every tick instead of jumping once. Keep the constant
  // BELOW 5.36%: that is what guarantees price re-anchors before it can leave
  // the window it is anchored in.
  const span0 = WIN.hi - WIN.lo;
  const nearEdge = (ref < WIN.lo + 0.12*span0 || ref > WIN.hi - 0.12*span0);
  const moved = (WIN.anchor == null) || Math.abs(ref - WIN.anchor) >= 0.05*span0;
  if(nearEdge && moved){
    const core = coreLevels(sc, ref, st.vwap, st.points);
    const w2 = solveWindow(core, optionalLevels(sc), ref, st.sigma, sessRange);
    if(w2){ WIN = w2; WIN.anchor = ref; }        // a null re-solve must not blank WIN
  }

  // A zero span sends NaN into every y, cy and height below; in a browser each
  // invalid length falls back to 0 and the plot renders as garbage pinned to the
  // top edge with no message. Guarded here rather than in solveWindow so the
  // re-anchor path above cannot throw.
  const span = WIN.hi - WIN.lo;
  if(!(span > 0)){ svg.innerHTML = '<text class="p-word" x="11" y="34">NO PRICE MEASURED</text>'; return; }
  const inWin = v => v != null && isFinite(v) && v >= WIN.lo && v <= WIN.hi;

  const wc = (sc.walls||{}).call || [], wp = (sc.walls||{}).put || [];
  const mag = (sc.magnet||{}).top_strikes;

  // Every y that WILL receive a rule below, gated by push()'s own test.
  const drawnY = new Set();
  const markDrawn = y => { if(inWin(y)) drawnY.add(+y); };
  if(wc[0] && wc[0].strike != null) markDrawn(Number(wc[0].strike));
  if(wp[0] && wp[0].strike != null) markDrawn(Number(wp[0].strike));
  for(const l of WIN.admitted) markDrawn(l.y);
  if(Array.isArray(mag) && mag.length && Array.isArray(mag[0]) && mag[0][0] != null)
    markDrawn(Number(mag[0][0]));
  for(const m of magnetRunners(sc)) markDrawn(m.y);

  // Only a BOOK level can be named at an edge. A tape price or the session low
  // has no strike and its off-window portion is already carried by the clip
  // (§8, §14.24); admitting them breaks the slot count §14.6 rests on — that is
  // the 2026-08-24 bug, where ~70 exiled tape points took both slots and the
  // wall the gate names in 30px type reached no pixel. Rank by KIND first: a
  // wall's gex and a magnet's share are different denominators (§14.7). A level
  // that already got a rule is not named again. Split on the PRICE, not on the
  // padded bounds, or a level exiled by less than the pad width lands in neither
  // stack. A refused level that sits inside the window is named at a fixed row
  // that is not where it sits — that is what row v prescribes, and it beats not
  // drawing it at all.
  const rank = l => (l.kind === 'wall' ? 2 : 1);
  const leftover = WIN.refused.concat(WIN.exiled)
    .filter(l => (l.kind === 'wall' || l.kind === 'magnet') && !drawnY.has(+l.y))
    .sort((a, b) => rank(b) - rank(a) || (b.gex || 0) - (a.gex || 0));
  // gex priority picks the pair; PRICE decides the row, on a plot whose whole
  // grammar is vertical = price. Descending on BOTH stacks: the top stack's row
  // 0 is the row farthest from the plot, the bottom stack's row 0 the nearest.
  const byPrice = a => a.sort((x, y) => y.y - x.y);
  const above = byPrice(leftover.filter(l => l.y > ref).slice(0, 2));
  const below = byPrice(leftover.filter(l => l.y < ref).slice(0, 2));
  const PAD_T = 12 + 13*above.length, PAD_B = 18 + 13*below.length;
  const plotTop = PAD_T, plotBottom = SVGH - PAD_B, plotH = plotBottom - plotTop;
  const k = plotH / span;
  const yFor = v => plotTop + (WIN.hi - v) * k;

  // ---- levels ------------------------------------------------------------
  const ruled = [];
  const push = l => { if(inWin(l.y)) ruled.push(l); };
  if(wc[0] && wc[0].strike != null) push(_lvlWall(wc[0], 'call', true));
  if(wp[0] && wp[0].strike != null) push(_lvlWall(wp[0], 'put', true));
  for(const l of WIN.admitted) push(l);
  if(Array.isArray(mag) && mag.length && Array.isArray(mag[0]) && mag[0][0] != null)
    push({y:Number(mag[0][0]), kind:'magnet', lead:true, share:Number(mag[0][1]), weight:1});
  for(const m of magnetRunners(sc)) push(m);
  const levels = mergeLevels(ruled, span);
```
Note the mechanical changes inside the block: `inWin`, `wc`/`wp` and `mag` are hoisted above their old positions (none of them depends on plot geometry — `inWin` reads only `WIN.lo/hi`), `span` moves up and `k` stays down, and the old `const span = …, k = …` line is gone.

---

# CONFLICT AND ORDERING SUMMARY

| # | Constraint |
|---|---|
| 1 | **W3 must not ship without W10 part 3.** W3 makes the footer speak at 320×568, and until the short breakpoint sheds content that sentence renders as a 2px smear. |
| 2 | **W1 ships with W5.** Same crossed-price root cause, opposite ends of the screen. |
| 3 | **W5 requires W3.** A suppressed bracket with the old footer guard says nothing at all. |
| 4 | **W4 is not made redundant by W5.** Different cause (a cluster dropped from both pools by sign), same false emptiness. |
| 5 | **W2 before W13.** The lead-weight marker needs a marker to attach to. |
| 6 | **W2, W7 and W12 are one edit**, not three — apply the consolidated block. |
| 7 | **W4, and W9's optional wording, require the §11 edit in the same commit** — §11 states that a string not on its list is not on the page. |
| 8 | **W8 must not add a new failure string** without amending §10F and §11. Scope it to the blanking. |
| 9 | **W10 changes SPEC §1 and §13 step 1's reference numbers** (376→360, 511→495). Update them or the build gate fails a correct build. |
| 10 | Finding 17's window-widening is **rejected** in favour of W7. If it is ever revived, W2's ref-split becomes mandatory, not optional. |

**§14 status:** no item is regressed. W2 and W6 restore 14.6; W2 also serves 14.7 and 14.21; W1 extends 14.15; W5 stays inside 14.16 (`=== true` remains necessary); W4 and W9 need the §11 inventory moved with them; W8 does not touch 14.26; W10 keeps 14.23; W13 touches none.

**Open tickets deliberately NOT folded in:**
- `page.js:444-445` — `NO WALL EITHER WAY` / `The board was read and holds nothing above or below price.` carry the same unqualified overstatement as W4, from the same two flags via `bothSidesClear()`. Fixing it means authoring a new sanctioned string, so it needs its own §11 decision.
- `beyondWall` (`page.js:485`) still describes what lies *beyond* a wall that live price has crossed — the residual of W1's root cause on the gate's B row.
- With walls ranked into the edge queue (W2), a third off-window book level per side is still dropped. §14.6's absolute wording only holds while at most two sit off-window per side.
- `glance.js:224` pushes every tape point where §9.1 says min and max. **Fix the spec, not the code** — narrowing it measurably moves `WIN`.

**Verification gate before any of this is called done.** Every finding was verified against its own fix in isolation; nobody has run the composite. Re-run the harness across `payload-live`, `d-clear`, `d-nowall`, `d-bare`, `d-unknown`, the new sigma-null fixture and `pinned-0811` at 320×568 / 384×780 / 412×915, at 12:14 and the withdrawn 15:58, and assert §12 state 1 unchanged to the decimal. Then do the thing §13 step 9 and §14.23 both insist on and the harness cannot do: **a real browser at 320 and 412**, because W10 is a layout fix and a clean harness run proves nothing about layout.