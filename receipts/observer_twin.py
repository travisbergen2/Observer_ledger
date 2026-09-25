#!/usr/bin/env python3
"""observer_twin.py — the Python twin of Room X · The Observer's Ledger.

fractalyouniverse.org/Observer_ledger/  ·  pure Python 3.9+, no third-party packages.

What it is
----------
The page runs, in the browser, the scaffold's observer on synthetic markets. This file is the
same computation in Python, written so the two can be compared number for number:

  * the scaffold's reality models and SyntheticBroker are IMPORTED VERBATIM from ./ea
    (eurusd_reality.py, xauusd_reality.py, broker_adapter.py — unmodified, sha256 in backtrader_receipt.json);
  * the Commit block of observer_ea.py (state → action, cross-market confirmation, risk sizing,
    drawdown guard, feedback, the transcript's anticipated gross / friction / net) is ported here
    line by line, because Backtrader cannot run in a browser;
  * the synthetic worlds (mulberry32 PRNG, Irwin–Hall(12) shocks, regime switching, trend, jumps,
    and the scaffold's own deterministic 180-bar smoke tape) are generated exactly as the page generates them.

`python3 observer_twin.py` writes observer_twin.json (the page's self-check target).
`python3 observer_twin.py --selftest` additionally checks the smoke run against backtrader_receipt.json:
the decision sequence must be identical bar for bar and the final model states equal to 1e-9,
because decisions and learning are broker-independent in the scaffold (they never read equity).

What it is not
--------------
Not a backtest on real prices (the scaffold ships none and this room uses none), not an edge
claim, not evidence about any market. Paper 18 §11(8): "estimation is not edge, and no
profitability claim is made or implied."
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ea"))

from broker_adapter import OrderRequest, SyntheticBroker  # noqa: E402  (verbatim scaffold)
from eurusd_reality import EURUSDRealityModel  # noqa: E402  (verbatim scaffold)
from xauusd_reality import XAUUSDRealityModel  # noqa: E402  (verbatim scaffold)

NAMES = ("EURUSD", "XAUUSD")
MASK = 0xFFFFFFFF

# --------------------------------------------------------------------------------------
# PRNG — mulberry32, the same 32-bit recurrence the page uses (JS: a = (a + 0x6D2B79F5) | 0; ...)
# --------------------------------------------------------------------------------------
class Mulberry32:
    __slots__ = ("a",)

    def __init__(self, seed: int):
        self.a = seed & MASK

    def next(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & MASK
        a = self.a
        t = ((a ^ (a >> 15)) * (a | 1)) & MASK
        t = ((t + (((t ^ (t >> 7)) * (t | 61)) & MASK)) ^ t) & MASK
        return ((t ^ (t >> 14)) & MASK) / 4294967296.0


def irwin_hall(rng: Mulberry32) -> float:
    """Sum of 12 uniforms minus 6: near-Gaussian, and bit-identical across platforms (additions only)."""
    s = 0.0
    for _ in range(12):
        s += rng.next()
    return s - 6.0


# --------------------------------------------------------------------------------------
# Worlds
# --------------------------------------------------------------------------------------
MARKET_SPEC = ({"price": 1.08, "sigma": 0.0012}, {"price": 2350.0, "sigma": 0.006})
WORLD_DEFAULTS = dict(snr=0.25, regime_len=40, couple=1.0, shock_prob=0.02, shock_size=5.0)


def smoke_tapes():
    """The scaffold's make_smoke_data.py tape, including its 8-decimal CSV rounding (Backtrader reads the CSV)."""
    tapes = []
    for start, scale in ((1.08, 0.0005), (2350.0, 2.5)):
        price = start
        rows = []
        for i in range(180):
            drift = scale * (1 if (i // 18) % 2 == 0 else -1)
            shock = scale * 5 if i in (70, 125) else 0
            old = price
            price = max(0.0001, price + drift + shock)
            high = max(old, price) + scale
            low = min(old, price) - scale
            rows.append([float(f"{old:.8f}"), float(f"{high:.8f}"), float(f"{low:.8f}"), float(f"{price:.8f}")])
        tapes.append(rows)
    return tapes[0], tapes[1]


def make_world(preset: str, seed: int, bars: int, **kw):
    """Two OHLC tapes. Draw order per market per bar is fixed (19 draws) so the page and the twin agree."""
    if preset == "smoke":
        return smoke_tapes()
    w = dict(WORLD_DEFAULTS, **kw)
    rng = Mulberry32(seed)
    price = [MARKET_SPEC[0]["price"], MARKET_SPEC[1]["price"]]
    sign = [1.0, 1.0]
    tapes = ([], [])
    for t in range(bars):
        for m in (0, 1):
            sigma = MARKET_SPEC[m]["sigma"]
            u_reg = rng.next()
            u_couple = rng.next()
            u_sign = rng.next()
            z = irwin_hall(rng)
            u_hi = rng.next()
            u_lo = rng.next()
            u_jump = rng.next()
            u_jsign = rng.next()
            if preset == "regime" and (t == 0 or u_reg < 1.0 / w["regime_len"]):
                if m == 1 and u_couple < w["couple"]:
                    sign[m] = sign[0]
                else:
                    sign[m] = 1.0 if u_sign < 0.5 else -1.0
            if preset == "regime":
                mu = sign[m] * w["snr"] * sigma
            elif preset == "trend":
                mu = w["snr"] * sigma
            else:
                mu = 0.0
            r = mu + sigma * z
            if preset == "shock" and u_jump < w["shock_prob"]:
                r += (1.0 if u_jsign < 0.5 else -1.0) * w["shock_size"] * sigma
            o = price[m]
            c = o * math.exp(r)
            h = max(o, c) * math.exp(sigma * u_hi)
            lo = min(o, c) * math.exp(-sigma * u_lo)
            tapes[m].append([o, h, lo, c])
            price[m] = c
    return tapes[0], tapes[1]


# --------------------------------------------------------------------------------------
# The observer — Commit block of observer_ea.py ported line by line, SyntheticBroker verbatim
# --------------------------------------------------------------------------------------
DEFAULTS = dict(cash=100000.0, min_samples=40, risk_fraction=0.005, max_leverage=3.0, max_drawdown=0.20,
                stop_vol_multiple=2.5, min_stop_pct=0.002, spread_bps=1.5, commission_pct=0.00002,
                slippage_pct=0.00005)


def size_for_risk(equity: float, price: float, volatility: float, p: dict) -> int:
    """observer_ea.ObserverActor._size_for_risk, verbatim arithmetic."""
    equity = max(equity, 0.0)
    price = max(price, 1e-12)
    stop_distance = max(price * p["min_stop_pct"], price * max(volatility, 1e-7) * p["stop_vol_multiple"])
    risk_units = equity * p["risk_fraction"] / stop_distance
    leverage_units = equity * p["max_leverage"] / price
    return max(0, int(min(risk_units, leverage_units)))


def run_observer(tape_e, tape_x, p=None, learn=True, init=None, commit_fix=False, keep_series=False):
    """Run the observer over two aligned tapes.

    commit_fix=False : the scaffold as committed — evidence = state.evidence / volatility (the second
                       normalisation; state.evidence is already a sum of return_mean / vol).
    commit_fix=True  : the labelled variant — evidence = state.evidence, compared to the same threshold.
    Decisions at bar t are executed at bar t+1's open (Backtrader's default for market orders).
    """
    p = dict(DEFAULTS, **(p or {}))
    init = init or {}
    broker = SyntheticBroker(p["cash"], p["spread_bps"], p["commission_pct"], p["slippage_pct"], p["max_leverage"])
    models = {"EURUSD": EURUSDRealityModel(init.get("EURUSD")), "XAUUSD": XAUUSDRealityModel(init.get("XAUUSD"))}
    tapes = {"EURUSD": tape_e, "XAUUSD": tape_x}
    n = min(len(tape_e), len(tape_x))
    last_close = {nm: None for nm in NAMES}
    last_action = {nm: 0 for nm in NAMES}
    pending = {nm: None for nm in NAMES}
    friction_rate = (p["spread_bps"] / 10000.0) + (2.0 * p["slippage_pct"]) + (2.0 * p["commission_pct"])
    peak = None
    maxdd = 0.0
    halted = False
    halt_bar = None
    fills = refusals = refusals_reducing = 0
    friction_paid = 0.0
    detections = {nm: 0 for nm in NAMES}
    scored = {nm: 0 for nm in NAMES}
    correct = {nm: 0 for nm in NAMES}
    traded_bars = {nm: 0 for nm in NAMES}
    neg_net_commits = 0
    commits = 0
    over_threshold = 0
    eligible = 0
    max_ratio = 0.0
    decisions = []
    series = []
    for t in range(n):
        bars = {nm: tapes[nm][t] for nm in NAMES}
        # 1 — execute yesterday's targets at today's open (half-spread + slippage + commission via SyntheticBroker)
        for nm in NAMES:
            broker.mark(nm, bars[nm][0])
        for nm in NAMES:
            tgt = pending[nm]
            pending[nm] = None
            if tgt is None:
                continue
            cur = broker.positions().get(nm, 0.0)
            diff = tgt - cur
            if diff == 0:
                continue
            res = broker.submit(OrderRequest(nm, "buy" if diff > 0 else "sell", abs(diff)))
            if res.accepted:
                fills += 1
                friction_paid += res.commission + res.slippage
            else:
                refusals += 1
                if abs(tgt) < abs(cur):
                    refusals_reducing += 1
        # 2 — mark to market at the close; drawdown guard (observer_ea._drawdown_guard)
        for nm in NAMES:
            broker.mark(nm, bars[nm][3])
        value = broker.account_equity()
        peak = value if peak is None else max(peak, value)
        drawdown = 0.0 if peak <= 0 else 1.0 - value / peak
        maxdd = max(maxdd, drawdown)
        if drawdown >= p["max_drawdown"] and not halted:
            halted = True
            halt_bar = t + 1
            for nm in NAMES:
                if broker.positions().get(nm, 0.0):
                    pending[nm] = 0
        # 3 — Estimate + Detect (the models, verbatim) and the raw actions (observer_ea._state)
        acts, states, ratios, thresholds = {}, {}, {}, {}
        for nm in NAMES:
            o, h, lo, c = bars[nm]
            st = models[nm].update(c, h, lo)
            states[nm] = st
            if st.detected:
                detections[nm] += 1
            if st.samples < p["min_samples"]:
                acts[nm] = 0
                ratios[nm] = 0.0
                thresholds[nm] = st.criterion * (1.0 + 0.15 * st.change_score)
                continue
            evidence = st.evidence if commit_fix else st.evidence / max(st.volatility, 1e-7)
            threshold = st.criterion * (1.0 + 0.15 * st.change_score)
            acts[nm] = 1 if evidence > threshold else -1 if evidence < -threshold else 0
            ratios[nm] = evidence
            thresholds[nm] = threshold
            eligible += 1
            if abs(evidence) > threshold:
                over_threshold += 1
            max_ratio = max(max_ratio, abs(evidence) / threshold if threshold > 0 else 0.0)
        a_e, a_x = acts["EURUSD"], acts["XAUUSD"]
        conf = {"EURUSD": a_e if a_x in (0, a_e) else 0, "XAUUSD": a_x if a_e in (0, a_x) else 0}
        # the transcript's anticipated numbers (observer_ea._explanation)
        anticipated = {}
        for nm in NAMES:
            a = conf[nm]
            gross = a * states[nm].return_mean
            friction = friction_rate if a else 0.0
            anticipated[nm] = (gross, friction, gross - friction)
            if a:
                commits += 1
                if gross - friction < 0:
                    neg_net_commits += 1
        # 4 — feedback: the EA scores yesterday's confirmed action against today's close-to-close return
        realized = {}
        for nm in NAMES:
            c = bars[nm][3]
            prev = last_close[nm]
            if prev is not None:
                r = c / prev - 1.0
                if learn:
                    models[nm].feedback(r, last_action[nm])
                if last_action[nm] != 0:
                    scored[nm] += 1
                    if last_action[nm] * r > 0:
                        correct[nm] += 1
                realized[nm] = r
            else:
                realized[nm] = None
            last_close[nm] = c
            last_action[nm] = conf[nm]
            if conf[nm]:
                traded_bars[nm] += 1
        # 5 — rebalance to the risk-sized target (observer_ea._rebalance), executed at the next open
        if not halted:
            for nm in NAMES:
                a = conf[nm]
                desired = a * size_for_risk(value, bars[nm][3], states[nm].volatility, p) if a else 0
                cur = int(broker.positions().get(nm, 0.0))
                if desired != cur:
                    pending[nm] = desired
        decisions.append([conf["EURUSD"], conf["XAUUSD"]])
        if keep_series:
            series.append({
                "bar": t + 1, "value": value, "drawdown": drawdown, "halted": halted,
                "pos": [broker.positions().get("EURUSD", 0.0), broker.positions().get("XAUUSD", 0.0)],
                "action": [conf["EURUSD"], conf["XAUUSD"]],
                "evidence_ratio": [ratios["EURUSD"], ratios["XAUUSD"]],
                "threshold": [thresholds["EURUSD"], thresholds["XAUUSD"]],
                "state": {nm: states[nm].to_dict() for nm in NAMES},
                "anticipated": {nm: list(anticipated[nm]) for nm in NAMES},
            })
    snap = broker.snapshot()
    out = {
        "bars": n,
        "final_value": snap["equity"],
        "return_pct": snap["return_pct"],
        "cash": snap["cash"],
        "gross_notional": snap["gross_notional"],
        "fills": fills,
        "refusals": refusals,
        "refusals_reducing": refusals_reducing,
        "friction_paid": friction_paid,
        "total_commission": snap["total_commission"],
        "total_slippage": snap["total_slippage"],
        "max_drawdown": maxdd,
        "halted": halted,
        "halt_bar": halt_bar,
        "detections": [detections["EURUSD"], detections["XAUUSD"]],
        "traded_bars": [traded_bars["EURUSD"], traded_bars["XAUUSD"]],
        "scored": [scored["EURUSD"], scored["XAUUSD"]],
        "correct": [correct["EURUSD"], correct["XAUUSD"]],
        "accuracy": (correct["EURUSD"] + correct["XAUUSD"]) / max(1, scored["EURUSD"] + scored["XAUUSD"]),
        "commits": commits,
        "neg_net_commits": neg_net_commits,
        "eligible_bars": eligible,
        "over_threshold_bars": over_threshold,
        "max_evidence_over_threshold": max_ratio,
        "final_states": {nm: models[nm].snapshot() for nm in NAMES},
        "decisions": decisions,
    }
    if keep_series:
        out["series"] = series
    return out


# --------------------------------------------------------------------------------------
# The census the page recomputes
# --------------------------------------------------------------------------------------
SEEDS = list(range(1, 25))
BARS = 500
SNR_GRID = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0, 1.5]
ENSEMBLE_WORLDS = {
    "null": dict(preset="null"),
    "regime": dict(preset="regime", snr=0.25, regime_len=40, couple=1.0),
    "shock": dict(preset="shock", shock_prob=0.02, shock_size=5.0),
    "trend": dict(preset="trend", snr=0.25),
}
SUMMARY_FIELDS = ("return_pct", "fills", "friction_paid", "detections_total", "accuracy", "max_drawdown",
                  "criterion_e", "halt")


def q(values, frac):
    """Deterministic quantile: sorted, linear interpolation (page uses the same formula)."""
    s = sorted(values)
    if not s:
        return None
    pos = (len(s) - 1) * frac
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def flat(r):
    return {
        "return_pct": r["return_pct"], "fills": r["fills"], "friction_paid": r["friction_paid"],
        "detections_total": r["detections"][0] + r["detections"][1], "accuracy": r["accuracy"],
        "max_drawdown": r["max_drawdown"], "criterion_e": r["final_states"]["EURUSD"]["criterion"],
        "halt": 1 if r["halted"] else 0,
    }


def summarize(rows):
    return {f: {"median": q([x[f] for x in rows], 0.5), "q1": q([x[f] for x in rows], 0.25),
                "q3": q([x[f] for x in rows], 0.75), "mean": sum(x[f] for x in rows) / len(rows)} for f in SUMMARY_FIELDS}


def build(selftest: bool) -> dict:
    t0 = datetime.now()
    out = {"generated": t0.strftime("%Y-%m-%d"), "seeds": SEEDS, "bars": BARS, "snr_grid": SNR_GRID,
           "market_spec": MARKET_SPEC, "world_defaults": WORLD_DEFAULTS, "defaults": DEFAULTS,
           "prng_check": (lambda r: [r.next() for _ in range(3)])(Mulberry32(12345))}

    # --- the smoke tape: the scaffold's own run, twin vs Backtrader ---
    te, tx = smoke_tapes()
    smoke = run_observer(te, tx, keep_series=True)
    out["smoke"] = {k: v for k, v in smoke.items() if k not in ("series",)}
    out["smoke"]["tape_check"] = {"e_close_48": te[47][3], "x_close_48": tx[47][3], "e_close_180": te[179][3], "x_close_180": tx[179][3]}
    out["smoke"]["first_commit_bar"] = next((i + 1 for i, d in enumerate(smoke["decisions"]) if d[0] or d[1]), None)
    out["smoke"]["value_at"] = {str(b): smoke["series"][b - 1]["value"] for b in (49, 85, 120, 180)}
    smoke_nolearn = run_observer(te, tx, learn=False)
    out["smoke"]["learning_off"] = {"final_value": smoke_nolearn["final_value"], "detections": smoke_nolearn["detections"],
                                    "decisions_changed": sum(1 for a, b in zip(smoke["decisions"], smoke_nolearn["decisions"]) if a != b),
                                    "criterion_e": smoke_nolearn["final_states"]["EURUSD"]["criterion"]}
    smoke_fix = run_observer(te, tx, commit_fix=True)
    out["smoke"]["commit_fix"] = {k: smoke_fix[k] for k in ("final_value", "fills", "traded_bars", "detections", "halted", "halt_bar", "accuracy")}
    out["smoke"]["commit_fix"]["first_commit_bar"] = next((i + 1 for i, d in enumerate(smoke_fix["decisions"]) if d[0] or d[1]), None)

    receipt_path = HERE / "backtrader_receipt.json"
    if receipt_path.exists():
        bt = json.loads(receipt_path.read_text())
        same = sum(1 for a, b in zip(smoke["decisions"], bt["decisions"]) if a == b)
        state_diffs = []
        for nm in NAMES:
            for k, v in bt["final_states"][nm].items():
                mine = smoke["final_states"][nm][k]
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    state_diffs.append(abs(mine - v) / max(1.0, abs(v)))
                else:
                    state_diffs.append(0.0 if mine == v else 1.0)
        out["smoke"]["backtrader"] = {
            "decisions_identical": same, "decisions_total": len(bt["decisions"]),
            "final_state_max_rel_diff": max(state_diffs), "final_value": bt["result"]["final_value"],
            "trades": bt["result"]["trades"], "halted": bt["result"]["halted"], "first_halt_bar": bt["first_halt_bar"],
            "max_drawdown_observed": bt["result"]["max_drawdown_observed"], "detections": bt["detections"],
            "action_counts": bt["action_counts"], "backtrader_version": bt["backtrader_version"],
        }
        if selftest:
            assert same == len(bt["decisions"]) == 180, ("decision mismatch vs Backtrader", same)
            assert max(state_diffs) < 1e-9, ("final-state mismatch vs Backtrader", max(state_diffs))
            print(f"selftest: decisions {same}/{len(bt['decisions'])} identical to Backtrader; final states agree to {max(state_diffs):.2e}")

    # --- the four default worlds at seed 1 (what the page shows on load) ---
    out["default_runs"] = {}
    for wname, w in ENSEMBLE_WORLDS.items():
        kw = {k: v for k, v in w.items() if k != "preset"}
        te, tx = make_world(w["preset"], 1, BARS, **kw)
        r = run_observer(te, tx)
        rf = run_observer(te, tx, commit_fix=True)
        rn = run_observer(te, tx, learn=False)
        out["default_runs"][wname] = {
            "tape_check": {"e_close_last": te[-1][3], "x_close_last": tx[-1][3], "e_high_1": te[0][1]},
            "scaffold": {k: r[k] for k in ("final_value", "return_pct", "fills", "refusals", "friction_paid", "max_drawdown",
                                             "halted", "halt_bar", "detections", "traded_bars", "accuracy", "commits", "neg_net_commits",
                                             "eligible_bars", "over_threshold_bars", "max_evidence_over_threshold")},
            "scaffold_final_states": r["final_states"],
            "commit_fix": {k: rf[k] for k in ("final_value", "return_pct", "fills", "detections", "traded_bars", "accuracy", "halted")},
            "learning_off": {"final_value": rn["final_value"], "detections": rn["detections"],
                             "decisions_changed": sum(1 for a, b in zip(r["decisions"], rn["decisions"]) if a != b)},
        }
        # the "wake the detector" knob: both models start with their criterion scaled (checkpoint-style init)
        for scale in (0.5, 0.25):
            init = {"EURUSD": {"instrument": "EURUSD", "criterion": 2.5 * scale},
                    "XAUUSD": {"instrument": "XAUUSD", "criterion": 2.8 * scale}}
            rc = run_observer(te, tx, init=init)
            out["default_runs"][wname][f"crit_scale_{scale}"] = {
                "final_value": rc["final_value"], "fills": rc["fills"], "detections": rc["detections"],
                "accuracy": rc["accuracy"], "traded_bars": rc["traded_bars"],
                "decisions_changed": sum(1 for a, b in zip(r["decisions"], rc["decisions"]) if a != b),
                "criterion_e": rc["final_states"]["EURUSD"]["criterion"], "alpha_e": rc["final_states"]["EURUSD"]["alpha"],
            }

    # --- ensembles: 24 seeds × 4 worlds × both commit rules ---
    out["ensembles"] = {}
    for wname, w in ENSEMBLE_WORLDS.items():
        kw = {k: v for k, v in w.items() if k != "preset"}
        for fix in (False, True):
            rows = []
            for s in SEEDS:
                te, tx = make_world(w["preset"], s, BARS, **kw)
                rows.append(flat(run_observer(te, tx, commit_fix=fix)))
            out["ensembles"][f"{wname}:{'fix' if fix else 'scaffold'}"] = {
                "summary": summarize(rows), "returns": [x["return_pct"] for x in rows],
                "fills": [x["fills"] for x in rows], "accuracy": [x["accuracy"] for x in rows],
            }

    # --- the operating characteristic: regime world, SNR sweep ---
    out["snr_sweep"] = {}
    for fix in (False, True):
        curve = []
        for snr in SNR_GRID:
            rows = []
            for s in SEEDS:
                te, tx = make_world("regime", s, BARS, snr=snr, regime_len=40, couple=1.0)
                rows.append(flat(run_observer(te, tx, commit_fix=fix)))
            curve.append({"snr": snr, "median_return_pct": q([x["return_pct"] for x in rows], 0.5),
                          "q1": q([x["return_pct"] for x in rows], 0.25), "q3": q([x["return_pct"] for x in rows], 0.75),
                          "median_accuracy": q([x["accuracy"] for x in rows], 0.5),
                          "median_fills": q([x["fills"] for x in rows], 0.5),
                          "frac_positive": sum(1 for x in rows if x["return_pct"] > 0) / len(rows)})
        out["snr_sweep"]["fix" if fix else "scaffold"] = curve

    # --- replay: "feed it back data until it catches up" — the same tape five times with the checkpoint carried,
    #     exactly as observer_ea.py reloads observer_state.json (RealityState(**saved), prev_close reset) ---
    out["replay"] = {}
    for label, (te, tx) in (("smoke", smoke_tapes()), ("regime", make_world("regime", 1, BARS, snr=0.25, regime_len=40, couple=1.0))):
        init = None
        passes = []
        first = None
        for k in range(5):
            r = run_observer(te, tx, init=init)
            first = first or r
            passes.append({"pass": k + 1, "final_value": r["final_value"], "detections": r["detections"],
                           "decisions_changed_vs_pass1": sum(1 for a, b in zip(first["decisions"], r["decisions"]) if a != b),
                           "criterion": [r["final_states"]["EURUSD"]["criterion"], r["final_states"]["XAUUSD"]["criterion"]],
                           "alpha_e": r["final_states"]["EURUSD"]["alpha"], "fills": r["fills"], "accuracy": r["accuracy"],
                           "traded_bars": r["traded_bars"],
                           "first_commit_bar": next((i + 1 for i, d in enumerate(r["decisions"]) if d[0] or d[1]), None)})
            init = r["final_states"]
        out["replay"][label] = passes

    out["elapsed_s"] = (datetime.now() - t0).total_seconds()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default=str(HERE / "observer_twin.json"))
    a = ap.parse_args()
    out = build(a.selftest)
    Path(a.out).write_text(json.dumps(out, indent=1))
    s = out["smoke"]
    print(json.dumps({"smoke_twin": {k: s[k] for k in ("final_value", "fills", "refusals", "refusals_reducing", "halted", "halt_bar",
                                                        "max_drawdown", "detections", "traded_bars", "accuracy", "commits", "neg_net_commits",
                                                        "eligible_bars", "over_threshold_bars", "max_evidence_over_threshold", "first_commit_bar")},
                      "smoke_backtrader": s.get("backtrader"), "smoke_learning_off": s["learning_off"], "smoke_commit_fix": s["commit_fix"]}, indent=1))
    for w, r in out["default_runs"].items():
        print(w, "scaffold", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r["scaffold"].items()})
        print(w, "fix     ", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r["commit_fix"].items()}, "learning_off", r["learning_off"])
    for k, e in out["ensembles"].items():
        print("ensemble", k, {f: round(e["summary"][f]["median"], 4) for f in SUMMARY_FIELDS})
    for k, c in out["snr_sweep"].items():
        print("snr", k, [(p["snr"], round(p["median_return_pct"], 2), round(p["median_accuracy"], 3)) for p in c])
    print("elapsed", round(out["elapsed_s"], 1), "s; wrote", a.out)


if __name__ == "__main__":
    main()
