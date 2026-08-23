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
  const sign=regime.gamma_sign, word=regime.word;
  const lean = sign==null ? null
    : (sign>0||sign==='positive'||sign==='long' ? 'walls hold' : 'walls give way');
  if(word && lean) return String(word)+' · '+lean;
  return word ? String(word) : lean;
}

function gammaIsLong(regime){
  if(!regime) return null;
  const s=regime.gamma_sign;
  if(s==null) return null;
  if(typeof s==='number') return s>0;
  return s==='positive'||s==='long'||s==='+';
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
  // The desktop finds air pockets with snkGaps, which measures the space
  // between one cluster's far edge and the next one's near edge. The scene
  // ships wall PEAKS with no edges, so that measurement cannot be reproduced
  // here and is not approximated — a fabricated gap is worse than none.
  //
  // What the scene does carry is the same fact told two other ways, both
  // computed server-side and both deliberate findings rather than absences:
  // *_side_clear means the board was measured and this side holds nothing
  // between price and open air, and *_heaviest_behind names the heavier
  // cluster sitting behind the one price meets first.
  if(!walls) return null;
  if(walls[side+'_side_clear']===true)
    return {clear:true, text:'Nothing measured beyond — open air on this side.'};
  const behind=walls[side+'_heaviest_behind'];
  if(behind&&behind.strike!=null)
    return {clear:false, strike:behind.strike,
            text:'Heavier wall behind it at '+gUsd(behind.strike,0)+'.'};
  const ladder=walls[side];
  if(Array.isArray(ladder)&&ladder.length>=2&&ladder[1].strike!=null)
    return {clear:false, strike:ladder[1].strike,
            text:'Next wall at '+gUsd(ladder[1].strike,0)+'.'};
  return null;
}

/* ---- which wall price meets first ------------------------------------- */

function farSideNote(walls, side){
  // walls.put_side_clear / call_side_clear are measured-empty: the board WAS
  // read and that side holds nothing between price and open air. On this name
  // price in the clear is often the loudest fact on the board, and it belongs
  // on screen even when it is behind price rather than ahead of it.
  if(!walls||!side) return null;
  const far = side==='call' ? 'put' : 'call';
  if(walls[far+'_side_clear']!==true) return null;
  return (far==='put' ? 'Below' : 'Above') + ': open air — nothing measured on that side.';
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
  // Everything the chart can rule a line for, each carrying its own label and
  // class. Order matters only for paint: walls sit under the magnet, which
  // sits under the flip, because a flip crossing is the loudest of the three.
  const out=[], w=scene.walls||{}, p=scene.price||{};
  (w.put||[]).forEach((e,i)=>{ if(e.strike!=null)
    out.push({y:e.strike, cls:'lv-put', label:'put wall', gex:e.gex,
              held:e.unchanged_min!=null?e.unchanged_min:e.unchanged_min_at_least,
              lead:i===0}); });
  (w.call||[]).forEach((e,i)=>{ if(e.strike!=null)
    out.push({y:e.strike, cls:'lv-call', label:'call wall', gex:e.gex,
              held:e.unchanged_min!=null?e.unchanged_min:e.unchanged_min_at_least,
              lead:i===0}); });
  const mag=(scene.magnet||{}).top_strikes;
  if(Array.isArray(mag)&&mag.length&&Array.isArray(mag[0])&&mag[0][0]!=null)
    out.push({y:mag[0][0], cls:'lv-magnet', label:'magnet', lead:true});
  // The flip is the level where dealer behaviour inverts, so it is the loudest
  // line on the chart — and it arrives as SIGMAS from spot (ct_sigma/pt_sigma
  // are what flip_block measures), never as a price. Converting needs the
  // scene's own ruler; without one sigma in dollars there is no honest line to
  // draw and none is drawn.
  const flip=(scene.regime||{}).flip, sig=(scene.scale||{}).one_sigma_dollars, sp=p.now;
  if(flip&&sig&&sp!=null&&isFinite(sig)&&isFinite(sp)
     &&flip.ct_sigma!=null&&flip.pt_sigma!=null){
    const hi=sp+Number(flip.ct_sigma)*sig, lo=sp+Number(flip.pt_sigma)*sig;
    if(isFinite(hi)&&isFinite(lo))
      out.push({band:[Math.min(lo,hi), Math.max(lo,hi)], cls:'lv-flip',
                label:'flip band', lead:true});
  }
  if(p.session_high!=null) out.push({y:p.session_high, cls:'lv-session', label:'high'});
  if(p.session_low!=null)  out.push({y:p.session_low,  cls:'lv-session', label:'low'});
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

function chartGeometry(points, levels, spot, box){
  // A view window wide enough to hold the price path AND the levels that
  // matter, so distance on screen means distance in dollars. Levels that fall
  // outside are dropped rather than clamped: a line pinned to the frame edge
  // reads as "just off screen" when it may be nowhere near.
  const vals=[];
  for(const p of points) vals.push(p.s);
  if(spot!=null&&isFinite(spot)) vals.push(spot);
  for(const l of levels){
    if(!l.lead) continue;
    if(isFinite(l.y)) vals.push(l.y);
    if(Array.isArray(l.band)){ vals.push(l.band[0]); vals.push(l.band[1]); }
  }
  if(!vals.length) return null;
  let lo=Math.min(...vals), hi=Math.max(...vals);
  if(!(hi>lo)){ lo-=1; hi+=1; }
  const pad=(hi-lo)*0.12;
  lo-=pad; hi+=pad;
  const t0=points.length?points[0].t:0, t1=points.length?points[points.length-1].t:1;
  const span=(t1>t0)?(t1-t0):1;
  const yFor=v=>box.top+(hi-v)/(hi-lo)*(box.h);
  const xFor=t=>box.left+((t-t0)/span)*(box.w);
  return {lo, hi, yFor, xFor, t0, t1};
}

function linePath(points, geo){
  if(!points.length||!geo) return '';
  let d='';
  points.forEach((p,i)=>{
    d+=(i?'L':'M')+geo.xFor(p.t).toFixed(1)+','+geo.yFor(p.s).toFixed(1);
  });
  return d;
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
                  beyondWall, farSideNote, nearestWall, drawableLevels, tapePoints,
                  chartGeometry, linePath, freshness};
}
