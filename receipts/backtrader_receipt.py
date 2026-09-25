#!/usr/bin/env python3
"""backtrader_receipt.py — run the committed scaffold, unmodified, on its own smoke tape.

Room X · The Observer's Ledger (fractalyouniverse.org/Observer_ledger/)

This script does exactly what the scaffold's README says to do, in the sandbox instead of
PowerShell: regenerate the 180-bar smoke tape with the scaffold's own generator logic
(make_smoke_data.py, byte-for-byte the same CSV rows), then run observer_ea.py's `run()` under
Backtrader with every default unchanged. It records the receipt the page checks against:

  * the confirmed decision per bar per market (broker-independent, so it must match the twin exactly)
  * the final reality-model states (22 numbers, broker-independent)
  * per-bar drawdown / halted flags and the summary Backtrader itself prints
  * sha256 of every scaffold file it imported

Requires: backtrader==1.9.78.123 (pip install -r ea/requirements.txt). Output: backtrader_receipt.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
EA = HERE / "ea"
sys.path.insert(0, str(EA))

import backtrader as bt  # noqa: E402
import observer_ea  # noqa: E402  (the scaffold, verbatim)


def write_smoke(out: Path) -> None:
    """Verbatim body of ea/make_smoke_data.py, parameterised only by the output folder."""
    out.mkdir(parents=True, exist_ok=True)
    for name, start, scale in [("eurusd", 1.08, 0.0005), ("xauusd", 2350.0, 2.5)]:
        price = start
        lines = ["datetime,open,high,low,close,volume"]
        t = datetime(2024, 1, 1)
        for i in range(180):
            drift = scale * (1 if (i // 18) % 2 == 0 else -1)
            shock = scale * 5 if i in (70, 125) else 0
            old = price
            price = max(0.0001, price + drift + shock)
            high = max(old, price) + scale
            low = min(old, price) - scale
            lines.append(f'{(t + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")},{old:.8f},{high:.8f},{low:.8f},{price:.8f},{1000+i}')
        (out / f"{name}.csv").write_text("\n".join(lines) + "\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "backtrader_receipt.json"))
    ap.add_argument("--smoke-dir", default=str(HERE / "smoke"))
    a = ap.parse_args()

    smoke = Path(a.smoke_dir)
    write_smoke(smoke)

    with tempfile.TemporaryDirectory(prefix="observer_receipt_") as tmp:
        tmpd = Path(tmp)
        args = argparse.Namespace(
            eurusd=str(smoke / "eurusd.csv"), xauusd=str(smoke / "xauusd.csv"), cash=100000.0,
            min_samples=40, risk_fraction=0.005, max_leverage=3.0, max_drawdown=0.20,
            stop_vol_multiple=2.5, min_stop_pct=0.002, spread_bps=1.5, commission_pct=0.00002,
            slippage_pct=0.00005, checkpoint=str(tmpd / "state.json"),
            experience_log=str(tmpd / "experience.jsonl"), transcript_log=str(tmpd / "transcript.txt"),
            learn=True,
        )
        result = observer_ea.run(args)
        records = [json.loads(line) for line in (tmpd / "experience.jsonl").read_text().splitlines() if line.strip()]
        transcript = (tmpd / "transcript.txt").read_text().splitlines()

    decisions = [[r["decisions"]["EURUSD"], r["decisions"]["XAUUSD"]] for r in records]
    final_states = {name: records[-1][name] for name in ("EURUSD", "XAUUSD")}
    per_bar = [{"bar": i + 1, "drawdown": r["drawdown"], "halted": r["halted"],
                "pos": [r["positions"]["EURUSD"], r["positions"]["XAUUSD"]],
                "evidence": [r["EURUSD"]["evidence"], r["XAUUSD"]["evidence"]],
                "criterion": [r["EURUSD"]["criterion"], r["XAUUSD"]["criterion"]],
                "detected": [r["EURUSD"]["detected"], r["XAUUSD"]["detected"]]}
               for i, r in enumerate(records)]
    receipt = {
        "generated": datetime.utcnow().strftime("%Y-%m-%d"),
        "backtrader_version": bt.__version__,
        "python": sys.version.split()[0],
        "scaffold_sha256": {p.name: sha256(p) for p in sorted(EA.iterdir()) if p.is_file()},
        "smoke_sha256": {p.name: sha256(p) for p in sorted(smoke.iterdir()) if p.is_file()},
        "defaults": {k: getattr(args, k) for k in ("cash", "min_samples", "risk_fraction", "max_leverage", "max_drawdown",
                                                    "stop_vol_multiple", "min_stop_pct", "spread_bps", "commission_pct", "slippage_pct")},
        "result": result,
        "bars": len(records),
        "decisions": decisions,
        "final_states": final_states,
        "first_halt_bar": next((p["bar"] for p in per_bar if p["halted"]), None),
        "detections": [sum(1 for p in per_bar if p["detected"][0]), sum(1 for p in per_bar if p["detected"][1])],
        "action_counts": {name: {str(k): sum(1 for d in decisions if d[j] == k) for k in (-1, 0, 1)}
                          for j, name in enumerate(("EURUSD", "XAUUSD"))},
        "per_bar": per_bar,
        "transcript_first_commit": next((line for line in transcript if "BUY" in line or "SELL" in line), None),
        "transcript_last": transcript[-1] if transcript else None,
    }
    Path(a.out).write_text(json.dumps(receipt, indent=1))
    print(json.dumps({k: receipt[k] for k in ("backtrader_version", "result", "first_halt_bar", "detections", "action_counts")}, indent=2))
    print("final EURUSD criterion", final_states["EURUSD"]["criterion"], "evidence", final_states["EURUSD"]["evidence"])
    print("first commit:", receipt["transcript_first_commit"])
    print("wrote", a.out)


if __name__ == "__main__":
    main()
