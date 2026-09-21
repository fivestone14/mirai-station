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
  // The unit is spelt, once: "1m" read as a month as easily as a minute.
  if(m==null||!isFinite(m)) return null;
  if(m<1) return 'just now';
  // Round ONCE, then split. Rounding the remainder separately returns 60 for
  // the last thirty seconds of every hour, so the freshness chip printed
  // "1H 60M" — repainted every 5s, so reliably visible.
  const t=Math.round(m);
  if(t<60) return t+' min';
  const h=Math.floor(t/60), r=t%60;
  return r? h+' hr '+r+' min' : h+' hr';
}

function gTimes(m){
  // A multiple. Two places under a tenth, because 0.06 at one would read 0.1
  // and overstate it by two thirds; one place through single figures, so the
  // column is one shape; none from ten up, where the decimal is noise. Every
  // form starts with a digit, which is the edge the column is read down.
  if(m==null||!isFinite(m)||m<0) return null;
  return m.toFixed(m<0.1 ? 2 : m<9.95 ? 1 : 0)+'×';
}

/* ---- walls ------------------------------------------------------------ */

function wallPassed(side, strike, price){
  // Has price, AS SHOWN, gone past this wall since the book was read?
  //
  // walls_ladder files a wall against the book's spot, up to a couple of
  // minutes old, while the price on screen repaints every 5s. Replayed over 8
  // sessions, price stood beyond a wall the screen still showed on 2.7% of
  // minutes (5.8% on 09-10). The wall is relabelled at the next scan — on
  // every crossing on record — so until then the honest thing is to say price
  // has passed it and drop its colour: a call wall below price is not the call
  // side any more, and this screen teaches green as the call side.
  const k=_fin(strike), p=_fin(price);
  if(k==null||p==null) return false;
  return side==='call' ? p>k : side==='put' ? p<k : false;
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


/* ---- weight ------------------------------------------------------------ */


/* ---- level assembly ---------------------------------------------------- */

function _expectedMove(scene, side){
  // How far the day is expected to carry from here, on the side the level sits.
  // Asymmetric because the scene measures it that way, and it DECAYS through
  // the session: at 09:30 it is the whole day's room, at 15:59 it is a minute's.
  // Absent means absent — the caller keeps its old behaviour rather than
  // inventing a distance.
  const e=(((scene||{}).scale||{}).expected_move_today_asym)||null;
  if(!e) return null;
  return _fin(side==='call'?e.up_dollars:e.down_dollars);
}

function _wallAnchors(scene, price, side){
  // Is this wall one price could actually meet today?
  //
  // The window used to make room for the nearest wall on each side whatever the
  // distance, subject only to the 1.75-sigma exile radius — $115 on 2026-09-16,
  // which let the call wall 83 dollars above price anchor the chart and left the
  // day's own $47.33 of movement drawing inside 42% of the plot. The intent was
  // right (the level price meets next must not vanish) and the reach was not:
  // a wall five expected moves away is not a level price meets next.
  //
  // So the test is the day's own room rather than a multiple of a typical day's.
  // A wall inside it is anchored as before. A wall outside it is not dropped —
  // it goes to the optional set, faces the legibility test with everything else,
  // and is NAMED at the edge when it does not fit. Nothing is silently dropped;
  // that is the 2026-08-24 bug and it stays fixed.
  const w=(((scene||{}).walls||{})[side]||[])[0];
  if(!w||w.strike==null) return false;
  const move=_expectedMove(scene, side);
  if(move==null||!(move>0)) return true;      // no datum: the old behaviour
  if(price==null||!isFinite(price)) return true;
  return Math.abs(Number(w.strike)-price)<=move;
}

function coreLevels(scene, price, vwap, points){
  // Always admitted, each still subject to the exile radius.
  const out=[], p=(scene||{}).price||{}, w=(scene||{}).walls||{};
  if(price!=null) out.push({y:price, kind:'price'});
  if(_fin(p.session_low)!=null)  out.push({y:p.session_low,  kind:'session'});
  if(_fin(p.session_high)!=null) out.push({y:p.session_high, kind:'session'});
  for(const pt of (points||[])) out.push({y:pt.s, kind:'path'});
  // Anchored only while price could actually reach it today; otherwise the wall
  // is handed to optionalLevels() below, which is where it earns its place.
  const c=(w.call||[])[0], u=(w.put||[])[0];
  if(c&&c.strike!=null&&_wallAnchors(scene,price,'call')) out.push(_wall(c,'call',true));
  if(u&&u.strike!=null&&_wallAnchors(scene,price,'put'))  out.push(_wall(u,'put',true));
  // sr-7 reshape: top_strikes entries are {strike, share_of_book_gamma_pp}
  // dicts now, not [strike, share] pairs
  const mag=((scene||{}).magnet||{}).top_strikes;
  if(Array.isArray(mag)&&mag.length&&mag[0]&&_fin(mag[0].strike)!=null)
    out.push({y:Number(mag[0].strike), kind:'magnet', lead:true,
              share:_fin(mag[0].share_of_book_gamma_pp), weight:1});
  if(vwap!=null) out.push({y:vwap, kind:'vwap'});
  return out;
}

function optionalLevels(scene, price){
  // Tried one at a time, HEAVIEST FIRST, each subject to the admission test.
  const out=[], w=(scene||{}).walls||{};
  for(const side of ['call','put']){
    // the nearest wall, when it is too far for the day to reach and so was not
    // anchored by coreLevels(). It arrives here rather than nowhere: refused, it
    // still becomes a named edge marker.
    const n=(w[side]||[])[0];
    if(n&&n.strike!=null&&!_wallAnchors(scene,price,side)) out.push(_wall(n,side,true));
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

/* ---- the gutter's prices, and the ruler between them ------------------- */

// Advances in the shipped face (pjs-153fc85b7029.woff2), in em, read off its
// hmtx with the tnum substitution applied — which is what the chart draws,
// since every chart label restates tabular-nums. A tabular figure is 0.600em
// at every weight; the comma and the point are not tabular and grow with the
// weight, so a price measured at one weight is wrong at another. Anything
// else is charged a figure's width.
const _FIG_EM=0.6;
const _SEP_EM={500:{',':0.299, '.':0.317}, 600:{',':0.328, '.':0.347},
               700:{',':0.357, '.':0.378}, 800:{',':0.386, '.':0.408}};

function figW(s, px, weight){
  // an unlisted weight is charged the heaviest, so a guess can only be wide
  const sep=_SEP_EM[weight]||_SEP_EM[800];
  let em=0;
  for(const ch of String(s)) em+=(sep[ch]!=null)?sep[ch]:_FIG_EM;
  return em*px;
}

// The finest ROUND step whose pitch two 11px numbers can sit at, so the ruler
// counts in steps a reader already counts in, and a $10 board and a $7,000
// board run the same arithmetic. 1, 2 and 5 only: a 2.5 step at whole dollars
// prints 1,502.5 as "1,503", and on a $5 strike grid it never lands on a
// strike anyway. 20, not 18, since the chart's type went to 11px (2026-09-18).
// It costs rungs where a span falls between the two floors: a $10 step fits
// $72.78 on a 131px plot at 18 and $65.50 at 20, and the $67.85 board of
// 2026-09-16 goes to $20 there (SIDE-SPEC.md 6a).
const AXIS_NICE=[1, 2, 5, 10], AXIS_MIN_PX=20;
// A rung this near a tag ROW is dropped. The chip is 18px tall, so 17 leaves
// 4px of white between it and a grey number; and tag rows in a run sit 20px
// apart, so a rung between two of them is at most 10px from one — the ruler
// can never squeeze into a run of tags.
const AXIS_CLEAR=17;

function axisStep(span, plotH){
  if(!(span>0)||!(plotH>0)) return null;
  const want=AXIS_MIN_PX*span/plotH;
  const dec=Math.pow(10, Math.floor(Math.log10(want)));
  const step=AXIS_NICE.map(m=>m*dec).find(s=>s>=want*(1-1e-9));
  // the step decides the decimals, so a 20-cent ruler cannot print 9 · 9 · 9
  return {step, dp:Math.max(0, Math.ceil(-Math.log10(step)-1e-9))};
}

function priceTicks(lo, hi, top, bottom, tags){
  // Every multiple of the step inside the window, on the plot's own y, minus
  // any rung something better already names: a tag within half a step of it
  // (the tag says it in its hue, at 12px, tied to its true height), a tag row
  // closer than AXIS_CLEAR, or a glyph box that would leave the plot band. The
  // ruler fills the silence between named prices and never adds to a crowd.
  // `tags` is [{v, row}]: each tag's price and the row it was solved onto.
  const a=axisStep(hi-lo, bottom-top);
  if(!a) return [];
  const k=(bottom-top)/(hi-lo), out=[];
  const i1=Math.floor(hi/a.step+1e-9);
  for(let i=Math.ceil(lo/a.step-1e-9); i<=i1; i++){
    const v=i*a.step, y=top+(hi-v)*k;
    if(y<top+4||y>bottom-4) continue;
    if((tags||[]).some(t=>Math.abs(v-t.v)<a.step/2||Math.abs(y-t.row)<AXIS_CLEAR)) continue;
    out.push({v, y, label:gUsd(v, a.dp).replace('$','')});
  }
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

/* ---- where contracts traded today -------------------------------------- */

// Each strike's puts grow LEFT from a zero TRADED_ZERO of the plot's width in
// from its left edge, and its calls RIGHT, on one scale: the longest single
// side in view reaches TRADED_SIDE of the plot, what one grey bar of calls and
// puts together reached before, so a pair spans 40% at most. The owner's
// choice of 2026-09-19, placed by a tally of what the bars ran into over the
// 504 boards with bars of 2026-09-15..17 (CPB-SPEC.md 8.2). At 360:
//   - left of 0.65 they run into TRADING PICKED UP, which sits at the plot's
//     left: at 0.55 it leaves its brackets on 128 boards and has the count
//     under it on 91;
//   - from 0.75 the calls reach the live dot's ring (158 boards at 0.75, 292
//     at 0.80) and the brackets' right corners (35, 77);
//   - 0.72 is the one place that also holds at 320: the word out of its
//     brackets on 5 boards and the count stacked with it on 5, against 34 and
//     17 for the single grey bar;
//   - 25% a side reaches the ring from 0.70 (156 boards).
// The bars then stand under older price, not beside "now"; the key says where
// they sit is not a time of day.
const TRADED_ZERO=0.72, TRADED_SIDE=0.20;
// A bar is this share of the tightest strike pitch in view, never more than
// 8px. The lane's fixed 8px left 0.25px between 1,540, 1,545 and 1,550 on a
// 112px plot and the three read as one block; a share of the pitch keeps the
// rest of it white whatever the plot's height or the window's span.
const TRADED_PITCH=0.70, TRADED_H_MAX=8;
// The count names the longest single bar and its side, "3,861 PUTS" or "2,704
// CALLS": 11px/600, its figures figW's and the word after them measured in
// WebKit with the shipped face, " CALLS" 37.05 and " PUTS" 29.83 (CPB-SPEC.md
// 2.6; "6,104 CALLS" is 67.06).
const COUNT_CALLS_W=37.05, COUNT_PUTS_W=29.83;

function tradedBars(strikes, lo, hi, top, bottom, hMax){
  // Contracts traded today at each strike in the window, calls and puts apart:
  // `vc` and `vp`. `most` is the longest single side in view, the one scale
  // both sides are drawn to, and `lead` the bar and side holding it, {b, n,
  // call}, whose count the chart prints: the one number that gives the scale,
  // which a total of both sides would not. On a tie the higher strike leads,
  // and its calls before its puts.
  //
  // Honest-absent twice. A row missing either column gets no bar, never a
  // zero-length stub. A book with neither column anywhere gets none at all:
  // the builder withholds both while the day's first book still carries the
  // prior session's counts (sndk_board's WITHHELD_* notes): the first two scans
  // of 09-16 and 09-17, the first six (to 09:42) of 09-15.
  //
  // `hMax` is the thickest a bar may be, TRADED_H_MAX unless the caller has
  // more room than the card: the full screen view's 14. It has to be the cap
  // the thickness is solved at rather than a stretch afterwards, because the
  // bars that would cross the plot's edge are the ones dropped below.
  const cap=_fin(hMax)>0?_fin(hMax):TRADED_H_MAX;
  const k=(bottom-top)/(hi-lo);
  if(!(k>0)||!isFinite(k)) return null;
  const seen=[];
  for(const r of (((strikes||{}).rows)||[])){
    const v=_fin(r&&r.strike), vc=_fin(r&&r.vol_calls), vp=_fin(r&&r.vol_puts);
    if(v==null||vc==null||vp==null||v<lo||v>hi) continue;
    seen.push({v, vc, vp, y:top+(hi-v)*k});
  }
  if(!seen.length) return null;
  seen.sort((a,b)=>b.v-a.v);
  let pitch=Infinity;
  for(let i=1;i<seen.length;i++) pitch=Math.min(pitch, seen[i].y-seen[i-1].y);
  const h=Math.min(cap, TRADED_PITCH*pitch);
  // a bar that would cross the plot's edge is dropped, not clipped: a clipped
  // bar's middle is no longer its strike's price
  const bars=seen.filter(b=>b.y-h/2>=top&&b.y+h/2<=bottom);
  if(!bars.length) return null;
  let lead=null;
  for(const b of bars) for(const [n, call] of [[b.vc, true], [b.vp, false]])
    if(!lead||n>lead.n) lead={b, n, call};
  return {h, most:lead.n, lead, bars};
}

// WHAT TRADED SINCE THE LATEST READING, calls and puts apart (CPB-SPEC.md 1).
// The payload's since_read carries each listed strike's calls and puts in the
// book the latest reading was written from (snapshot._since_read); now minus
// then is what each side's paler outer end holds. It starts from nothing at
// every reading, so it is usually short: over the boards of 2026-09-15..17
// the longest paler end on a board was 2.7px at the median at 360, and 3px or
// more on 46% of them. Honest-absent, never a guessed end:
//  - no field, or the station says why not: no reading yet today, no new book
//    since it, a book still carrying the prior session's counts;
//  - a field for another reading than the one "What it means" shows (the
//    card chooses among the read rows the phone fetched; 09-15 13:23);
//  - a strike the field leaves out: not in the book at the reading, or its
//    count went backwards within five books;
//  - a count lower now than at the reading, a vendor revision: that strike
//    gets no paler end on either side.
function tradedSince(field, reads, strikes){
  // -> {at, by: {strike: [calls since, puts since]}}, or null for no paler ends
  if(!field||!Array.isArray(field.rows)) return null;
  const m=modelRead(reads), at=Date.parse(field.read_at);
  if(!m||!isFinite(at)||m.at.getTime()!==at) return null;
  const then={};
  for(const r of field.rows){
    const k=_fin(r&&r[0]), c=_fin(r&&r[1]), p=_fin(r&&r[2]);
    if(k!=null&&c!=null&&p!=null) then[k]=[c, p];
  }
  const by={};
  for(const r of (((strikes||{}).rows)||[])){
    const k=_fin(r&&r.strike), vc=_fin(r&&r.vol_calls), vp=_fin(r&&r.vol_puts), t=then[k];
    if(k==null||vc==null||vp==null||!t) continue;
    if(vc<t[0]||vp<t[1]) continue;
    by[k]=[vc-t[0], vp-t[1]];
  }
  return {at, by};
}

/* ---- the chart full screen --------------------------------------------- */

// THE CORNER CONTROL IN THE CARD'S HEAD IS WHAT OPENS IT, by the owner's
// decision of 2026-09-19, which overturns the tap of ZOOM-SPEC.md 5: a tap on
// the chart itself does nothing, so there is no tap here to tell apart from
// the scroll and the pull-to-refresh that share this glass. What a finger on
// the chart can still arm is a hold or a sideways read, and touchKind below is
// the whole of that rule.
//
// THE VIEW'S CHART WRITES NOTHING ON ITS BARS, by the owner's decision of
// 2026-09-20 (FULL2-SPEC.md 4). Numbers past both ends of every bar were the
// view's whole point until then; they went because the chart gives up height
// to a table of every listed price, and a figure printed in both places sets
// a reader checking one against the other. What the chart keeps is the shape,
// and the glance's one count on the longest bar with it.

/* ---- where new contracts arrived --------------------------------------- */

// A price area's share of the contracts newly traded across the whole board in
// the last NEW_TAIL books (about nine minutes), against its own median share of
// each earlier book in the window. Both terms are contracts, and the
// denominator is the whole board, so a market-wide lull or burst moves both
// together and says nothing: what survives is the flow moving to one place.
// Size is not change: the bars say how much traded all day, this says where the
// last nine minutes went. CHANGE-SPEC.md 2 and 3, off 2026-09-16:
//   NEW_LIFT     6 points of the board's new contracts. The median lift is
//                +0.4 and the 90th percentile +7.2.
//   NEW_SHARE    and a tenth of them now, so a strike cannot flag by climbing
//                from nothing to next to nothing.
//   NEW_FLOOR    60 contracts at the strike, and NEW_BOARD 250 on the whole
//                board: a big share of nothing is not change. Four boards of
//                09-16 were quiet by this alone; 12:44 would have lit 1,520
//                for 56 contracts.
//   NEW_BASE     4 earlier books of the strike's own, or there is no baseline
//                to be lifted from. A window needs 7 books for that, so the
//                spec's floor of 6 books in the window never decides anything
//                and is not here.
//   NEW_MERGE    flagged strikes 10 points apart or less are ONE area: 1,500,
//                1,510 and 1,520 lighting together is one thing happening.
//   NEW_CAP      2 areas, the biggest lift first, and the rest counted. The
//                24 scans of 09-16 the model read never had a third after the
//                merge; 18 of the phone's 514 boards of 09-15..17 did.
const NEW_TAIL=2, NEW_BASE=4, NEW_LIFT=6.0, NEW_SHARE=10.0,
      NEW_FLOOR=60, NEW_BOARD=250, NEW_MERGE=10, NEW_CAP=2;

function newContracts(strikes, frames){
  // -> {areas, more} or null. Each area: `strikes` flagged, low to high; `lo`
  // and `hi`, the price it spans, half the way to the next listed strike either
  // side (never more than half the board's usual step); `n` contracts in the
  // last NEW_TAIL books and `share` of the board's, against `base` earlier, all
  // summed over its strikes. `more` counts the areas past the cap.
  //
  // Honest-absent: a strike whose series is not the window's length is not
  // measured, and a null inside one is a book the strike was not listed in,
  // left out of its baseline, never counted as a book at 0%. Nothing
  // qualifies, nothing is returned.
  const nb=_fin(frames&&frames.books_in_series);
  if(nb==null) return null;
  const nd=nb-1, series=[], listed=[];
  for(const r of (((strikes||{}).rows)||[])){
    const k=_fin(r&&r.strike);
    if(k==null) continue;
    listed.push(k);
    const s=r.vol_added_per_book;
    if(Array.isArray(s)&&s.length===nd) series.push({k, s:s.map(_fin)});
  }
  if(!series.length) return null;
  const tot=new Array(nd).fill(0);
  for(const {s} of series) s.forEach((v, j)=>{ if(v!=null) tot[j]+=v; });
  const board=tot.slice(-NEW_TAIL).reduce((a, b)=>a+b, 0);
  if(board<NEW_BOARD) return null;
  const flags=[];
  for(const {k, s} of series){
    const n=s.slice(-NEW_TAIL).reduce((a, v)=>a+(v||0), 0);
    const was=[];
    for(let j=0;j<nd-NEW_TAIL;j++) if(s[j]!=null&&tot[j]>0) was.push(100*s[j]/tot[j]);
    if(was.length<NEW_BASE) continue;
    was.sort((a, b)=>a-b);
    const m=was.length>>1, base=was.length%2 ? was[m] : (was[m-1]+was[m])/2;
    const share=100*n/board;
    if(share-base>=NEW_LIFT&&share>=NEW_SHARE&&n>=NEW_FLOOR) flags.push({k, n, share, base});
  }
  if(!flags.length) return null;
  flags.sort((a, b)=>a.k-b.k);
  const groups=[];
  for(const f of flags){
    const g=groups[groups.length-1];
    if(g&&f.k-g[g.length-1].k<=NEW_MERGE) g.push(f); else groups.push([f]);
  }
  // an area's edges, half the way to its listed neighbours
  listed.sort((a, b)=>a-b);
  const steps=listed.slice(1).map((k, i)=>k-listed[i]).filter(d=>d>0).sort((a, b)=>a-b);
  const half=steps.length ? steps[steps.length>>1]/2 : null;
  const edge=(k, dir)=>{
    const next=dir>0 ? listed.find(x=>x>k) : [...listed].reverse().find(x=>x<k);
    const d=[next!=null ? Math.abs(next-k)/2 : null, half].filter(v=>v!=null);
    return k+dir*(d.length ? Math.min(...d) : 0);
  };
  const sum=(g, key)=>g.reduce((a, f)=>a+f[key], 0);
  const areas=groups.map(g=>{
    const share=sum(g, 'share'), base=sum(g, 'base');
    return {strikes:g.map(f=>f.k), lo:edge(g[0].k, -1), hi:edge(g[g.length-1].k, 1),
            n:sum(g, 'n'), share, base, lift:share-base, board};
  }).sort((a, b)=>b.lift-a.lift);
  return {areas:areas.slice(0, NEW_CAP), more:Math.max(0, areas.length-NEW_CAP)};
}

/* ---- where new contracts arrived, marked on the plot ------------------- */

// CHANGE-SPEC.md 5.1: each corner a 16px arm and a 5.5px leg, 1.5px thick. A
// box under 13px tall is grown about its middle, so a one-strike area can be
// found and can hold its word: 8.6px of ink and a pixel of air inside each
// arm. At the spec's 11 the word left its box on 110 to 120 of the 335 boxed
// boards of 2026-09-15..17, by phone; at 13 on 0 to 14, each of them the
// longer "· 1 MORE" form crossing a bar.
const NEW_ARM=16, NEW_LEG=5.5, NEW_W=1.5, NEW_MIN_H=13;
// The word, 11px/700 in the shipped face, measured in WebKit: "TRADING PICKED
// UP" is 109.09 and " · 1 MORE" after it 49.90. Bold and in the brackets' ink,
// because it names them and they mean just now, where the count on the bars is
// the whole day's, in theirs (READABLE2-SPEC.md 1). It carries no share: the
// share's denominator, every contract traded on the board in the last two
// books, is on no screen. Its capitals run 8.4px above the baseline and 0.2
// below it.
const NEW_WORD=109.09, NEW_MORE=49.90, WORD_UP=8.4, WORD_DOWN=0.2;
// Further than this off its box, the word would label something else, so it is
// not drawn and the corners and the tab mark the place alone. On the 514
// boards of 2026-09-15..17 it has not come to that at any phone.
const WORD_OFF_MAX=8;

function newBox(t0, b0, inks, top, bottom){
  // -> {t, b}, the rows of the corner arms round an area the plot draws from
  // t0 down to b0, inside top..bottom. `inks` is every rule's [y, half its
  // width].
  //
  // An arm on a rule reads as the rule doubled, so each arm keeps 2.5px off
  // every rule's ink. It gets there moving OUTWARD, 4px at most; coming in over
  // the area it marks is the worse misstatement and costs twice as much, and
  // is taken only where the plot's edge or the 4px stops the outward move, so
  // a box pinned to the edge or grown to its least height can shift clear.
  // Where rules run closer together than any move of 4px can clear (72 of the
  // 1,532 arms on 514 boards of 09-15..17), the arms take the moves that keep
  // the nearer of them furthest off the ink: 0.65px at the least, never on it.
  if(b0-t0<NEW_MIN_H){
    t0=Math.min(Math.max((t0+b0-NEW_MIN_H)/2, top), bottom-NEW_MIN_H);
    b0=t0+NEW_MIN_H;
  }
  t0=Math.max(t0, top); b0=Math.min(b0, bottom);
  const offInk=y=>Math.min(2.5, ...inks.map(([ry, hw])=>Math.abs(y-ry)-hw-NEW_W/2));
  const moves=[0];
  for(let d=0.25;d<=4;d+=0.25) moves.push(-d, d);
  let best={t:t0, b:b0, off:-Infinity, cost:Infinity};
  for(const dt of moves) for(const db of moves){
    const t=t0+dt, b=b0+db, cost=(dt<0 ? -dt : 2*dt)+(db>0 ? db : -2*db);
    if(t<top||b>bottom||b-t<NEW_MIN_H) continue;
    const off=Math.min(offInk(t), offInk(b));
    if(off>best.off||(off===best.off&&cost<best.cost)) best={t, b, off, cost};
  }
  return {t:best.t, b:best.b};
}

function wordRow(t, b, soft, hard, top, bottom){
  // The baseline for the word on the area boxed from t to b, or null. `soft`
  // and `hard` are [top, bottom] spans across the word's width: the ink of
  // rules, which its halo may cut, and what it may never touch (text, bars,
  // the dot's ring), each with the air it must keep. First the tallest stretch
  // of the box clear of both, the word centred in it: a word laid across a
  // rule reads as that rule's label, one beside it as the box's (CHANGE-SPEC.md
  // 5.5). Then the stretch clear of what it may not touch nearest the box's
  // middle, over the rules. Then the room just outside the box on whichever
  // side is nearer, below on a tie, inside top..bottom and no more than
  // WORD_OFF_MAX off: a word out of its box is worse than one over a rule, and
  // better than one over other text. Below first put it 3.4 to 23px off its
  // brackets on 15 boxed boards at 320, under the count, where it read as the
  // count's caption.
  const need=WORD_UP+WORD_DOWN;
  const free=(lo, hi, spans)=>{
    const out=[];
    let at=lo;
    for(const [a, z] of spans.slice().sort((p, q)=>p[0]-q[0])){
      if(a>at) out.push([at, Math.min(a, hi)]);
      at=Math.max(at, z);
      if(at>=hi) break;
    }
    if(at<hi) out.push([at, hi]);
    return out.filter(([a, z])=>z-a>=need);
  };
  const lo=t+NEW_W/2+1, hi=b-NEW_W/2-1, mid=(t+b)/2;
  const clear=free(lo, hi, soft.map(([a, z])=>[a-1, z+1]).concat(hard));
  if(clear.length){
    const [a, z]=clear.reduce((p, q)=>(q[1]-q[0]>p[1]-p[0] ? q : p));
    return (a+z+WORD_UP-WORD_DOWN)/2;
  }
  const at=([a, z])=>Math.min(Math.max(mid+(WORD_UP-WORD_DOWN)/2, a+WORD_UP), z-WORD_DOWN);
  const over=free(lo, hi, hard);
  if(over.length) return over.map(at).reduce((p, q)=>(Math.abs(q-mid)<Math.abs(p-mid) ? q : p));
  const below=free(b+NEW_W/2+1, bottom, hard), above=free(top, t-NEW_W/2-1, hard);
  const out=[];
  if(below.length) out.push([below[0][0]+WORD_UP, below[0][0]-(b+NEW_W/2+1)]);
  if(above.length) out.push([above[above.length-1][1]-WORD_DOWN, (t-NEW_W/2-1)-above[above.length-1][1]]);
  const near=out.filter(([, off])=>off<=WORD_OFF_MAX).sort((p, q)=>p[1]-q[1]);
  return near.length ? near[0][0] : null;
}

// A pick-up the plot cannot show is named in the edge stack on its side in the
// brackets' own words, "↑ ABOVE, AT 1,600: TRADING PICKED UP". 11px/500 with
// the phrase at 700, measured in WebKit: "↑ ABOVE, AT " 68.07, "↓ BELOW, AT "
// 70.18, "↑ AT " 24.21, the dash of a range 6.57, ": TRADING PICKED UP" 114.47
// and " · 1 MORE" 48.63. The prices are figW's.
const EDGE_ABOVE=68.07, EDGE_BELOW=70.18, EDGE_AT=24.21, EDGE_DASH=6.57, EDGE_PICKED=114.47,
      EDGE_MORE=48.63;

function pickedRow(up, strikes, more, room){
  // -> {lead, tail}: the words before the phrase and after it, in the longest
  // form whose width fits `room`. The side is said in words; wider than the
  // room it goes back to its arrow alone, and past that the count of places
  // with no mark of their own goes, never the price. The row names the strike
  // or the range of strikes, never a midpoint between them.
  const k=strikes.map(v=>gUsd(v, 0).replace('$',''));
  const at=k[0]+(k.length>1 ? '–'+k[k.length-1] : '');
  const atW=k.length>1 ? figW(k[0], 11, 500)+EDGE_DASH+figW(k[k.length-1], 11, 500) : figW(k[0], 11, 500);
  const tail=more ? ' · '+more+' MORE' : '';
  const tailW=more ? EDGE_MORE-figW('1', 11, 500)+figW(String(more), 11, 500) : 0;
  const forms=[[up ? '↑ ABOVE, AT ' : '↓ BELOW, AT ', up ? EDGE_ABOVE : EDGE_BELOW, tail, tailW],
               [up ? '↑ AT ' : '↓ AT ', EDGE_AT, tail, tailW],
               [up ? '↑ AT ' : '↓ AT ', EDGE_AT, '', 0]];
  const fit=forms.find(([, hw, , tw])=>hw+atW+EDGE_PICKED+tw<=room)||forms[forms.length-1];
  return {lead:fit[0]+at, tail:fit[2]};
}

/* ---- how busy each stretch was ----------------------------------------- */

// A FIXED scale. Scaled to the day's own maximum, the opening block alone runs
// many times the median and most of the session draws at under a pixel — 27 of
// 69 blocks on 2026-09-16.
// 29,000 shares a minute is the 90th percentile of the 1,170 five-minute blocks
// in the sessions of one-minute bars on disk (p50 10,153, p95 42,812). A busier
// block is CLIPPED and the clip is marked, never quietly flattened.
const FULL_VOL_PER_MIN=29000.0;

function volumeBlocks(bars, minutes){
  // Whole blocks only. A part-block at the live edge is a smaller sample drawn
  // on the same gauge as a full one, which reads as a lull that is really just
  // a minute that has not finished yet.
  if(!Array.isArray(bars)||!bars.length) return [];
  const n=(minutes>0?minutes:5), out=[];
  let cur=null;
  for(const b of bars){
    const t=Date.parse(b&&b.ts), v=_fin(b&&b.volume);
    if(!isFinite(t)||v==null) continue;          // no datum, no block
    if(!cur||cur.rows>=n){ cur={t0:t, t1:t, sum:0, rows:0}; out.push(cur); }
    cur.t1=t; cur.sum+=v; cur.rows++;
  }
  while(out.length&&out[out.length-1].rows<n) out.pop();
  // `sum` rides along because the block's own height cannot be read back as a
  // number: the bar is a share of a fixed scale and anything past it is capped.
  // A finger on the strip reads the shares themselves (chartAt), and summing
  // the rows again there would be a second copy of the rule for which minutes
  // are in which block.
  return out.map(b=>({t0:b.t0, t1:b.t1, sum:b.sum,
                      weight:Math.min(1, (b.sum/b.rows)/FULL_VOL_PER_MIN),
                      capped:(b.sum/b.rows)>FULL_VOL_PER_MIN}));
}

// The chart's feet are .p-axis, 11px/500 tracked .12em: each letter's advance,
// measured in WebKit off the shipped face, and 1.32px of tracking between each
// two letters (getComputedTextLength leaves the tracking out, and the drawn
// box has none after the last letter). A letter not listed is charged the
// widest listed, so a guess can only be wide.
const _AXIS_ADV={'0':6.6, '1':6.6, '2':6.6, '3':6.6, '4':6.6, '5':6.6, '6':6.6, '7':6.6, '8':6.6, '9':6.6,
                 ' ':1.90, ':':3.48, A:7.39, C:8.57, D:8.15, E:6.67, F:6.41, H:8.09, I:2.90, L:5.89,
                 M:9.40, N:8.11, O:9.66, R:7.12, S:7.11, T:5.74};
const AXIS_TRACK=1.32;

function axisW(s){
  const t=String(s);
  let w=0;
  for(const ch of t) w+=(_AXIS_ADV[ch]!=null) ? _AXIS_ADV[ch] : 9.66;
  return w+Math.max(0, t.length-1)*AXIS_TRACK;
}

// The strip is shares of SNDK traded, not options, and it said nothing of
// itself: it is named on the feet's row, between them, in the long form
// wherever that keeps STRIP_AIR from each foot and the short one where only
// that does. SHARES TRADED is 104.45 and SHARES 50.09. The room is least
// under a long countdown, "3 HR 58 MIN LEFT" beside "09:30": 128.3px at 360,
// where the long name fits, and 88.3 at 320, where only the short one does
// (READABLE2-SPEC.md 2.6).
const STRIP_NAMES=['SHARES TRADED', 'SHARES'], STRIP_AIR=10;

function stripName(room){
  // -> the longest name that keeps its air in `room`, the px between the feet,
  // or null
  return STRIP_NAMES.find(s=>axisW(s)+2*STRIP_AIR<=room)||null;
}

/* ---- a finger on the chart --------------------------------------------- */

// At the owner's 360px the plot is 5.2cm wide, a bar 1.1mm thick and the paler
// end that says what traded since the reading half a millimetre of it
// (ZOOM-SPEC.md 1). Bar thickness is a height, so the taller chart fixed it;
// a length is a width, set by the phone, and no height ever will. So the chart
// answers a finger: HELD still it magnifies what is under it, dragged SIDEWAYS
// it reads the price line at that minute. Anything else is the page's own
// scroll, which is never taken.
//
// The rules are here, with no DOM in them: page.js listens and draws.

// A hold is 250ms inside 8px. Android's own long press is 400ms and its touch
// slop 8dp (ViewConfiguration); TradingView's charts arm a long tap at 240ms
// and cancel it at 5px. 250 is deliberately at the short end of that: stock
// Android abandons its Back swipe once a finger has held 250ms, so a gesture
// that arms at 250 survives at the screen's edges, where one that armed on
// movement would be taken for Back instead (ZOOM-RESEARCH.md 3.4).
const HOLD_MS=250, TOUCH_SLOP=8;

function touchKind(dx, dy, ms){
  // What a finger on the chart is doing, from how far it has moved and how
  // long it has been down: 'hold' magnifies, 'read' reads the price line,
  // 'scroll' is the page's and is never taken, 'wait' is not decided yet.
  //
  // MOVEMENT DECIDES FIRST, so a flick can never become a hold however long
  // the finger stays down afterwards, and a scroll is never stolen from the
  // page. The caller stops asking once one of the two has armed.
  if(!isFinite(dx)||!isFinite(dy)||!isFinite(ms)) return 'wait';
  if(Math.hypot(dx, dy)>TOUCH_SLOP) return Math.abs(dx)>Math.abs(dy)?'read':'scroll';
  return ms>=HOLD_MS?'hold':'wait';
}

// The magnified window and the readout under it. 224 x 112 CSS px shows
// 90 x 45 px of chart at 2.5x, where an 11px label is redrawn at 27.5 and a
// 2.5px paler end at 6. Redrawn, not blown up: page.js gives the window a
// viewBox onto the chart's own drawing, so every bar, stripe and letter in it
// is as crisp as the chart.
const LENS_ZOOM=2.5, LENS_W=224, LENS_H=112, LENS_H_MIN=64;
// Its bottom edge sits this far above the touch point, 7.8mm: the touch point
// is the middle of the pad and the fingertip runs some 20px above it. Android's
// own magnifier keeps 18dp and Shift about 46px.
const LENS_LIFT=40;
const LENS_EDGE=8;               // and it keeps this far inside the screen
const LENS_DOT=12;               // this close to the latest price's dot, the lens names the price too

function lensBox(cx, cy, readH, vw, vh){
  // Where the lens goes for a finger at (cx, cy) with a readout readH tall:
  // -> {left, top, h, where}, h the magnified window's height.
  //
  // ABOVE the finger, centred on it and kept on screen, because above is the
  // one place the hand is not. Near the top of the screen the window gives up
  // height first, down to LENS_H_MIN, which keeps the lens above the finger
  // over 94% of the chart with the page at the top and 91% at the old height
  // (ZOOM-SPEC.md 3). Only then BESIDE it, on the roomier side, and last
  // BELOW it, where the hand is.
  const cl=(v, a, b)=>Math.min(Math.max(v, a), b);
  const left=cl(cx-LENS_W/2, LENS_EDGE, vw-LENS_W-LENS_EDGE);
  const room=cy-LENS_LIFT-LENS_EDGE;
  if(room>=readH+LENS_H_MIN){
    const h=Math.min(LENS_H, room-readH);
    return {left, top:cy-LENS_LIFT-h-readH, h, where:'above'};
  }
  const tall=LENS_H+readH, toRight=cx<=vw/2;
  const beside=toRight?vw-cx-LENS_LIFT-LENS_EDGE:cx-LENS_LIFT-LENS_EDGE;
  if(beside>=LENS_W)
    return {left:toRight?cx+LENS_LIFT:cx-LENS_LIFT-LENS_W,
            top:cl(cy-tall/2-16, LENS_EDGE, vh-tall-LENS_EDGE), h:LENS_H, where:'beside'};
  return {left, top:cl(cy+LENS_LIFT+24, LENS_EDGE, vh-tall-LENS_EDGE), h:LENS_H, where:'below'};
}

function barAt(bars, y){
  // The bar nearest a height on the plot. Every height belongs to one of them,
  // so the lens names what the finger is closest to rather than nothing at
  // all — and it outlines the bar it named, so which one is never guessed at.
  if(!Array.isArray(bars)||!bars.length||!isFinite(y)) return null;
  return bars.reduce((a, b)=>Math.abs(b.y-y)<Math.abs(a.y-y)?b:a);
}

function volumeBlockAt(vol, x){
  // The five-minute block of shares under a point across the chart, or null
  // where none is drawn: before the first, after the last, and in the gutter a
  // detached quote's dot takes.
  if(!Array.isArray(vol)||!isFinite(x)) return null;
  return vol.find(b=>x>=b.x0-0.5&&x<=b.x1+0.5)||null;
}

function chartAt(geo, x, y){
  // What is under a finger at (x, y) on the chart, as facts rather than a
  // sentence: page.js writes them. `geo` is the geometry paintLadder leaves.
  //
  // Honest-absent, four ways, and every one of them is reachable: a book the
  // builder withheld draws no bars at all ('nostrike'); past the last block
  // there is no five-minute count ('noblock'); before the day's first reading
  // there is nothing to count since (at == null); and a reading that never
  // listed this strike cannot say what traded there since (since == null). A
  // count that was measured at zero is a zero.
  if(!geo) return null;
  const price=(geo.live&&Math.hypot(x-geo.live.x, y-geo.live.y)<=LENS_DOT)
            ?{v:geo.live.v, t:geo.live.t}:null;
  if(y>geo.bottom){
    const block=volumeBlockAt(geo.vol, x);
    return block?{kind:'shares', block, price}:{kind:'noblock', price};
  }
  const bar=barAt(geo.bars, y);
  if(!bar) return {kind:'nostrike', price};
  const d=geo.since?geo.since.by[bar.v]:null;
  return {kind:'strike', bar, price, at:geo.since?geo.since.at:null,
          since:d?{c:d[0], p:d[1]}:null};
}

// A price more than three minutes from the finger's own minute is not "the
// price then", it is the nearest price there happens to be.
const SCRUB_GAP_MIN=3;

function priceAt(geo, x){
  // What the price line says at a point across the chart:
  // -> {x, y, t, s, kind}, kind saying which sort of answer it is.
  //
  // It never reads past what was measured. Right of the last minute recorded
  // it reads the LATEST price and says so; a stretch with no price within
  // SCRUB_GAP_MIN of the finger reads none ('gap'), rather than reaching for
  // the nearest point on the other side of the hole. The one mark on this
  // chart that is a time of day is the price line, so it is the only thing
  // this reads: where a bar sits across the chart is not a time.
  if(!geo||!Array.isArray(geo.pts)||geo.pts.length<2||!isFinite(x)) return null;
  const at=Math.min(Math.max(x, geo.plotL), geo.plotR);
  const last=geo.pts[geo.pts.length-1];
  if(at>=last.x&&geo.live&&at>=geo.live.x-2)
    return {x:geo.live.x, y:geo.live.y, t:geo.live.t, s:geo.live.v, kind:'latest'};
  if(at>=last.x) return {x:last.x, y:last.y, t:last.t, s:last.s, kind:'last'};
  const t=geo.t0+(at-geo.plotL)/((geo.pathR>geo.plotL)?(geo.pathR-geo.plotL):1)*(geo.t1-geo.t0);
  const p=geo.pts.reduce((a, b)=>Math.abs(b.t-t)<Math.abs(a.t-t)?b:a);
  return Math.abs(p.t-t)>SCRUB_GAP_MIN*60000
       ?{x:at, y:null, t, s:null, kind:'gap'}
       :{x:p.x, y:p.y, t:p.t, s:p.s, kind:'line'};
}

/* ---- how busy each strike has been ------------------------------------- */

// The ladder's gauge is on a FIXED scale: a per-scan maximum would fill the
// busiest row on every scan and destroy the comparison between scans and
// between days. Full is five turns of the pile. Over the 288 ladder rows of
// 09-15, 09-16 and 09-17 the multiple ran p50 1.44, p90 4.66, p95 5.51, max
// 8.17: at 5 the cap takes 7.6% of rows, near the 6.4 to 8.1% of walls the
// levels card's bars took at their 30% cap, and 1x sits a fifth of the way
// along, where 34% of rows end short of it. At 4 the cap took one row in eight.
const FULL_TURNOVER=5;

// Under 500 contracts standing, the multiple reports the smallness of the pile
// faster than the size of the day. The busiest single four-minute stretch on
// the 2026-09-16 board added 326 contracts, two thirds of a whole turn of a
// 500-lot pile. 12 of 174 ladder rows that day fell under it, all at 1,495.
const THIN_PILE=500;

function turnover(row){
  // How many times over the contracts already standing at a strike have
  // changed hands today: everything traded against the open positions at last
  // night's close. Calls and puts are summed on both sides, the way the scan's
  // own contracts_share_pp counts a strike. The pile is last night's and does
  // not move during the day, so the number cannot drift on its denominator.
  //
  // No pile, or no volume measured at all: no multiple, never a zero. The day's
  // first scan carries open interest and no volume columns.
  if(!row) return null;
  const vc=_fin(row.vol_calls), vp=_fin(row.vol_puts);
  const pile=(_fin(row.oi_calls)||0)+(_fin(row.oi_puts)||0);
  if(!(pile>0)||(vc==null&&vp==null)) return null;
  return {mult:((vc||0)+(vp||0))/pile, pile};
}

function turnoverBar(mult){
  // The gauge, in percent of its track, on the ladder's own scale.
  // Anything above zero draws at least 2%, so a datum that exists gets ink; past
  // full the bar fills the track and says it was clipped rather than being
  // quietly flattened. Nothing traded is a measured zero and draws no fill.
  if(mult==null||!isFinite(mult)||mult<0) return null;
  return {pct:mult>0 ? Math.max(2, Math.min(100, mult/FULL_TURNOVER*100)) : 0,
          over:mult>FULL_TURNOVER};
}

/* ---- how fast each strike is trading now ------------------------------- */

// The last 4 stretches between the scanner's tallies against every stretch
// before them — 4 against 7 on a full series — in CONTRACTS A MINUTE. Not the
// last few prints: one stretch swings hard (on 2026-09-16 1,490 traded 206 from
// 14:51 to 14:55 and 8 in the five minutes after) and three cannot tell a turn
// from a tick. At least 3 stretches each side, so the first word of a session
// comes at its 8th tally. Under 150 contracts across the whole series a 25%
// swing is inside the counting noise, so nothing is said. 1.25 and 0.80 are one
// step either way.
const PACE_RECENT=4, PACE_MIN=3, PACE_FLOOR=150, PACE_FAST=1.25, PACE_SLOW=0.80;

function _clockMin(s){
  const m=/^(\d{1,2}):(\d{2})$/.exec(String(s));
  return m ? Number(m[1])*60+Number(m[2]) : null;
}

function pace(series, bookTimes){
  // -> {before, now, word} in contracts a minute, or null when the series
  // cannot carry a word. `series` is vol_added_per_book: contracts traded at the
  // strike between consecutive tallies, one entry per gap in frames.book_times.
  //
  // PER MINUTE, NOT PER TALLY. The tallies are four minutes apart until the
  // scanner stalls, and then one entry holds everything since the stall: on
  // 2026-09-15 at 13:24 the last gap was 108 minutes and 1,500's 974 contracts
  // in it read, per tally, as four times the earlier pace — per minute it was
  // half. A mean per tally gives a different word on 84 of the 491 worded
  // strike rows of 09-15/16/17: 73 in the nine scans with a stall in them, and
  // 11 single rows within 7% of a threshold. A null entry is a stretch the
  // strike was not measured across, so its minutes go with it.
  if(!Array.isArray(series)||!Array.isArray(bookTimes)||bookTimes.length!==series.length+1) return null;
  const t=bookTimes.map(_clockMin);
  const stretch=(from, to)=>{
    let n=0, v=0, min=0;
    for(let i=from;i<to;i++){
      const x=_fin(series[i]);
      if(x==null) continue;
      const gap=(t[i]!=null&&t[i+1]!=null) ? t[i+1]-t[i] : null;
      if(!(gap>0)) return null;          // a gap that cannot be timed cannot be a rate
      n++; v+=x; min+=gap;
    }
    return {n, v, min};
  };
  const cut=Math.max(0, series.length-PACE_RECENT);
  const now=stretch(cut, series.length), before=stretch(0, cut);
  if(!now||!before||now.n<PACE_MIN||before.n<PACE_MIN) return null;
  if(now.v+before.v<PACE_FLOOR) return null;
  const b=before.v/before.min, a=now.v/now.min;
  if(!(b>0)) return null;
  return {before:b, now:a,
          word:a/b>=PACE_FAST ? 'faster' : a/b<=PACE_SLOW ? 'slower' : 'steady'};
}

/* ---- where the activity is, as a map around price ---------------------- */

function activityRows(day, price, show, strikes, bookTimes){
  // The panel printed "Newly busy: 7 strikes / Gone quiet: 10 strikes" and threw
  // away every strike and every time the builder had already written down. On
  // 2026-09-17 at 10:45 that count hid the whole story: everything newly busy
  // sat within $22 ABOVE price and everything that went quiet sat just BELOW it.
  //
  // So the rows are the strikes themselves, in price order around price, nearest
  // first, and each carries which of three things happened to it. Nearest first
  // because distance from price is what decides whether a change matters at all:
  // a strike that went quiet two hundred dollars away is not news.
  if(!day) return null;
  const st={};
  for(const k of (day.stood||[]))  { const y=_fin(k); if(y!=null) st[y]={y, state:'held', at:null}; }
  for(const k of (day.joined||[])) { const y=_fin(k); if(y!=null) st[y]={y, state:'new',  at:null}; }
  // LAST, so it wins: a strike that arrived and then went quiet is gone now, and
  // "gone now" is the fact the reader is standing in.
  for(const e of (day.left||[])){
    const y=_fin(Array.isArray(e)?e[0]:(e&&e.strike));
    if(y==null) continue;
    const t=Array.isArray(e)?e[1]:(e&&e.at);
    st[y]={y, state:'gone', at:t?String(t):null};
  }
  const all=Object.keys(st).map(k=>st[k]);
  if(!all.length||price==null||!isFinite(price)) return null;
  const n=(show>0?show:5);
  // drawn top to bottom, so both sides run high price to low
  const up=all.filter(r=>r.y>price).sort((a,b)=>a.y-b.y);
  const dn=all.filter(r=>r.y<price).sort((a,b)=>b.y-a.y);
  const above=up.slice(0,n).reverse(), below=dn.slice(0,n);
  // the state word is printed once per RUN, so a block of one kind reads as one
  // thing instead of repeating itself down the column
  for(const side of [above, below]){
    let last=null;
    for(const r of side){ r.first = r.state!==last; last=r.state; }
  }
  // How busy each strike has been rides with its row, off the scan's strike
  // table. A strike that has gone quiet has left the list that table is drawn
  // from — 74 gone rows on the ladders of 09-15, 09-16 and 09-17, none of them in
  // it — and its row carries its time instead. If the two ever meet, the time
  // wins, as `gone` wins the row's state above.
  const table={};
  for(const s of (((strikes||{}).rows)||[])){ const y=_fin(s&&s.strike); if(y!=null) table[y]=s; }
  for(const r of above.concat(below)){
    const t=r.state!=='gone' ? turnover(table[r.y]) : null;
    if(!t) continue;
    r.mult=t.mult; r.thin=t.pile<THIN_PILE;
    const p=pace(table[r.y].vol_added_per_book, bookTimes);
    if(p) r.pace=p.word;
  }
  return {above, below, moreAbove:Math.max(0, up.length-above.length),
          moreBelow:Math.max(0, dn.length-below.length),
          counts:{new:all.filter(r=>r.state==='new').length,
                  gone:all.filter(r=>r.state==='gone').length,
                  held:all.filter(r=>r.state==='held').length}};
}

/* ---- the ladder's columns, on the card's width ------------------------- */

// Six columns, drawn for a 375px phone and its 311px content box: the figure,
// the dot, the state word, then the fourth column split three ways — the
// multiple, its gauge, one word — with the card's 12px clearance baked into
// each track. The first three never move: the figure column ends where the
// price chip's integer does (10 + 36), and the word column holds "busy all
// day" (65.04) and its 12, and "further above" (76.73). Advances are the
// shipped face's in WebKit at 12px/400: "small pile" (52.28) is the widest
// word the last cell holds, and "0.06×" (32.89 at 12px/500) the widest
// multiple.
const GRID_COLS=[46, 24, 78], GRID_MULT=33, GRID_WORD=52.28, GRID_COUNT=76.73;
const GRID_GAP=12, GRID_GAP_MIN=6;
// The gauge is 46 where it was drawn and never shorter than 34, the shortest
// track that still reads as a ratio. One turn is then 6.8px: GAUGE-SPEC judged
// a 1× tick 6.6px from the zero no longer reads as a mark, and 34 is the first
// whole length past that. It is also the shortest track on which "1×" (13.46
// at 11px/500), centred on its tick, starts inside the track instead of
// hanging off its zero; "1×" and "5×" keep 13.74 between them there. Over the
// 366 ladder rows of 09-15/16/17, the bars ending within 1px of the tick, where
// only the printed number can say short of one turn or past it, go from 7.1%
// at 46 to 9.3% at 34.
const GRID_TRACK=46, GRID_TRACK_MIN=34;

function activityGrid(width, headW){
  // -> {cols, track, head} for a card whose content box is `width` px wide: the
  // five fixed tracks, in px, before the last cell, which takes the rest; the
  // gauge's length, or null for no gauge and no scale; and where the fourth
  // column's head goes — 'beside' "further above" at its own size, 'smaller',
  // there at 11px, or 'alone' in a row of its own. `headW` is its width at 12px.
  //
  // From 311 up it is the drawn layout, and a wider card only gives the last
  // cell more room. NARROWER IS AN INTERIM REFLOW: the owner has not decided
  // how the grid should behave below 375, so it gives up width in the order
  // that keeps every word legible, and stops as soon as the row fits. The gauge
  // shortens first, by exactly what the row is short, down to GRID_TRACK_MIN,
  // its tick and scale keeping their places on it; then the gaps either side of
  // it close toward the card's 6px unit; and only then does it go, with its
  // scale, leaving the multiple to say how many times over. At 360 (296 of
  // content) the track is 38 and nothing else in the row moves; at 320 (256)
  // the gauge goes.
  //
  // No column can make room for the head. It is right-aligned to the card's
  // edge and "further above" starts at a fixed 70, so the two close by exactly
  // what the card narrows; at 360 they were 1.27px apart. Under 307.35 of
  // content it is set a pixel smaller, which keeps the card's 12 from "further
  // above" down to 294.97, and under that it takes a row of its own.
  const w=width>0 ? width : 0;
  const room=w-GRID_COLS[0]-GRID_COLS[1]-GRID_COLS[2]-GRID_MULT-GRID_WORD;
  let gap=GRID_GAP, track=Math.min(GRID_TRACK, Math.floor(room-2*gap));
  if(track<GRID_TRACK_MIN){
    track=GRID_TRACK_MIN;
    gap=Math.floor((room-track)/2);
  }
  let cols;
  if(gap>=GRID_GAP_MIN) cols=GRID_COLS.concat([GRID_MULT+gap, track+gap]);
  else {
    // The gauge's cell stays, empty, so the words keep their place at the
    // card's edge, under the head's own right edge, rather than closing up on
    // the number beside them.
    track=null;
    gap=Math.max(GRID_GAP_MIN, Math.min(GRID_GAP, Math.floor(room)));
    cols=GRID_COLS.concat([GRID_MULT+gap, Math.max(0, Math.floor(room-gap))]);
  }
  const beside=w-GRID_COLS[0]-GRID_COLS[1]-GRID_COUNT-GRID_GAP;
  const head=headW<=beside ? 'beside' : headW*11/12<=beside ? 'smaller' : 'alone';
  return {cols, track, head};
}

function namedGone(day){
  // Strikes the model named that have since dropped off the list, and which of
  // them left the book altogether. It is the one line on this card that says
  // "what I told you earlier is gone", and nothing showed it before.
  const out=[];
  for(const e of ((day||{}).named_off_list||[])){
    const y=_fin(e&&e.strike);
    if(y==null) continue;
    out.push({y, at:(e.named_at?String(e.named_at):null), inBook:e.in_book!==false});
  }
  return out;
}

/* ---- the last half hour, and apart from it the record ------------------ */

function halfHour(scene, rec){
  // -> every word and count THE LAST HALF HOUR card prints, or null for no card.
  //
  // Two things, which the card keeps apart. THIS HALF HOUR comes off the scene
  // the phone already holds:
  //   the move     price.moved_last_30min_sigma: the board's own path, the
  //                live spot against the first diary scan at least 30 minutes
  //                back (sndk_read.with_path), in that row's sigma. Not the
  //                minute bars, which run on another clock: at 15:10 on
  //                2026-09-16 their 14:40 and 15:10 closes are $26.74 apart
  //                where the path says $19.09, because price ran $5 in the
  //                14:39 minute. The card says what the scene says.
  //   in dollars   times scale.one_sigma_dollars, the sigma it was divided by
  //   the clock    data_sources.scan_taken_at less 30 minutes
  // THE RECORD is `earlier_half_hours` off the payload wrapper, counted by
  // snapshot._earlier_half_hours over every session before today's:
  //   usual        its median half hour, put on today's ruler
  //   the counts   the pairs on the same side of usual as this half hour. The
  //                side is the record's own cut, so "That was bigger than
  //                usual" can never disagree with the counts under it.
  //
  // Honest-absent at card level: every line hangs on the move, its clock, the
  // ruler and the record, and there is no true half of the card. No move (a gap
  // in the diary), no clock, no ruler or no record, and there is no card.
  // Nor before the session has had a whole half hour: with_path measures its
  // "30-minute" move off the first scan from 20 minutes in — at 09:51 on
  // 2026-09-16 it read -0.39 across 20.7 minutes — and SINCE 09:21 would name a
  // time before the open. Nor with fewer than two earlier sessions or two half
  // hours to count: every sentence here is plural, and "So were 1 earlier half
  // hours" is worse than no card. Nor when the two counts do not add up to the
  // half hours they split, the one sum a reader can check.
  const pr=(scene&&scene.price)||{}, ds=(scene&&scene.data_sources)||{};
  const mv=_fin(pr.moved_last_30min_sigma), sig=_fin(((scene&&scene.scale)||{}).one_sigma_dollars);
  const usual=_fin(rec&&rec.usual_sigma), days=_fin(rec&&rec.sessions);
  const t=Date.parse(ds.scan_taken_at), since=isFinite(t) ? etTime(t-30*60000) : null;
  const from=_clockMin(since);
  if(mv==null||from==null||from<9*60+30||!(sig>0)||!(usual>0)||!(days>=2)) return null;
  const bigger=Math.abs(mv)>=usual;
  const b=rec[bigger ? 'bigger' : 'no_bigger']||{};
  const n=_fin(b.n), other=_fin(b.other_way), same=_fin(b.same_way);
  if(!(n>=2)||other==null||same==null||other<0||same<0||other+same!==n) return null;
  // The captions name the direction the sentence above them printed, off the
  // same sign: down, and the other way is back up. It is a renaming of the
  // record's buckets, not a recount — a pair is "other way" when its two half
  // hours have opposite signs. Exactly flat has no direction, so none is named.
  const dir=mv>0 ? 'Up' : mv<0 ? 'Down' : 'Flat';
  return {
    since,
    say: [dir+' '+gUsd(Math.abs(mv*sig), 0)+' in the last half hour.',
          'A usual half hour on this stock is '+gUsd(usual*sig, 0)+'.'],
    span: days+' trading days',
    set: ['That was '+(bigger ? 'bigger' : 'no bigger')+' than usual. So were '
          +n.toLocaleString('en-US')+' earlier',
          'half hours. Here is what came next each time:'],
    // other way first, always: sorted by size, the larger count would lead on
    // every board, and that is emphasis
    out: [{n:other, words:dir==='Up' ? 'went back down' : dir==='Down' ? 'went back up' : 'went the other way'},
          {n:same,  words:dir==='Up' ? 'kept going up' : dir==='Down' ? 'kept going down' : 'went the same way'}],
  };
}

if(typeof module!=='undefined'&&module.exports){
  module.exports={gUsd, gMinutes, gTimes, wallPassed, priorClose,
                  bookAge, shownPrice, dayChange,
                  etTime, etToday,
                  coreLevels, optionalLevels, magnetRunners, solveWindow, mergeLevels,
                  layoutLabels, figW, axisStep, priceTicks,
                  barPoints, tapePoints, livePoint, modelRead,
                  TRADED_ZERO, TRADED_SIDE, COUNT_CALLS_W, COUNT_PUTS_W, tradedBars,
                  tradedSince,
                  newContracts, NEW_WORD, NEW_MORE, newBox, wordRow,
                  pickedRow, activityRows, namedGone,
                  FULL_VOL_PER_MIN, volumeBlocks, axisW, stripName,
                  HOLD_MS, TOUCH_SLOP, touchKind,
                  LENS_ZOOM, LENS_W, LENS_H, LENS_H_MIN, LENS_LIFT, LENS_EDGE, LENS_DOT,
                  lensBox, barAt, volumeBlockAt, chartAt, SCRUB_GAP_MIN, priceAt,
                  FULL_TURNOVER, THIN_PILE, turnover, turnoverBar, pace,
                  GRID_TRACK_MIN, activityGrid, halfHour};
}
