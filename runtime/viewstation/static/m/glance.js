/* glance.js — the phone view's reasoning, kept out of the markup.
 *
 * Every function here is PURE (data in, value out) so the page can be read as
 * layout and this can be read as rules, and so the rules can be tested without
 * a browser.
 *
 * THREE LAWS THIS FILE ENFORCES
 *   1. Honest-absent. No datum yields no value, never a zero and never a guess.
 *   2. No English is authored here. Every sentence the phone shows is copied
 *      byte-for-byte from the desktop (snkArrows, envWords) so the two screens
 *      can never describe one board in two voices.
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

/* ---- the environment, in two words ------------------------------------ */

function envWords(regime){
  if(!regime) return null;
  const word=regime.word;
  const g=gammaIsLong(regime);
  const lean = g==null ? null : (g ? 'walls hold' : 'walls give way');
  if(word && lean) return String(word)+' · '+lean;
  return word ? String(word) : lean;
}

function gammaIsLong(regime){
  // Recognised tokens only; everything else is null.
  //
  // This returned FALSE for 'unknown' until 2026-08-24, and that is not a bug
  // of degree. gamma_sign is literally the string 'unknown' on 241 of 3,393
  // recorded rows (7.1%), and on every one of them the header said "walls give
  // way" and the card claimed a dealer direction. A confident claim, in the
  // largest words on the screen, from a field whose value is the word unknown.
  if(!regime) return null;
  const s=regime.gamma_sign;
  if(typeof s==='number') return isFinite(s) ? s>0 : null;
  if(s==='positive'||s==='long'||s==='+') return true;
  if(s==='negative'||s==='short'||s==='-') return false;
  return null;
}

/* ---- what happens at a wall ------------------------------------------- */

function wallBehaviour(regime, strike, spot){
  // snkArrows' four cases, unchanged and unreworded. `up` there is
  // green?(k<spot):(k>spot) — a positive-gamma level below price holds it up,
  // the same level above caps it, and a negative-gamma level does the opposite
  // on both sides because the hedge runs WITH the move instead of against it.
  const green=gammaIsLong(regime);
  if(green==null||strike==null||spot==null||!isFinite(strike)||!isFinite(spot)) return null;
  const up = green ? (strike<spot) : (strike>spot);
  const eng = green
    ? (up ? 'Dealers buy the dips here — it holds price up.'
          : 'Dealers sell the rallies here — it caps the move.')
    : (up ? 'Dealers must buy a break up — moves speed up.'
          : 'Dealers must sell a break down — moves speed up.');
  return {up, english: eng, kind: green ? 'brake' : 'accelerant'};
}

/* ---- what lies past it ------------------------------------------------ */

function beyondWall(walls, side){
  // This once led with a `walls[side + '_side_clear']` branch that was DEAD BY
  // CONSTRUCTION: walls_ladder sets *_side_clear only when a side's pool is
  // empty and continues before writing the ladder, so a side can never hold
  // both — and this is only ever called with the side of a wall that exists.
  if(!walls||!side) return null;
  const behind=walls[side+'_heaviest_behind'];
  if(behind&&behind.strike!=null)
    return {strike:behind.strike, gex:_fin(behind.gex), heaviest:true};
  const ladder=walls[side];
  if(Array.isArray(ladder)&&ladder.length>=2&&ladder[1].strike!=null)
    return {strike:ladder[1].strike, gex:_fin(ladder[1].gex), heaviest:false};
  // A complete ladder of one with nothing named behind it is a sound
  // inference that the side holds exactly one cluster: WALLS_PER_SIDE is 2, so
  // a second would have shipped if it existed.
  if(Array.isArray(ladder)&&ladder.length===1) return {alone:true};
  return null;
}

function farSideNote(walls, side){
  // Worded as what the flag MEANS. walls_ladder admits a cluster only if it
  // matches the side by gamma sign AND sits on that side of spot, so a
  // wrongly-signed pile there is dropped from both pools. "Nothing measured on
  // that side" overstated it.
  if(!walls||!side) return null;
  const far = side==='call' ? 'put' : 'call';
  if(walls[far+'_side_clear']!==true) return null;
  return far==='put' ? 'No put wall below price.' : 'No call wall above price.';
}

function bothSidesClear(walls){
  // A board measured clear on BOTH sides is not an absence of information. It
  // is the loudest reading the scene can produce, and the card used to delete
  // itself there because nearestWall returns null.
  return !!walls && walls.call_side_clear === true && walls.put_side_clear === true;
}

function nearestWall(walls, spot){
  if(!walls||spot==null||!isFinite(spot)) return null;
  const cands=[];
  const up=(walls.call||[])[0], dn=(walls.put||[])[0];
  if(up&&up.strike!=null) cands.push(Object.assign({side:'call', dir:'up'}, up));
  if(dn&&dn.strike!=null) cands.push(Object.assign({side:'put', dir:'down'}, dn));
  if(!cands.length) return null;
  cands.sort((a,b)=>Math.abs(a.strike-spot)-Math.abs(b.strike-spot));
  return cands[0];
}

function wallDistance(strike, price){
  // Against the price actually ON SCREEN. The masthead repaints off the live
  // quote every 5s while the scene rebuilds every 60s, so the shipped `sigma`
  // is measured from a spot the reader can no longer see. Dollars only: no
  // Greek leaves this file.
  if(strike==null||price==null||!isFinite(strike)||!isFinite(price)) return null;
  const d=strike-price;
  return {dollars:Math.abs(d), signed:d};
}

/* ---- price, age, and the two things that must never be guessed --------- */

function priorClose(price){
  const now=price&&price.now, pct=price&&price.vs_prior_close_pct;
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
  const v=(q!=null)?q:((scene&&scene.price)||{}).now;
  return (v!=null&&isFinite(v)) ? {v:Number(v), live:q!=null} : null;
}

function dayChange(scene, live, diaryLast, todayStr){
  // Gated on the SCAN'S OWN DATE against the reader's local date. The risk is a
  // percentage measured against ANOTHER day's close; comparing dates removes
  // exactly that and still lets a zero-minute-old scan show its own change. A
  // mismatch WITHHOLDS the figure. It never misstates it.
  const local=todayStr||etToday();
  if((((scene||{}).clock)||{}).date !== local) return null;
  const p=(scene||{}).price||{};
  const q=_fin(live&&live.spot);
  if(q==null) return _fin(p.vs_prior_close_pct);
  // the diary row carries the EXACT prior close; recovery from a 1-dp
  // percentage is a rounding of it, so prefer the real number when it is there
  const pc=_fin(diaryLast&&diaryLast.prior_close) || priorClose(p);
  return (pc&&isFinite(pc)) ? (q/pc-1)*100 : null;
}

function vwapPrice(scene, diaryLast){
  // A PRICE at a position. vwap_dist_sigma is (vwap - spot)/sigma, so a
  // NEGATIVE value means price is ABOVE its average — 13 of 15 reviewers read
  // it backwards. Rendering the level instead of the ratio makes the sign trap
  // structurally impossible: a price cannot be read backwards.
  const exact=_fin(diaryLast&&diaryLast.vwap);
  if(exact!=null) return exact;
  const p=((scene||{}).price)||{}, sig=_fin(((scene||{}).scale||{}).one_sigma_dollars);
  if(p.now==null||p.vwap_dist_sigma==null||sig==null) return null;
  return p.now + p.vwap_dist_sigma * sig;
}

/* ---- weight ------------------------------------------------------------ */

function wallTier(gex){
  // Three steps, because 1.2px against 1.6px is invisible at arm's length
  // outdoors. Floor raised to 1.6 for the same reason. Measured over 18,510
  // recorded wall observations the share of the book runs p10 3.5%, p50 7.3%,
  // p90 20.4% — spread enough that weight is worth encoding at all.
  if(gex==null||!isFinite(gex)) return 1.8;      // a default, never a claim
  if(gex >= 10) return 2.8;
  if(gex >= 5)  return 2.0;
  return 1.6;
}

function railWidth(gex, full){
  // Fixed full scale: 20% of the book fills the bar, always. A per-scan maximum
  // makes the biggest wall full-width every single scan and destroys
  // comparison between days. 20.4% is p90 of the recorded tape, so roughly one
  // wall in ten clips, and the clip is marked.
  if(gex==null||!isFinite(gex)) return null;     // no datum: no bar AND no track
  return {w:Math.max(2, Math.min(full, gex/20*full)), clipped: gex>20};
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
  const mag=((scene||{}).magnet||{}).top_strikes;
  if(Array.isArray(mag)&&mag.length&&Array.isArray(mag[0])&&_fin(mag[0][0])!=null)
    out.push({y:Number(mag[0][0]), kind:'magnet', lead:true, share:_fin(mag[0][1]),
              weight:1});
  if(vwap!=null) out.push({y:vwap, kind:'vwap'});
  return out;
}

function optionalLevels(scene){
  // Tried one at a time, HEAVIEST FIRST, each subject to the admission test.
  const out=[], w=(scene||{}).walls||{};
  for(const side of ['call','put']){
    const b=w[side+'_heaviest_behind'];
    if(b&&b.strike!=null) out.push(Object.assign(_wall(b,side,false),{behind:true}));
    const l=w[side];
    if(Array.isArray(l)&&l[1]&&l[1].strike!=null) out.push(_wall(l[1],side,false));
  }
  return out.sort((a,b)=>(b.gex||0)-(a.gex||0));
}

function magnetRunners(scene){
  const out=[], mag=((scene||{}).magnet||{}).top_strikes;
  if(!Array.isArray(mag)||mag.length<2) return out;
  const top=_fin(mag[0][1]);
  for(let i=1;i<mag.length;i++){
    const m=mag[i];
    if(!Array.isArray(m)||_fin(m[0])==null) continue;
    const share=_fin(m[1]);
    // continuous, no threshold anywhere: sr-3 deleted a hardcoded 5.0pp tie
    // constant for shipping a near-constant as a finding, and any cutoff here
    // re-imports it. A near-tie must LOOK like a tie without anyone deciding
    // where a tie begins.
    out.push({y:Number(m[0]), kind:'magnet', lead:false, share,
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

function tapePoints(rows){
  // Diary rows carry ts + spot every couple of minutes, which is the session's
  // own record and the only series reliably there. The finer sndk_tape fills
  // only while somebody has the desktop tab open, so it cannot be the spine —
  // a glance must draw something on a day nobody watched.
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
  const line = String(rd.read || (pts[0] && pts[0].note) || '')
            || 'Nothing standing out on the board.';
  return {line, quiet,
          count:pts.length,
          forced: rd.abstain === 'forced',
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
  // YYYY-MM-DD in New York, to compare against scene.clock.date — which is also
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
  return {y:Number(e.strike), kind:'wall', side, nearest:!!nearest,
          gex:_fin(e.gex),
          held:(e.unchanged_min!=null)?_fin(e.unchanged_min):_fin(e.unchanged_min_at_least),
          heldExact:e.unchanged_min!=null};
}

if(typeof module!=='undefined'&&module.exports){
  module.exports={gUsd, gMinutes, envWords, gammaIsLong, wallBehaviour, beyondWall,
                  farSideNote, bothSidesClear, nearestWall, wallDistance, priorClose,
                  bookAge, shownPrice, dayChange, vwapPrice, wallTier, railWidth,
                  etTime, etToday,
                  coreLevels, optionalLevels, magnetRunners, solveWindow, mergeLevels,
                  layoutLabels, tapePoints, livePoint, modelRead};
}
