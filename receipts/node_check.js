// node_check.js — run the page's computational core (PRNG, worlds, models, commit, broker, census helpers)
// under Node and compare it with observer_twin.json and backtrader_receipt.json. No DOM needed.
//   node receipts/node_check.js          (from the repo root, after build_page.py)
'use strict';
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const s = html.indexOf('// ---------- PRNG');
const e = html.indexOf('// ---------- drawing ----------');
if (s < 0 || e < 0) throw new Error('markers not found in index.html');
const core = html.slice(s, e);
const TWIN = JSON.parse(fs.readFileSync(path.join(__dirname, 'observer_twin.json'), 'utf8'));
const BT = JSON.parse(fs.readFileSync(path.join(__dirname, 'backtrader_receipt.json'), 'utf8'));
const NAMES = ['EURUSD', 'XAUUSD'];
// eslint-disable-next-line no-new-func
const lib = new Function('TWIN', 'BT', 'NAMES', core + '\nreturn {mulberry32, irwinHall, smokeTapes, makeWorld, runObserver, q, flat, col, SEEDS, BARS, SNR_GRID, ENSEMBLE_WORLDS, kwOf};')(TWIN, BT, NAMES);

let checks = 0, fails = 0, worst = 0;
function cmp(name, a, b, tol) {
  checks++;
  if (typeof a === 'number' && typeof b === 'number') {
    const d = Math.abs(a - b) / Math.max(1, Math.abs(b));
    worst = Math.max(worst, d);
    if (d > (tol === undefined ? 1e-9 : tol)) { fails++; console.log('FAIL', name, a, b, d); }
  } else if (JSON.stringify(a) !== JSON.stringify(b)) { fails++; console.log('FAIL', name, JSON.stringify(a), JSON.stringify(b)); }
}
// PRNG
const r = lib.mulberry32(12345);
cmp('prng0', r(), TWIN.prng_check[0]); cmp('prng1', r(), TWIN.prng_check[1]); cmp('prng2', r(), TWIN.prng_check[2]);
// smoke vs Backtrader
const sm = lib.smokeTapes();
cmp('smoke e48', sm[0][47][3], TWIN.smoke.tape_check.e_close_48); cmp('smoke x180', sm[1][179][3], TWIN.smoke.tape_check.x_close_180);
const S = lib.runObserver(sm[0], sm[1], { keep_series: true });
let same = 0; for (let i = 0; i < BT.decisions.length; i++) { if (S.decisions[i][0] === BT.decisions[i][0]) same++; if (S.decisions[i][1] === BT.decisions[i][1]) same++; }
cmp('smoke decisions identical', same, 2 * BT.decisions.length);
for (const nm of NAMES) for (const f of ['return_mean', 'volatility', 'change_score', 'alpha', 'integration_window', 'gain', 'criterion', 'evidence', 'samples', 'estimate']) cmp('bt state ' + nm + ' ' + f, S.final_states[nm][f], BT.final_states[nm][f], 1e-12);
for (const k of ['final_value', 'fills', 'refusals', 'friction_paid', 'max_drawdown', 'neg_net_commits', 'over_threshold_bars', 'max_evidence_over_threshold', 'commits', 'eligible_bars', 'accuracy']) cmp('smoke ' + k, S[k], TWIN.smoke[k]);
cmp('smoke value@85', S.series[84].value, TWIN.smoke.value_at['85']);
const Sf = lib.runObserver(sm[0], sm[1], { commit_fix: true }); cmp('smoke fix fv', Sf.final_value, TWIN.smoke.commit_fix.final_value); cmp('smoke fix fills', Sf.fills, TWIN.smoke.commit_fix.fills);
const Sn = lib.runObserver(sm[0], sm[1], { learn: false }); cmp('smoke nolearn fv', Sn.final_value, TWIN.smoke.learning_off.final_value);
// default worlds
for (const w of Object.keys(lib.ENSEMBLE_WORLDS)) {
  const W = lib.ENSEMBLE_WORLDS[w], kw = lib.kwOf(W), T = TWIN.default_runs[w];
  const tapes = lib.makeWorld(W.preset, 1, lib.BARS, kw);
  cmp(w + ' tape last e', tapes[0][tapes[0].length - 1][3], T.tape_check.e_close_last); cmp(w + ' tape high1', tapes[0][0][1], T.tape_check.e_high_1);
  const R = lib.runObserver(tapes[0], tapes[1], {});
  for (const k of ['final_value', 'fills', 'friction_paid', 'max_drawdown', 'accuracy', 'neg_net_commits', 'commits', 'over_threshold_bars']) cmp(w + ' ' + k, R[k], T.scaffold[k]);
  cmp(w + ' detections', R.detections, T.scaffold.detections);
  for (const nm of NAMES) for (const f of ['criterion', 'evidence', 'alpha', 'gain', 'return_mean']) cmp(w + ' state ' + nm + ' ' + f, R.final_states[nm][f], T.scaffold_final_states[nm][f]);
  const Rf = lib.runObserver(tapes[0], tapes[1], { commit_fix: true }); cmp(w + ' fix fv', Rf.final_value, T.commit_fix.final_value);
  for (const scale of [0.5, 0.25]) {
    const init = { EURUSD: { instrument: 'EURUSD', criterion: 2.5 * scale }, XAUUSD: { instrument: 'XAUUSD', criterion: 2.8 * scale } };
    const Rc = lib.runObserver(tapes[0], tapes[1], { init }); cmp(w + ' crit' + scale + ' fv', Rc.final_value, T['crit_scale_' + scale].final_value); cmp(w + ' crit' + scale + ' det', Rc.detections, T['crit_scale_' + scale].detections);
  }
}
// ensembles + sweep
for (const key of Object.keys(TWIN.ensembles)) {
  const [w, rule] = key.split(':'); const W = lib.ENSEMBLE_WORLDS[w], kw = lib.kwOf(W), rows = [];
  for (const seed of lib.SEEDS) { const tapes = lib.makeWorld(W.preset, seed, lib.BARS, kw); rows.push(lib.flat(lib.runObserver(tapes[0], tapes[1], { commit_fix: rule === 'fix' }))); }
  cmp(key + ' median return', lib.q(lib.col(rows, 'return_pct'), 0.5), TWIN.ensembles[key].summary.return_pct.median);
  cmp(key + ' median fills', lib.q(lib.col(rows, 'fills'), 0.5), TWIN.ensembles[key].summary.fills.median);
  cmp(key + ' first seed return', rows[0].return_pct, TWIN.ensembles[key].returns[0]);
  for (let i = 0; i < rows.length; i++) cmp(key + ' seed ' + lib.SEEDS[i], rows[i].return_pct, TWIN.ensembles[key].returns[i]);
}
for (const rule of ['scaffold', 'fix']) for (let k = 0; k < lib.SNR_GRID.length; k++) {
  const rows = [];
  for (const seed of lib.SEEDS) { const tapes = lib.makeWorld('regime', seed, lib.BARS, { snr: lib.SNR_GRID[k], regime_len: 40, couple: 1.0 }); rows.push(lib.flat(lib.runObserver(tapes[0], tapes[1], { commit_fix: rule === 'fix' }))); }
  cmp('sweep ' + rule + ' ' + lib.SNR_GRID[k], lib.q(lib.col(rows, 'return_pct'), 0.5), TWIN.snr_sweep[rule][k].median_return_pct);
}
// replay: five passes with the checkpoint carried
for (const lab of ['smoke', 'regime']) {
  const tapes = lab === 'smoke' ? lib.smokeTapes() : lib.makeWorld('regime', 1, lib.BARS, { snr: 0.25, regime_len: 40, couple: 1.0 });
  let init = null, first = null;
  for (let k = 0; k < 5; k++) {
    const rr = lib.runObserver(tapes[0], tapes[1], { init });
    if (!first) first = rr;
    const T = TWIN.replay[lab][k];
    cmp(lab + ' replay pass ' + (k + 1) + ' crit E', rr.final_states.EURUSD.criterion, T.criterion[0]);
    cmp(lab + ' replay pass ' + (k + 1) + ' crit X', rr.final_states.XAUUSD.criterion, T.criterion[1]);
    cmp(lab + ' replay pass ' + (k + 1) + ' alpha E', rr.final_states.EURUSD.alpha, T.alpha_e);
    cmp(lab + ' replay pass ' + (k + 1) + ' fv', rr.final_value, T.final_value);
    cmp(lab + ' replay pass ' + (k + 1) + ' fills', rr.fills, T.fills);
    let changed = 0; for (let i = 0; i < rr.decisions.length; i++) if (rr.decisions[i][0] !== first.decisions[i][0] || rr.decisions[i][1] !== first.decisions[i][1]) changed++;
    cmp(lab + ' replay pass ' + (k + 1) + ' changed', changed, T.decisions_changed_vs_pass1);
    init = { EURUSD: Object.assign({}, rr.final_states.EURUSD), XAUUSD: Object.assign({}, rr.final_states.XAUUSD) };
  }
}
console.log(`node_check: ${checks - fails} / ${checks} agree with the twin (worst relative difference ${worst.toExponential(2)})`);
process.exit(fails ? 1 : 0);
