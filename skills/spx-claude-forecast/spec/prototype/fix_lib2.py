# read-only: rebuild the 14:30 base rate and the precedent shortlist with the pressure-test fixes applied
#  - seed cut at the diary row nearest the slot (within 2 min), blind otherwise (freshness gate)
#  - outcome from the read's own spot, h30/h60 on the average price from the read minute, to_close on the official close
#  - strict boundaries (|x| <= 1 edge is flat), anchor = diary sigma_anchor at the cut
#  - unit_suspect days (anchor / VIX-implied move < 0.8) kept out of base rates and precedents
#  - precedents: same book (native), slots 14:00/14:30/15:00, <=2 per session, earlier days only, VWAP = median of 3 rows
import json, glob, os, math, statistics as st, collections, hashlib, sys
S = os.path.expanduser('~/.claude/plugins/mirai-station/state')
fac = lambda T: math.sqrt((T + 1) * (2 * T + 1) / (6 * T * T))
def dc(sym):
    return {json.loads(l)['day']: json.loads(l) for l in open(f'{S}/spx_jev/daily_closes/{sym}.jsonl')}
SPX, VIX = dc('$SPX'), dc('$VIX')
def prior(dct, day):
    ks = [k for k in dct if k < day]
    return dct[max(ks)] if ks else None
def b7(x):
    a = abs(x)
    if a <= 1: return 'F'
    s = 'U' if x > 0 else 'D'
    return s + ('1' if a <= 2 else '2' if a <= 3 else '3')
ORDER = ['D3', 'D2', 'D1', 'F', 'U1', 'U2', 'U3']
def hm2m(hm): return int(hm[:2]) * 60 + int(hm[3:5])
def m2hm(m): return f'{m // 60:02d}:{m % 60:02d}'

def day_rows(day):
    out = []
    for l in open(f'{S}/reversion/{day}.jsonl'):
        try: out.append(json.loads(l))
        except Exception: pass
    return out

def build(day, slots, cut_override=None):
    p = f'{S}/reversion/bars/{day}-SPX.json'
    if not os.path.exists(p) or not os.path.exists(f'{S}/reversion/{day}.jsonl'): return []
    bars = json.load(open(p))
    if len(bars) < 389 or day not in SPX: return []
    by = {b['ts'][11:16]: b for b in bars}
    pc_row, pv_row = prior(SPX, day), prior(VIX, day)
    pc, pvix = pc_row['close'], pv_row['close']
    rows = day_rows(day)
    out = []
    for slot in slots:
        s = hm2m(slot)
        if cut_override:
            cands = [r for r in rows if r['ts'][11:26] <= cut_override]
            cut = cands[-1] if cands else None
        else:
            cands = [r for r in rows if s - 2 <= hm2m(r['ts'][11:16]) + int(r['ts'][17:19]) / 60 <= s + 2]
            cut = cands[-1] if cands else None
        if cut is None:
            out.append(dict(day=day, slot=slot, status='blind')); continue
        anc = cut.get('sigma_anchor') or cut.get('sigma')
        vixmove = pc * pvix / 100 / math.sqrt(252)
        avx = anc / vixmove
        t0 = hm2m(cut['ts'][11:16]); spot = cut['spot']
        sec = int(cut['ts'][17:19])
        # finished bars: bar start + 1 min <= cut
        fin = [b for b in bars if hm2m(b['ts'][11:16]) + 1 <= t0 + sec / 60]
        if len(fin) < 2: out.append(dict(day=day, slot=slot, status='blind', why='no_finished_bar')); continue
        C = [b['close'] for b in fin]
        last = C[-1]
        hi = max(b['high'] for b in fin); lo = min(b['low'] for b in fin)
        settled = by.get('09:34', {}).get('close')
        gv = cut.get('gex_views') or {}
        # VWAP: median of the cut row and the two rows before it
        i = rows.index(cut)
        vws = [r.get('vwap') for r in rows[max(0, i - 2):i + 1] if r.get('vwap')]
        vw = st.median(vws) if len(vws) == 3 else None
        fp = dict(day=day, slot=slot, status='built', cut=cut['ts'][11:19], book=('native' if cut.get('gex_source') == 'native' else 'stand_in'),
                  gamma=(gv.get('regime') or '').replace('_gamma', '') or None, anchor=anc, anchor_vs_vix_x=round(avx, 2),
                  m30=(last - C[-31]) / anc if len(C) > 30 else None, m60=(last - C[-61]) / anc if len(C) > 60 else None,
                  price_minus_close=(spot - pc) / anc, price_minus_open=(spot - settled) / anc if settled and t0 >= hm2m('09:35') else None,
                  range_pos=(last - lo) / (hi - lo) if hi > lo else None, range=(hi - lo) / anc,
                  price_minus_vwap=(spot - vw) / anc if vw else None, price_minus_flip=(spot - gv['flip']) / anc if gv.get('flip') else None,
                  gap=(settled - pc) / anc if settled else None, vix_vix3m=cut.get('vix_ts'))
        # outcomes
        T = 16 * 60 - t0
        res = {}
        for H, f, key in ((30, 0.07, 'h30'), (60, 0.11, 'h60')):
            end = t0 + H
            if end > 16 * 60 + 2: continue
            w = [by.get(m2hm(m)) for m in range(t0, min(end, 16 * 60))]
            if any(x is None for x in w): continue
            g = sum(b['close'] - spot for b in w) / len(w)
            res[key] = g / (fac(len(w)) * f * anc)
        res['to_close'] = (SPX[day]['close'] - spot) / (0.07 * anc * (T / 30) ** 0.652)
        fp['out_edges'] = {k: round(v, 2) for k, v in res.items()}
        fp['out_bucket'] = {k: b7(v) for k, v in res.items()}
        out.append(fp)
    return out

days = sorted(os.path.basename(p)[:10] for p in glob.glob(f'{S}/reversion/bars/2026-*-SPX.json'))
days = [d for d in days if '2026-07-20' <= d < '2026-10-09']
lib = []
for d in days:
    lib += build(d, ['14:00', '14:30', '15:00'])
built = [r for r in lib if r['status'] == 'built']
SUS = {'2026-08-17','2026-09-09','2026-10-06','2026-10-07','2026-10-08'}
for r in built: r['unit_suspect'] = r['day'] in SUS or r['anchor_vs_vix_x'] < 0.6
suspect = sorted({r['day'] for r in built if r['unit_suspect']})
print('days', len(days), 'built rows', len(built), 'blind', sum(1 for r in lib if r['status'] != 'built'))
print('unit_suspect days', suspect)
print('blind', [(r['day'], r['slot']) for r in lib if r['status'] != 'built'])
# base rate at 14:30
br = [r for r in built if r['slot'] == '14:30' and not r['unit_suspect']]
print('base-rate sessions at 14:30', len(br))
BR = {}
for h in ('h30', 'h60', 'to_close'):
    c = collections.Counter(r['out_bucket'][h] for r in br if h in r['out_bucket'])
    BR[h] = [c[b] for b in ORDER]
    print(h, BR[h], sum(BR[h]))
# smoothed 7-bucket (+1 each), 3-way derived, floored at 0.02, log loss on flat
def three(v):
    s = [x + 1 for x in v]; n = sum(s)
    p = {'down': sum(s[:3]) / n, 'flat': s[3] / n, 'up': sum(s[4:]) / n}
    lifted = {k: max(v, 0.02) for k, v in p.items()}; t = sum(lifted.values())
    return {k: v / t for k, v in lifted.items()}
for h in BR:
    p = three(BR[h]); print(h, 'base 3-way', {k: round(v, 3) for k, v in p.items()}, 'logloss(flat)', round(-math.log(p['flat']), 3))
# now row
now = build('2026-10-09', ['14:30'], cut_override='14:30:12.458122')[0]
print('NOW', {k: (round(v, 3) if isinstance(v, float) else v) for k, v in now.items()})
# precedents
KEYS = ['m30', 'm60', 'price_minus_close', 'price_minus_open', 'range_pos', 'range', 'price_minus_vwap', 'price_minus_flip', 'gap', 'vix_vix3m']
pool = [r for r in built if r['book'] == now['book'] and not r['unit_suspect'] and r['day'] < '2026-10-09' and len(r['out_edges']) == 3]
sd = {k: st.pstdev([r[k] for r in pool if r.get(k) is not None]) for k in KEYS}
def dist(a, b):
    v = [((a[k] - b[k]) / sd[k]) ** 2 for k in KEYS if a.get(k) is not None and b.get(k) is not None]
    g = 0 if a['gamma'] == b['gamma'] else 1
    v.append(g)
    return math.sqrt(sum(v) / len(v)), len(v)
for r in pool: r['d'], r['n'] = dist(now, r)
pool = [r for r in pool if r['n'] >= 8]
pool.sort(key=lambda r: (round(r['d'], 4), r['day']), reverse=False)
print('pool sessions', len({r['day'] for r in pool}), 'rows', len(pool), 'pool at 14:30 sessions', len({r['day'] for r in pool if r['slot']=='14:30'}))
pick, per = [], collections.Counter()
for r in pool:
    if per[r['day']] >= 2: continue
    pick.append(r); per[r['day']] += 1
    if len(pick) == 10: break
alldays = sorted({r['day'] for r in built} | {'2026-10-09'})
def f2(x): return None if x is None else round(x, 2)
cards = []
for r in pick:
    ago = sum(1 for d in alldays if r['day'] < d <= '2026-10-09')
    c = dict(ago=ago, at=r['slot'], src='seed', book=r['book'], gamma=r['gamma'], **{k: f2(r[k]) for k in KEYS}, out_edges=r['out_edges'])
    c = {k: v for k, v in c.items() if v is not None}
    cid = 'p' + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:5]
    cards.append({'id': cid, **c, '_d': round(r['d'], 2), '_day': r['day']})
for c in cards: print(json.dumps(c))
from collections import Counter
for h in ('h30', 'h60', 'to_close'):
    print('shortlist', h, Counter(b7(c['out_edges'][h]) for c in cards))
print('actual', now['out_edges'], now['out_bucket'])
json.dump({'now': {k: (f2(v) if isinstance(v, float) else v) for k, v in now.items()}, 'cards': cards, 'base_rate': BR, 'base_sessions': len(br),
           'suspect': suspect, 'pool_sessions': len({r['day'] for r in pool})},
          open('/private/tmp/claude-501/-Users-will-Desktop-Mirai-Awakening/3ffd3e1c-b125-40a7-bbc5-e45838ebcfe6/scratchpad/payload_v1/fix_lib.json', 'w'), indent=1)
