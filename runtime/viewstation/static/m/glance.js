/* glance.js — the phone view's reasoning, kept out of the markup.
 *
 * Every function here is PURE (data in, value out) so the page can be read as
 * layout and this can be read as rules, and so the rules can be tested without
 * a browser.
 *
 * THREE LAWS THIS FILE ENFORCES
 *   1. Honest-absent. No datum yields no value, never a zero and never a guess.
 *   2. Where the weight is, never what price will do about it. That is the
 *      station's own rule for the model (sndk_read.py's doctrine: never say
 *      what dealers are doing, never say hedging holds or speeds a move), and
 *      docs/sndk-plan.md "Closed by measurement" records why — on SNDK no
 *      damping or amplifying effect was found, weeklies show no pin, and a
 *      wall is relabelled on every crossing on record. A screen that says what
 *      the model is forbidden to say teaches the reader the opposite of the
 *      finding.
 *
 *      This law used to read "no English is authored here; every sentence is
 *      copied from the desktop's snkArrows". The copying was faithful and the
 *      sentences were the problem: "Dealers sell the rallies here — it caps
 *      the move" and "walls hold / walls give way" were removed 2026-09-10,
 *      and the desktop's snkArrows went the same day, so neither screen says
 *      them now.
 *   3. No Greek reaches the surface. Distances leave this file in dollars. The
 *      ruler is stated once, in English, by the page.
 */

/* ---- formatting ------------------------------------------------------- */

function gUsd(v, dp){
  if(v==null||!isFinite(v)) return null;
  return '$' + Number(v).toLocaleString('en-US',
    {minimumFractionDigits: dp==null?2:dp, maximumFractionDigits: dp==null?2:dp});
}

function gMinutes(m){
  if(m==null||!isFinite(m)) return null;
  if(m<1) return 'just now';
  // Round ONCE, then split. Rounding the remainder separately returns 60 for
  // the last thirty seconds of every hour, so the freshness chip printed
  // "1H 60M" — repainted every 5s, so reliably visible.
  const t=Math.round(m);
  if(t<60) return t+'m';
  const h=Math.floor(t/60), r=t%60;
  return r? h+'h '+r+'m' : h+'h';
}

/* ---- the environment, in a word --------------------------------------- */

function envParts(regime){
  // The regime word only. The gloss under it used to read "walls hold" or
  // "walls give way" off the gamma sign, which is a claim that hedging damps
  // or speeds a move — the one sentence the model is forbidden to write, and
  // measured absent on SNDK. The sign was also the literal string "unknown" on
  // 490 of 5,423 scans (9.0%) and on every other scan it rested on an assumed
  // dealer convention. Nothing on this screen reads it any more.
  const word = regime ? regime.regime_label : null;   // sr-7: regime.word -> regime_label
  return {word: word ? String(word) : ''};
}

/* ---- the three levels -------------------------------------------------- */

// ONE full scale for every weight on the screen: the card's bars, the chart's
// rail bars and the chart's line thickness. A per-scan maximum would make the
// biggest wall full every single scan and destroy comparison between days, so
// the scale is fixed and chosen from the tape.
//
// 30, not 20. Measured over 15,653 wall observations since 07-27 the share of
// the book runs p50 9.4%, p90 25.6%, p95 33.1%, and the last 8 sessions run
// heavier (p50 12.6%, p90 28.1%). At 20 a full bar was 15.5% of all walls and
// 27.1% of recent ones — a quarter of the levels drew identically at the cap,
// which is the comparison the scale exists to make. At 30 the cap takes 6.4%
// (8.1% recently), and the clip is still marked where it happens.
const FULL_SHARE = 30;

function shareBarPct(share){
  // The card's bar, in percent of its track. No datum: no bar and no track,
  // because an empty track reads as zero.
  if(share==null||!isFinite(share)) return null;
  return Math.max(2, Math.min(100, share/FULL_SHARE*100));
}

function wallStroke(share){
  // Heavier draws thicker, continuously: 1.2px for a wall that barely clears
  // the cluster floor, 7.6px at FULL_SHARE and above. Linear on purpose — the
  // card prints the exact number, so the line only has to rank, and a curve
  // that exaggerated small differences would rank things that are level. Two
  // walls a point apart draw alike, which is true: they weigh alike.
  if(share==null||!isFinite(share)) return 1.8;   // a default, never a claim
  return Math.round((1.2 + Math.min(share, FULL_SHARE)/FULL_SHARE*6.4)*100)/100;
}

function wallPassed(side, strike, price){
  // Has price, AS SHOWN, gone past this wall since the book was read?
  //
  // walls_ladder files a wall against the book's spot, up to a couple of
  // minutes old, while the price on screen repaints every 5s. Replayed over 8
  // sessions, price stood beyond a wall the card still showed on 2.7% of
  // minutes (5.8% on 09-10). The wall is relabelled at the next scan — on
  // every crossing on record — so until then the honest thing is to say price
  // has passed it and drop its colour: a call wall below price is not the call
  // side any more, and this screen teaches green as the call side.
  const k=_fin(strike), p=_fin(price);
  if(k==null||p==null) return false;
  return side==='call' ? p>k : side==='put' ? p<k : false;
}

function levelRows(walls, most, heaviest, price){
  // The card's rows: the nearest call wall, the strike with the most contracts,
  // the nearest put wall — ORDERED BY PRICE, top first, and never by kind.
  // The replay found the most-contracts strike above the call wall on 10.9% of
  // scans and below the put wall on 1.4%; a fixed call/most/put order would
  // draw those upside down. It is the same strike as a wall on 35.3%, and then
  // the two share one row rather than printing one price twice.
  //
  // An absent wall is a ROW, never a gap and never a zero: the side flag is a
  // measured emptiness (32.2% of recent scans had no put wall), and no flag
  // with no entry is no measurement at all.
  const rows=[], px=_fin(price);
  for(const side of ['call','put']){
    const e = walls && Array.isArray(walls[side]) ? walls[side][0] : null;
    if(e && _fin(e.strike)!=null){
      const k=Number(e.strike);
      rows.push({kind:'wall', side, strike:k,
                 share:_fin(e.cluster_share_of_book_gamma_pp),
                 passed:wallPassed(side, k, px),
                 heaviest:!!(heaviest && heaviest.role===side)});
    } else {
      rows.push({kind:'absent', side,
                 text:(walls && walls[side+'_side_has_no_wall']===true)
                      ? (side==='call' ? 'None above price' : 'None below price')
                      : 'Not measured'});
    }
  }
  const mk = most ? _fin(most.strike) : null;
  if(mk!=null){
    const m={count:_fin(most.contracts), traded:_fin(most.traded_today)};
    const hit=rows.find(r=>r.kind==='wall' && r.strike===mk);
    if(hit) hit.most=m;
    else rows.push({kind:'most', side:'most', strike:mk, most:m,
                    heaviest:!!(heaviest && _fin(heaviest.strike)===mk)});
  } else rows.push({kind:'absent', side:'most', text:'Not measured'});
  const key=r=> r.strike!=null ? r.strike
             : r.side==='call' ? Infinity : r.side==='put' ? -Infinity
             : (px!=null ? px : 0);
  return rows.sort((a,b)=>key(b)-key(a));
}

function lightNote(levels){
  // "Why the most-contracts strike can look light", or null when the sentence
  // would have nothing to point at.
  //
  // The two measures disagree because they count different things — the walls
  // are gamma from last night's positions with calls netted against puts; the
  // strike is a head count including today's trades — NOT because of distance.
  // The first draft said the heavier strike was "too far from price to count";
  // that was true on 3.4% of replayed scans.
  //
  // Hidden when: the heaviest pile peaks AT this strike (35.0% of scans) or
  // contains it (40.6%) — it does not look light then; or the heaviest pile is
  // not a wall at all (role null), so the sentence would name a level the
  // screen never draws.
  if(!levels) return null;
  const m=levels.most_contracts, h=levels.heaviest;
  if(!m || !h || _fin(m.strike)==null || _fin(h.strike)==null || _fin(h.share_pct)==null) return null;
  if(!h.role || h.holds_most_contracts || _fin(h.strike)===_fin(m.strike)) return null;
  const role={call:['call',false], put:['put',false],
              call_further:['call',true], put_further:['put',true]}[h.role];
  if(!role) return null;
  return {side:role[0], further:role[1], heavy:_fin(h.strike), share:_fin(h.share_pct),
          strike:_fin(m.strike), count:_fin(m.contracts), traded:_fin(m.traded_today)};
}

/* ---- price, age, and the two things that must never be guessed --------- */

function priorClose(price){
  // sr-7 rename: price.now -> live_spot
  const now=price&&price.live_spot, pct=price&&price.vs_prior_close_pct;
  if(now==null||pct==null||!isFinite(now)||!isFinite(pct)) return null;
  const d=1+pct/100;
  if(!isFinite(d)||d===0) return null;
  return now/d;
}

function bookAge(pay, nowMs){
  // ONE age, from row_ts against the wall clock.
  //
  // clock.book_age_min is never read. Off-live, build_scene stamps the clock
  // from the ROW's own timestamp, so it reads ~0 however old the scan is — a
  // number that is fresh because it is measuring itself. That is the failure
  // that let a dead Schwab login look healthy for 3.1 days.
  const t=Date.parse(pay&&pay.row_ts);
  if(!isFinite(t)) return {min:null, unknown:true};
  const now=(nowMs==null)?Date.now():nowMs;
  return {min:Math.max(0,(now-t)/60000), unknown:false};
}

function shownPrice(scene, live){
  const q=_fin(live&&live.spot);
  const v=(q!=null)?q:((scene&&scene.price)||{}).live_spot;   // sr-7 rename
  return (v!=null&&isFinite(v)) ? {v:Number(v), live:q!=null} : null;
}

function dayChange(scene, live, diaryLast, todayStr){
  // Gated on the SCAN'S OWN DATE against the reader's local date. The risk is a
  // percentage measured against ANOTHER day's close; comparing dates removes
  // exactly that and still lets a zero-minute-old scan show its own change. A
  // mismatch WITHHOLDS the figure. It never misstates it.
  const local=todayStr||etToday();
  // sr-7 rename: clock.date -> session_date
  if((((scene||{}).clock)||{}).session_date !== local) return null;
  const p=(scene||{}).price||{};
  const q=_fin(live&&live.spot);
  if(q==null) return _fin(p.vs_prior_close_pct);
  // the diary row carries the EXACT prior close; recovery from a 1-dp
  // percentage is a rounding of it, so prefer the real number when it is there
  const pc=_fin(diaryLast&&diaryLast.prior_close) || priorClose(p);
  return (pc&&isFinite(pc)) ? (q/pc-1)*100 : null;
}

function vwapPrice(scene, diaryLast){
  // A PRICE at a position. vwap_minus_live_spot_sigma (sr-8 rename of
  // vwap_dist_sigma — the VALUE never changed, only the name now spells its
  // subtraction) is (vwap - live spot)/sigma, so a NEGATIVE value means price
  // is ABOVE its average — 13 of 15 reviewers read it backwards. Rendering the
  // level instead of the ratio makes the sign trap structurally impossible: a
  // price cannot be read backwards. Recovering the level is still an ADD.
  const exact=_fin(diaryLast&&diaryLast.vwap);
  if(exact!=null) return exact;
  const p=((scene||{}).price)||{}, sig=_fin(((scene||{}).scale||{}).one_sigma_dollars);
  if(p.live_spot==null||p.vwap_minus_live_spot_sigma==null||sig==null) return null;
  return p.live_spot + p.vwap_minus_live_spot_sigma * sig;
}

/* ---- weight ------------------------------------------------------------ */

function railWidth(gex, full){
  // The chart's small rail bar beside a wall tag, on the same FULL_SHARE as the
  // card and the line thickness, so the three can never rank a wall
  // differently. The clip is marked where it happens.
  if(gex==null||!isFinite(gex)) return null;     // no datum: no bar AND no track
  return {w:Math.max(2, Math.min(full, gex/FULL_SHARE*full)), clipped: gex>FULL_SHARE};
}

/* ---- level assembly ---------------------------------------------------- */

function coreLevels(scene, price, vwap, points){
  // Always admitted, each still subject to the exile radius.
  const out=[], p=(scene||{}).price||{}, w=(scene||{}).walls||{};
  if(price!=null) out.push({y:price, kind:'price'});
  if(_fin(p.session_low)!=null)  out.push({y:p.session_low,  kind:'session'});
  if(_fin(p.session_high)!=null) out.push({y:p.session_high, kind:'session'});
  for(const pt of (points||[])) out.push({y:pt.s, kind:'path'});
  const c=(w.call||[])[0], u=(w.put||[])[0];
  if(c&&c.strike!=null) out.push(_wall(c,'call',true));
  if(u&&u.strike!=null) out.push(_wall(u,'put',true));
  // sr-7 reshape: top_strikes entries are {strike, share_of_book_gamma_pp}
  // dicts now, not [strike, share] pairs
  const mag=((scene||{}).magnet||{}).top_strikes;
  if(Array.isArray(mag)&&mag.length&&mag[0]&&_fin(mag[0].strike)!=null)
    out.push({y:Number(mag[0].strike), kind:'magnet', lead:true,
              share:_fin(mag[0].share_of_book_gamma_pp), weight:1});
  if(vwap!=null) out.push({y:vwap, kind:'vwap'});
  return out;
}

function optionalLevels(scene){
  // Tried one at a time, HEAVIEST FIRST, each subject to the admission test.
  const out=[], w=(scene||{}).walls||{};
  for(const side of ['call','put']){
    const b=w[side+'_heaviest_wall_behind_the_ladder'];   // sr-7 rename
    if(b&&b.strike!=null) out.push(Object.assign(_wall(b,side,false),{behind:true}));
    const l=w[side];
    if(Array.isArray(l)&&l[1]&&l[1].strike!=null) out.push(_wall(l[1],side,false));
  }
  return out.sort((a,b)=>(b.gex||0)-(a.gex||0));
}

function magnetRunners(scene){
  const out=[], mag=((scene||{}).magnet||{}).top_strikes;
  if(!Array.isArray(mag)||mag.length<2) return out;
  // sr-7 reshape: dict entries, as in coreLevels
  const top=_fin(mag[0]&&mag[0].share_of_book_gamma_pp);
  for(let i=1;i<mag.length;i++){
    const m=mag[i];
    if(!m||_fin(m.strike)==null) continue;
    const share=_fin(m.share_of_book_gamma_pp);
    // continuous, no threshold anywhere: sr-3 deleted a hardcoded 5.0pp tie
    // constant for shipping a near-constant as a finding, and any cutoff here
    // re-imports it. A near-tie must LOOK like a tie without anyone deciding
    // where a tie begins.
    out.push({y:Number(m.strike), kind:'magnet', lead:false, share,
              weight:(share!=null&&top)?Math.max(0.28, share/top):0.5});
  }
  return out;
}

function solveWindow(core, optional, price, sigma, sessionRange){
  // Computed ONCE per payload, then frozen: at every 5-second repaint the
  // geometry is bit-identical and exactly one mark has moved, so a glance is a
  // comparison against the last one rather than a fresh read.
  const FAR=1.75, MIN_RANGE_SHARE=0.50;
  const far=(sigma>0&&isFinite(sigma))?sigma*FAR:null;
  const exiled=[], admitted=[], refused=[];

  const inRadius=l => far==null||price==null||Math.abs(l.y-price)<=far;
  const keep=[];
  for(const l of core){ if(inRadius(l)) keep.push(l); else exiled.push(l); }
  if(!keep.length) return null;

  let lo=Math.min(...keep.map(l=>l.y)), hi=Math.max(...keep.map(l=>l.y));
  // degenerate floor: a pinned day must not render as a single line
  if(sigma>0&&isFinite(sigma)&&(hi-lo)<0.5*sigma&&price!=null){
    const half=0.25*sigma;
    lo=Math.min(lo, price-half); hi=Math.max(hi, price+half);
  }
  const pad=s=>{const p=(s.hi-s.lo)*0.06; return {lo:s.lo-p, hi:s.hi+p};};

  for(const cand of optional){
    if(!inRadius(cand)){ exiled.push(cand); continue; }
    const t=pad({lo:Math.min(lo,cand.y), hi:Math.max(hi,cand.y)});
    const span=t.hi-t.lo;
    // A LEGIBILITY constant, not a claim about the book. Without it a pinned
    // day renders as a flat line to make room for a level nobody can act on.
    if(sessionRange!=null&&span>0&&(sessionRange/span)<MIN_RANGE_SHARE){
      refused.push(cand); continue;
    }
    lo=Math.min(lo,cand.y); hi=Math.max(hi,cand.y); admitted.push(cand);
  }
  const f=pad({lo,hi});
  return {lo:f.lo, hi:f.hi, admitted, refused, exiled};
}

function mergeLevels(levels, span){
  // Two levels on one strike is the ordinary case. On the live board of
  // 2026-08-24 the heaviest wall was ALSO the second magnet, and the nearest
  // wall was the third. A wall absorbs a magnet rather than the reverse — the
  // wall is the thing price meets, the magnet is a property of where it sits.
  if(!Array.isArray(levels)) return [];
  const tol=(span>0?span:1)*0.006;
  const order={wall:0, magnet:1, vwap:2};
  const ruled=levels.filter(l=>l.y!=null&&isFinite(l.y))
                    .sort((a,b)=>((order[a.kind]??9)-(order[b.kind]??9))||(a.y-b.y));
  const out=[];
  for(const l of ruled){
    const hit=out.find(o=>Math.abs(o.y-l.y)<=tol&&!(o.kind==='vwap'||l.kind==='vwap'));
    if(!hit){ out.push(Object.assign({}, l)); continue; }
    if(l.kind==='magnet'){
      hit.magnet=true;
      hit.magnetLead=hit.magnetLead||!!l.lead;
      if(hit.share==null) hit.share=l.share;
    } else if((l.gex||0)>(hit.gex||0)) Object.assign(hit, l);
  }
  return out;
}

function layoutLabels(desired, gap, top, bottom){
  // Rows solved, not nudged. Two passes: separate downward, walk the run back
  // up if it clears the floor, then clamp. It makes a GUARANTEE — no two rows
  // closer than `gap` — where the first version made an attempt.
  const n=desired.length;
  if(!n) return [];
  const idx=desired.map((y,i)=>({y,i})).sort((a,b)=>a.y-b.y);
  const out=new Array(n);
  let prev=-Infinity;
  for(const d of idx){ const y=Math.max(d.y, prev+gap, top); out[d.i]=y; prev=y; }
  let next=Infinity;
  for(let k=idx.length-1;k>=0;k--){
    const i=idx[k].i; out[i]=Math.min(out[i], next-gap, bottom); next=out[i];
  }
  let floor=-Infinity;
  for(const d of idx){ out[d.i]=Math.max(out[d.i], floor+gap, top); floor=out[d.i]; }
  return out;
}

/* ---- the price line ---------------------------------------------------- */

function barPoints(rows){
  // THE SPINE (2026-09-09). One-minute bars from `sndk_bars`, written every 60s
  // by its own launchd job — a job that shares no process, no import and no
  // credential with the scanner.
  //
  // WHY IT REPLACED THE DIARY. tapePoints below drew the path from the
  // scanner's own diary, which is fine only while the scanner is alive. On
  // 2026-09-09 the diary held 14 rows: 09:30 to 09:45, then nothing until
  // 15:48. The chart drew a straight line across that hole, 1737 to 1758 — and
  // the real path inside it ran to 1807.22 at 10:06. The drawn line was wrong
  // by $66.40, three quarters of the plot height, and it was wrong in the
  // most dangerous way available: confidently, with no gap and no mark.
  //
  // The bar sidecar survived that outage untouched, because the thing that
  // died was the option-chain credential and the bars come from the broker.
  // That is the whole argument for it being the spine: it is the series least
  // correlated with the failure that erases the other one.
  if(!Array.isArray(rows)) return [];
  const out=[];
  for(const b of rows){
    if(!b) continue;
    const c=_fin(b.close), t=Date.parse(b.ts);
    if(c==null||!isFinite(t)) continue;
    out.push({t, s:c});
  }
  out.sort((a,b)=>a.t-b.t);
  return out;
}

function tapePoints(rows){
  // THE FALLBACK. Diary rows carry ts + spot every couple of minutes. Used only
  // when the bar sidecar has nothing for the day — a fresh install, a bars job
  // that has not run, or a session before that job existed. Coarser and
  // vulnerable to a scanner outage (see barPoints), but a glance must draw
  // something rather than nothing.
  if(!Array.isArray(rows)) return [];
  const out=[];
  for(const r of rows){
    if(!r||r.ticker!=='SNDK') continue;
    if((r.meta||{}).forced) continue;              // off-hours warmups are not tape
    const s=_fin(r.spot), t=Date.parse(r.ts);
    if(s==null||!isFinite(t)) continue;
    out.push({t, s});
  }
  out.sort((a,b)=>a.t-b.t);
  return out;
}

function livePoint(live, nowMs){
  // Timed off `age_s`, not off `ts`. live_spot stamps `ts` only on a FRESH
  // fetch — every cached branch omits the key — and with a 2s cache against a
  // 5s poll a good share of readings are cached. Keying on `ts` left the quote
  // with no time at all.
  if(!live||_fin(live.spot)==null) return null;
  const now=(nowMs==null)?Date.now():nowMs;
  const age=(_fin(live.age_s)!=null)?Number(live.age_s)*1000:0;
  let t=now-age;
  const stamped=Date.parse(live.ts);
  if(isFinite(stamped)) t=stamped;
  return {t, s:Number(live.spot), live:true};
}

/* ---- the reader's own sentence ---------------------------------------- */

/* obs-1: the reader's own book ceiling, so the phone and the gate agree on
   when a measurement has stopped being current. */
const STALE_BOOK_MIN_UI = 6;

function modelRead(rows, nowMs){
  // Sourced by reading_ts, NEVER by ts. The store re-emits the same reading
  // every couple of minutes with a fresh `ts` while `reading_ts` stays put: on
  // the reference file the last row carries ts 15:58 and reading_ts 11:47, a
  // 251-minute reading wearing a 0-minute timestamp.
  //
  // obs-1: two tiers, not three — see the note on the return below.
  if(!Array.isArray(rows)) return null;
  let best=null;
  for(const r of rows){
    // SELECT ON THE READING, not on a field it no longer has. The body below
    // was migrated to the observation shape and this filter was not, so the
    // phone kept choosing the newest row that still carried `line` — a wk-1 row
    // hours old — and was blind to every obs-1 reading. Once those rows rolled
    // off it would have painted NO READING TODAY permanently.
    const rdg=(r||{}).reading;
    if(!rdg || (rdg.quiet!==true && !Array.isArray(rdg.points) && !rdg.read)) continue;
    const t=Date.parse(r.reading_ts);
    if(!isFinite(t)) continue;
    if(!best||t>best.t) best={t, r};
  }
  if(!best) return null;
  const now=(nowMs==null)?Date.now():nowMs;
  const age=Math.max(0,(now-best.t)/60000);
  const rd=best.r.reading||{};
  // obs-1: there is no vector any more. The read is an OBSERVATION — `say` is
  // the human sentence, `quiet` means the model looked and found nothing, and
  // "nothing unusual" is the expected answer rather than a missing one.
  //
  // The `expired` tier also goes. It expired at 120 minutes because that was
  // four times a 30-minute forecast horizon; an observation has no horizon, so
  // what makes it stale is the measurement it describes no longer being
  // current, which is the book's own ceiling.
  const pts = rd.points || [];
  const quiet = rd.quiet === true || (!pts.length && !rd.read);
  // A LEVEL ANNOTATION IS NOT A SENTENCE (2026-09-09). This used to fall back
  // to pts[0].note, so a reading that authored no prose was drawn in the
  // reading's own serif voice as though the model had written it — on
  // 2026-09-09 the phone read "15:49 · lower edge of the box in force since
  // 14:52", which is a caption on a level, in the place reserved for what the
  // model said. The note stays on screen, but attached to its level and marked
  // as such, so the surface never puts words in the model's mouth.
  const wordless = !rd.read && !!(pts[0] && pts[0].note);
  const line = String(rd.read || (wordless ? pts[0].note : '') || '')
            || 'Nothing standing out on the board.';
  return {line, quiet, wordless,
          count:pts.length,
          ageMin:age,
          at:new Date(best.t),
          tier: age>STALE_BOOK_MIN_UI ? 'aged' : 'fresh'};
}

/* ---- market time ------------------------------------------------------- */

// Every clock face on this screen is MARKET time, never the viewer's.
//
// The session is 09:30-16:00 in New York and the whole scene is stamped that
// way. Rendered in local time on a Pacific machine the 12:12 scan reads 09:12
// and the session opens at 06:31, which is not a small error: it moves every
// label on the plot three hours and invites the reader to compare a market
// event against their own wall clock.
const _ET_TIME = {hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'America/New_York'};
const _ET_DAY  = {year:'numeric', month:'2-digit', day:'2-digit', timeZone:'America/New_York'};

function etTime(ms){
  const d = (ms instanceof Date) ? ms : new Date(ms);
  if(!isFinite(d.getTime())) return null;
  return d.toLocaleTimeString('en-US', _ET_TIME);
}

function etToday(nowMs){
  // YYYY-MM-DD in New York, to compare against scene.clock.session_date — also
  // New York. Comparing it to the viewer's local date puts a Pacific reader on
  // the wrong side of the boundary for three hours every evening.
  const d = (nowMs == null) ? new Date() : new Date(nowMs);
  const p = d.toLocaleDateString('en-CA', _ET_DAY);
  return p;
}

/* ---- internals --------------------------------------------------------- */

function _fin(v){
  if(v==null) return null;
  const n=Number(v);
  return isFinite(n) ? n : null;
}

function _wall(e, side, nearest){
  // sr-7/obs-2 renames on the scene entry: gex -> cluster_share_of_book_gamma_pp,
  // unchanged_min -> unchanged_for_min, unchanged_min_at_least ->
  // unchanged_for_at_least_min. `gex` stays the INTERNAL name for the share.
  return {y:Number(e.strike), kind:'wall', side, nearest:!!nearest,
          gex:_fin(e.cluster_share_of_book_gamma_pp),
          held:(e.unchanged_for_min!=null)?_fin(e.unchanged_for_min):_fin(e.unchanged_for_at_least_min),
          heldExact:e.unchanged_for_min!=null};
}

if(typeof module!=='undefined'&&module.exports){
  module.exports={gUsd, gMinutes, envParts, FULL_SHARE, shareBarPct, wallStroke, wallPassed,
                  levelRows, lightNote, priorClose,
                  bookAge, shownPrice, dayChange, vwapPrice, railWidth,
                  etTime, etToday,
                  coreLevels, optionalLevels, magnetRunners, solveWindow, mergeLevels,
                  layoutLabels, barPoints, tapePoints, livePoint, modelRead};
}
