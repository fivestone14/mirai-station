/* glance.js — the phone view's reasoning, kept out of the markup.
 *
 * WHAT THIS IS. The desktop map answers "what does the whole board look like".
 * This answers one much smaller question — "if price keeps going, what does it
 * hit, and what happens there" — because that is all a glance can carry. Every
 * function here is PURE (data in, string or number out) so the page can be read
 * as layout and this can be read as rules.
 *
 * WHERE THE RULES COME FROM. The wall-behaviour sentences are lifted from
 * snkArrows in index.html, which is the validated MM-action rule ported from
 * the SPX map on 07-29. They are copied verbatim rather than reworded: the
 * desktop and the phone must never describe the same board in two voices.
 *
 * ONE HONEST DIFFERENCE, stated here because it cannot be seen from the screen.
 * snkArrows reads the gamma sign PER STRIKE off gex_views.net_by_strike. The
 * scene the payload ships does not carry that surface — walls arrive as
 * strike/sigma/gex only — so this reads the sign off regime.gamma_sign, the
 * BOARD's own sign. Same rule, coarser instrument. It agrees with the desktop
 * whenever a wall's local sign matches the board's, which is the ordinary case
 * and not the guaranteed one. The screen says "board" for exactly this reason.
 *
 * HONEST-ABSENT, the payload's own doctrine, applies to every function: no
 * datum yields no sentence, never a fabricated direction. Missing must never
 * look like zero.
 */

/* ---- formatting ------------------------------------------------------- */

function gUsd(v, dp){
  if(v==null||!isFinite(v)) return null;
  return '$' + Number(v).toLocaleString('en-US',
    {minimumFractionDigits: dp==null?2:dp, maximumFractionDigits: dp==null?2:dp});
}

function gSigma(s){
  // sigma is the ruler the whole scene is written in — one sigma is a typical
  // move for this name. Rendered with its plain-English gloss the first time
  // it appears on screen, never as a bare Greek letter.
  if(s==null||!isFinite(s)) return null;
  const a=Math.abs(s);
  return a.toFixed(2)+'σ';
}

function gMinutes(m){
  if(m==null||!isFinite(m)) return null;
  if(m<60) return Math.round(m)+'m';
  const h=Math.floor(m/60), r=Math.round(m%60);
  return r? h+'h '+r+'m' : h+'h';
}

/* ---- the environment, in two words ------------------------------------ */

function envWords(regime){
  // The header's whole job. gamma_sign is the permission slip (wt-8 doctrine):
  // positive gamma means dealers lean against moves, negative means they feed
  // them. regime.word is the reader's own label and leads when present.
  if(!regime) return null;
  const word=regime.word;
  const g=gammaIsLong(regime);
  const lean = g==null ? null : (g ? 'walls hold' : 'walls give way');
  if(word && lean) return String(word)+' \u00b7 '+lean;
  return word ? String(word) : lean;
}

function gammaIsLong(regime){
  // Recognised tokens only; everything else is null.
  //
  // This returned FALSE for 'unknown' until 2026-08-24, and that is not a bug
  // of degree. gamma_sign is literally the string 'unknown' on 241 of 3,393
  // recorded rows (7.1%), and on every one of them the header said "walls give
  // way" and the card said "Dealers must sell a break down - moves speed up."
  // A confident direction, in the largest words on the screen, derived from a
  // field whose value is the word unknown. This file's own header promises
  // that no datum yields no sentence; this was the one place it lied.
  if(!regime) return null;
  const s=regime.gamma_sign;
  if(typeof s==='number') return isFinite(s) ? s>0 : null;
  if(s==='positive'||s==='long'||s==='+') return true;
  if(s==='negative'||s==='short'||s==='-') return false;
  return null;
}

/* ---- what happens at a wall ------------------------------------------- */

function wallBehaviour(regime, strike, spot){
  // snkArrows' four cases, unchanged. `up` there is green?(k<spot):(k>spot) —
  // a positive-gamma level below price holds it up, the same level above caps
  // it, and a negative-gamma level does the opposite on both sides because the
  // hedge runs WITH the move instead of against it.
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
  // What lies past the wall price meets first.
  //
  // This used to lead with a `walls[side + '_side_clear']` branch that was
  // DEAD BY CONSTRUCTION and had therefore never once rendered: walls_ladder
  // sets *_side_clear only when a side's pool is empty and `continue`s before
  // writing the ladder (sndk_read.py:1165-1176), so a side can never hold both
  // — and this function is only ever called with the side of a wall that
  // exists. The far side's emptiness is a real fact and belongs on the card,
  // but it is farSideNote's job, not this one's.
  //
  // The desktop finds air pockets with snkGaps, which measures the space
  // between one cluster's far edge and the next one's near edge. The scene
  // ships wall PEAKS with no edges, so that measurement cannot be reproduced
  // and is not approximated — a fabricated gap is worse than none.
  if(!walls||!side) return null;
  const behind=walls[side+'_heaviest_behind'];
  if(behind&&behind.strike!=null)
    return {strike:behind.strike, gex:(behind.gex!=null&&isFinite(behind.gex))?behind.gex:null,
            heaviest:true};
  const ladder=walls[side];
  if(Array.isArray(ladder)&&ladder.length>=2&&ladder[1].strike!=null)
    return {strike:ladder[1].strike, gex:(ladder[1].gex!=null&&isFinite(ladder[1].gex))?ladder[1].gex:null,
            heaviest:false};
  // A complete ladder of one, with nothing named behind it, is a sound
  // inference that the side holds exactly one cluster — WALLS_PER_SIDE is 2,
  // so a second would have shipped if it existed.
  if(Array.isArray(ladder)&&ladder.length===1) return {alone:true};
  return null;
}

function farSideNote(walls, side){
  // walls.put_side_clear / call_side_clear are measured-empty: the board WAS
  // read and that side holds nothing between price and open air. On this name
  // price in the clear is often the loudest fact on the board, and it belongs
  // on screen even when it is behind price rather than ahead of it.
  if(!walls||!side) return null;
  const far = side==='call' ? 'put' : 'call';
  if(walls[far+'_side_clear']!==true) return null;
  // Worded as what the flag MEANS. walls_ladder admits a cluster only if it
  // matches the side by gamma sign AND sits on that side of spot
  // (sndk_read.py:1158-1164), so a wrongly-signed pile there is dropped from
  // both pools. "Nothing measured on that side" overstated it; the honest
  // claim is that no wall of that kind stands between price and open air.
  return far==='put' ? 'No put wall below price.' : 'No call wall above price.';
}

function bothSidesClear(walls){
  // The card used to vanish entirely here: nearestWall returns null when
  // neither side has a ladder, and the card hid itself. But a board measured
  // clear on BOTH sides is not an absence of information — it is the loudest
  // reading the scene can produce, and sndk_read.py:1166-1172 says so in as
  // many words. Deleting the card was deleting the finding.
  return !!walls && walls.call_side_clear === true && walls.put_side_clear === true;
}

function wallDistance(strike, price, sigma){
  // Distance against the price actually ON SCREEN. The header repaints off the
  // live quote every 5s while the scene rebuilds every 60s, so the card's
  // shipped `sigma` is measured from a spot the reader can no longer see — it
  // printed 0.58sigma when the honest figure against the displayed price was
  // 0.51. Dollars lead because a general reader needs no training to read them.
  if(strike==null||price==null||!isFinite(strike)||!isFinite(price)) return null;
  const d=strike-price;
  return {dollars:Math.abs(d), signed:d,
          sigma:(sigma&&isFinite(sigma)&&sigma>0)?Math.abs(d)/sigma:null};
}

function nearestWall(walls, spot){
  // "First" is by distance, which is the ladder's own ordering — walls.call[0]
  // and walls.put[0] are each the nearest on their side, so the comparison is
  // only ever between two candidates.
  if(!walls||spot==null||!isFinite(spot)) return null;
  const cands=[];
  const up=(walls.call||[])[0], dn=(walls.put||[])[0];
  if(up&&up.strike!=null) cands.push({side:'call', dir:'up', ...up});
  if(dn&&dn.strike!=null) cands.push({side:'put', dir:'down', ...dn});
  if(!cands.length) return null;
  cands.sort((a,b)=>Math.abs(a.strike-spot)-Math.abs(b.strike-spot));
  return cands[0];
}

/* ---- the levels worth drawing ----------------------------------------- */

function drawableLevels(scene){
  // WHAT EARNS A LINE.
  //
  // The first cut drew six and the labels collided. The second cut drew the
  // nearest wall on each side and nothing else, and on 2026-08-24 that put the
  // LIGHTEST object on the board on screen while hiding the heaviest: walls.put[0]
  // was 1450 carrying 6.9% of the book, while put_heaviest_behind at 1400 carried
  // 14.8% — more than double — and never reached the canvas at all. The chart was
  // not under-drawn, it was selecting wrong.
  //
  // So the ladder ships whole, plus the heaviest-behind on each side, which
  // exists precisely because the distance-ordered ladder was cutting the
  // heaviest cluster (sndk_read.py:1177-1186). Weight is carried by stroke and
  // by a rail bar rather than by any new text.
  //
  // Still refused: session high/low (the price path IS them) and every word
  // label (the card names things; the plot has one text lane and it holds
  // prices).
  const out=[], w=scene.walls||{}, p=scene.price||{};

  for(const side of ['call','put']){
    const ladder=w[side];
    if(Array.isArray(ladder)) ladder.forEach((e,i)=>{
      if(e.strike==null||!isFinite(e.strike)) return;
      out.push({y:e.strike, kind:'wall', side, rank:1, nearest:i===0,
                gex:(e.gex!=null&&isFinite(e.gex))?e.gex:null,
                held:e.unchanged_min!=null?e.unchanged_min:e.unchanged_min_at_least,
                heldExact:e.unchanged_min!=null});
    });
    const behind=w[side+'_heaviest_behind'];
    if(behind&&behind.strike!=null&&isFinite(behind.strike))
      out.push({y:behind.strike, kind:'wall', side, rank:1, behind:true,
                gex:(behind.gex!=null&&isFinite(behind.gex))?behind.gex:null});
    if(w[side+'_side_clear']===true) out.push({kind:'clear', side});
  }

  // The magnet's runners-up are drawn, weighted by their own share, and NOT
  // gated on a threshold. sr-3 deleted `is_a_tie` because it was a hardcoded
  // 5.0pp constant shipped as a finding (sndk_read.py:1426-1432); reintroducing
  // any cutoff here would re-import exactly that mistake. Encoding the gap
  // continuously means a near-tie LOOKS like a tie without anyone deciding
  // where a tie begins.
  const mag=(scene.magnet||{}).top_strikes;
  if(Array.isArray(mag)&&mag.length){
    const top=(Array.isArray(mag[0])&&mag[0][1]!=null)?Number(mag[0][1]):null;
    mag.forEach((m,i)=>{
      if(!Array.isArray(m)||m[0]==null||!isFinite(m[0])) return;
      const share=(m[1]!=null&&isFinite(m[1]))?Number(m[1]):null;
      out.push({y:m[0], kind:'magnet', rank:i===0?2:3, lead:i===0,
                share, weight:(share!=null&&top)?Math.max(0.28, share/top):0.5});
    });
  }

  // The flip arrives as SIGMAS, never a price, so converting needs the scene's
  // own ruler. Measured over 1,036 recorded scenes the band is entirely
  // off-window 36% of the time, which is why the painter must be free to draw
  // nothing and the key must follow the painter.
  const flip=(scene.regime||{}).flip, sig=(scene.scale||{}).one_sigma_dollars, sp=p.now;
  if(flip&&sig&&sp!=null&&isFinite(sig)&&isFinite(sp)
     &&flip.ct_sigma!=null&&flip.pt_sigma!=null){
    const hi=sp+Number(flip.ct_sigma)*sig, lo=sp+Number(flip.pt_sigma)*sig;
    if(isFinite(hi)&&isFinite(lo))
      out.push({band:[Math.min(lo,hi), Math.max(lo,hi)], kind:'flip', rank:4});
  }
  return out;
}

function wallTier(gex){
  // Quantised to three steps, because 1.2px against 1.6px is invisible at
  // arm's length outdoors. Measured over 18,510 recorded wall observations the
  // share of the book runs p10 3.5%, p50 7.3%, p90 20.4% — genuinely spread,
  // so weight is worth encoding at all. The rail bar carries the continuum.
  if(gex==null||!isFinite(gex)) return 1.4;      // no datum: a default, never a claim
  if(gex >= 10) return 2.8;
  if(gex >= 5)  return 1.9;
  return 1.2;
}

function railWidth(gex, full){
  // Fixed full-scale, not the scan's own maximum: a per-scan max makes the
  // biggest wall full-width every single scan and destroys comparison between
  // days. 20% of the book is p90 of the recorded tape (20.37% over 18,510
  // observations), so roughly one wall in ten clips — and clipping is marked.
  if(gex==null||!isFinite(gex)) return null;
  const w=Math.max(2, Math.min(full, gex / 20 * full));
  return {w, clipped: gex > 20};
}

function mergeLevels(levels, span){
  // Two levels on one strike is the ordinary case, not the edge case: on the
  // live board of 2026-08-24 the heaviest wall (1400) was ALSO the second
  // magnet, and the nearest wall (1450) was the third. Drawing both means two
  // lines a pixel apart and two numbers fighting for one row.
  //
  // A wall absorbs a magnet rather than the reverse — the wall is the thing
  // price meets, the magnet is a property of where it sits — and the merged
  // line keeps a `magnet` flag so the painter can lay the amber glow beneath
  // the wall's own casing. Losing the confluence because the pixels collided
  // would be the chart editing the board.
  if(!Array.isArray(levels)) return [];
  const tol=(span>0?span:1)*0.006;
  const ruled=levels.filter(l=>l.y!=null&&isFinite(l.y))
                    .sort((a,b)=>(a.rank-b.rank)||(a.y-b.y));
  const other=levels.filter(l=>l.y==null);
  const out=[];
  for(const l of ruled){
    const hit=out.find(o=>Math.abs(o.y-l.y)<=tol);
    if(!hit){ out.push(Object.assign({}, l)); continue; }
    if(l.kind==='magnet'){ hit.magnet=true; hit.magnetLead=hit.magnetLead||!!l.lead;
                           hit.share=hit.share!=null?hit.share:l.share; }
    else if(hit.kind==='magnet'){
      const share=hit.share, wasLead=!!hit.lead;
      Object.assign(hit, l); hit.magnet=true; hit.magnetLead=wasLead; hit.share=share;
    } else if((l.gex||0) > (hit.gex||0)) { Object.assign(hit, l); }
  }
  return out.concat(other);
}

function layoutLabels(desired, gap, top, bottom){
  // Label rows solved, not nudged. The old pass only ever pushed DOWN and only
  // by one row height, so a crowded pair stayed crowded and a low pair walked
  // off the bottom. This is the standard two-pass: separate downward, then, if
  // the run overflows, push the whole run back up and clamp. Pure, so the
  // guarantee it makes — no two rows closer than `gap` — is testable without
  // a browser.
  const n=desired.length;
  if(!n) return [];
  const idx=desired.map((y,i)=>({y,i})).sort((a,b)=>a.y-b.y);
  const out=new Array(n);
  let prev=-Infinity;
  for(const d of idx){
    const y=Math.max(d.y, prev+gap, top);
    out[d.i]=y; prev=y;
  }
  // backward pass: if the last row cleared the floor, walk the run up
  let next=Infinity;
  for(let k=idx.length-1;k>=0;k--){
    const i=idx[k].i;
    out[i]=Math.min(out[i], next-gap, bottom);
    next=out[i];
  }
  // and never above the ceiling, even if that means re-crowding a full column
  let floor=-Infinity;
  for(const d of idx){
    out[d.i]=Math.max(out[d.i], floor+gap, top);
    floor=out[d.i];
  }
  return out;
}

/* ---- the price line ---------------------------------------------------- */

function tapePoints(rows){
  // Diary rows carry ts + spot every couple of minutes, which is the session's
  // own record and the only series that is reliably there. The finer sndk_tape
  // only fills while somebody has the desktop tab open, so it cannot be the
  // chart's spine — a glance app must draw something on a day nobody watched.
  if(!Array.isArray(rows)) return [];
  const out=[];
  for(const r of rows){
    if(!r||r.ticker!=='SNDK') continue;
    if((r.meta||{}).forced) continue;              // off-hours warmups are not tape
    const s=Number(r.spot), t=Date.parse(r.ts);
    if(!isFinite(s)||!isFinite(t)) continue;
    out.push({t, s});
  }
  out.sort((a,b)=>a.t-b.t);
  return out;
}

function chartGeometry(points, levels, spot, box, sigma){
  // A view window wide enough to hold the price path AND the structure that
  // matters, so distance on screen means distance in dollars.
  //
  // Ruled levels widen the window; BANDS never do. The flip band routinely
  // spans multiples of the day's range — on 2026-08-24 it sat $190 above price
  // — and letting it set the scale would squash the session into a flat line
  // to make room for a zone nobody can act on.
  //
  // Levels beyond FAR_SIGMA are exiled too, and the painter marks them with a
  // margin chevron instead. Before this, walls.put[0] set a floor of 1412 and
  // the heaviest wall on the board at 1400 fell outside it — the window was
  // hiding the very thing the chart exists to show.
  const FAR_SIGMA=1.75;
  const far=(sigma&&isFinite(sigma)&&sigma>0)?sigma*FAR_SIGMA:null;
  const vals=[];
  for(const p of points) vals.push(p.s);
  if(spot!=null&&isFinite(spot)) vals.push(spot);
  const exiled=[];
  for(const l of levels){
    if(l.y==null||!isFinite(l.y)) continue;
    if(l.rank>3) continue;
    if(far!=null&&spot!=null&&Math.abs(l.y-spot)>far){ exiled.push(l); continue; }
    vals.push(l.y);
  }
  if(!vals.length) return null;
  let lo=Math.min(...vals), hi=Math.max(...vals);
  if(!(hi>lo)){ lo-=1; hi+=1; }
  const pad=(hi-lo)*0.06;
  lo-=pad; hi+=pad;
  const t0=points.length?points[0].t:0, t1=points.length?points[points.length-1].t:1;
  const span=(t1>t0)?(t1-t0):1;
  const yFor=v=>box.top+(hi-v)/(hi-lo)*(box.h);
  const xFor=t=>box.left+((t-t0)/span)*(box.w);
  return {lo, hi, yFor, xFor, t0, t1, exiled, hasPath: points.length>=2};
}

function linePath(points, geo){
  if(!points.length||!geo) return '';
  let d='';
  points.forEach((p,i)=>{
    d+=(i?'L':'M')+geo.xFor(p.t).toFixed(1)+','+geo.yFor(p.s).toFixed(1);
  });
  return d;
}

/* ---- the price actually worth showing --------------------------------- */

function priorClose(price){
  // The scene ships the PERCENTAGE against prior close but not the close
  // itself, and a live quote needs the close to say anything about the day.
  // Recovering it is exact arithmetic on two numbers the scene already gives.
  const now=price&&price.now, pct=price&&price.vs_prior_close_pct;
  if(now==null||pct==null||!isFinite(now)||!isFinite(pct)) return null;
  const d=1+pct/100;
  if(!isFinite(d)||d===0) return null;
  return now/d;
}

function pickPrice(scene, live, sceneIsLive){
  // The header showed price.now — the price at the last SCAN. On a stale scene
  // that is simply the wrong number: measured 2026-08-24, the scan said 1598
  // while the stock was 1487. A screen glanced at for three seconds cannot
  // show a three-day-old price as though it were the price.
  //
  // So the quote leads whenever there is one. The CHANGE is a different
  // question: the percentage needs a prior close, and the only close derivable
  // here belongs to the scene's own session. Across days that is the wrong
  // close, and a wrong percentage is worse than none — so the change is
  // reported only while the scene is live, and withheld otherwise rather than
  // computed from a stale anchor.
  const p=(scene&&scene.price)||{};
  const q=(live&&live.spot!=null&&isFinite(live.spot))?Number(live.spot):null;
  const shown=(q!=null)?q:p.now;
  if(shown==null||!isFinite(shown)) return null;
  let pct=null;
  if(sceneIsLive){
    if(q==null) pct=p.vs_prior_close_pct;
    else {
      const pc=priorClose(p);
      if(pc) pct=(q/pc-1)*100;
    }
  }
  return {price:shown, pct:(pct!=null&&isFinite(pct))?pct:null, live:q!=null};
}

function livePoint(live, nowMs){
  // The streaming quote as a point on the time axis. It is a real measurement,
  // just from a different instrument than the diary — so it may sit on the
  // chart, but it may never be spliced into the measured path: the stretch
  // between the last scan and now was not observed, and a solid line across it
  // would invent a shape.
  //
  // Timed from `age_s`, not from `ts`. live_spot only stamps `ts` on a FRESH
  // fetch — every cached branch omits the key entirely (snapshot.py:10-11,
  // 21-22 vs 34-37) — and with a 2s cache against a 5s poll a good share of
  // readings are cached. Keying on `ts` meant the quote silently had no time
  // and the reach never drew. `age_s` is present in every branch.
  if(!live||live.spot==null||!isFinite(live.spot)) return null;
  const now=(nowMs==null)?Date.now():nowMs;
  const age=(live.age_s!=null&&isFinite(live.age_s))?Number(live.age_s)*1000:0;
  let t=now-age;
  const stamped=Date.parse(live.ts);
  if(isFinite(stamped)) t=stamped;        // exact when the server gave us one
  return {t, s:Number(live.spot), live:true};
}

function modelRead(rows, nowMs){
  // The reader's own sentence. Written for a human, capped at 24 words, and
  // present on 2,878 of 2,929 recorded rows.
  //
  // It ships with its age or not at all. Measured across the sr-6 store the
  // median reading is 12 minutes old, but p95 is 214 and the oldest on record
  // is 341. A sentence that says "the 1450 put wall has held 62 minutes as a
  // floor" is worth reading at 12 minutes and is a liability at 250, and
  // nothing in the sentence itself says which one you are looking at.
  if(!Array.isArray(rows)) return null;
  let best=null;
  for(const r of rows){
    const line=((r||{}).reading||{}).line;
    if(!line) continue;
    const t=Date.parse(r.reading_ts||r.ts);
    if(!isFinite(t)) continue;
    if(!best||t>best.t) best={t, r};
  }
  if(!best) return null;
  const now=(nowMs==null)?Date.now():nowMs;
  const age=Math.max(0,(now-best.t)/60000);
  const rd=best.r.reading||{};
  const v=rd.vector;
  return {line:String(rd.line),
          vector:(v==='up'||v==='down')?v:null,
          ageMin:age,
          stale:age>30};
}

/* ---- freshness --------------------------------------------------------- */

function freshness(payload, nowMs){
  // A phone cannot tell a frozen screen from a quiet tape, so the screen has to
  // say which it is — and the obvious field lies. When a payload is NOT live,
  // build_scene stamps its clock from the ROW's own timestamp, so
  // clock.book_age_min reads ~0 however old the scan is. Believing it is
  // precisely the failure that let a dead Schwab login look healthy for 3.1
  // days in August: the number was fresh because it was measuring itself.
  // Off-live, age is measured against the wall clock from row_ts instead.
  const scene=(payload&&payload.scene)||{};
  const live=!!(payload&&payload.as_of==='live');
  const now=(nowMs==null)?Date.now():nowMs;
  if(!live){
    const t=Date.parse(payload&&payload.row_ts);
    if(isFinite(t)){
      const mins=Math.max(0,(now-t)/60000);
      return {text:'last scan '+gMinutes(mins)+' ago',
              level: mins<=15?'warn':'bad', stale:true};
    }
    return {text:'last scan · age unknown', level:'bad', stale:true};
  }
  const age=(scene.clock||{}).book_age_min;
  if(age==null) return {text:'live', level:'ok', stale:false};
  const level = age<=2 ? 'ok' : (age<=15 ? 'warn' : 'bad');
  return {text:'book '+gMinutes(age)+' old', level, stale:level==='bad'};
}

if(typeof module!=='undefined'&&module.exports){
  module.exports={gUsd, gSigma, gMinutes, envWords, gammaIsLong, wallBehaviour,
                  beyondWall, farSideNote, bothSidesClear, wallDistance, livePoint, modelRead,
                  nearestWall, priorClose, pickPrice,
                  wallTier, railWidth,
                  drawableLevels, mergeLevels,
                  layoutLabels, tapePoints,
                  chartGeometry, linePath, freshness};
}
