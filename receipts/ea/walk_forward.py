"""Walk-forward validation for the observer EA.

Example on Windows PowerShell:
    py walk_forward.py --eurusd data\\eurusd.csv --xauusd data\\xauusd.csv \
        --train-bars 20000 --test-bars 5000 --step-bars 5000

Each window trains on an earlier segment, saves its checkpoint, and evaluates the
next segment with feedback learning disabled. Test inference still updates the
short-term state estimate, but test outcomes do not update the learned gain or
criterion.
"""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from observer_ea import run


HEADER = ["datetime", "open", "high", "low", "close", "volume"]


def load_rows(path: str) -> list[list[str]]:
    with Path(path).open("r", newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows or [c.strip().lower() for c in rows[0][:6]] != HEADER:
        raise ValueError(f"{path} must start with: {','.join(HEADER)}")
    return [row for row in rows[1:] if row and any(cell.strip() for cell in row)]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(rows)


def make_args(eurusd: Path, xauusd: Path, checkpoint: Path, log: Path,
              cash: float, base: argparse.Namespace, learn: bool) -> SimpleNamespace:
    return SimpleNamespace(
        eurusd=str(eurusd), xauusd=str(xauusd), cash=cash, min_samples=base.min_samples,
        risk_fraction=base.risk_fraction, max_leverage=base.max_leverage,
        max_drawdown=base.max_drawdown, stop_vol_multiple=base.stop_vol_multiple,
        min_stop_pct=base.min_stop_pct, spread_bps=base.spread_bps,
        commission_pct=base.commission_pct, slippage_pct=base.slippage_pct,
        checkpoint=str(checkpoint), experience_log=str(log),
        transcript_log=str(log.with_name("transcript.txt")), learn=learn,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validation for observer_ea.py")
    parser.add_argument("--eurusd", required=True)
    parser.add_argument("--xauusd", required=True)
    parser.add_argument("--train-bars", type=int, required=True)
    parser.add_argument("--test-bars", type=int, required=True)
    parser.add_argument("--step-bars", type=int, default=None)
    parser.add_argument("--cash", type=float, default=100000.0)
    parser.add_argument("--min-samples", type=int, default=40)
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--max-leverage", type=float, default=3.0)
    parser.add_argument("--max-drawdown", type=float, default=0.20)
    parser.add_argument("--stop-vol-multiple", type=float, default=2.5)
    parser.add_argument("--min-stop-pct", type=float, default=0.002)
    parser.add_argument("--spread-bps", type=float, default=1.5)
    parser.add_argument("--commission-pct", type=float, default=0.00002)
    parser.add_argument("--slippage-pct", type=float, default=0.00005)
    parser.add_argument("--output", default="walk_forward_results.json")
    args = parser.parse_args()
    step = args.step_bars or args.test_bars
    eurusd = load_rows(args.eurusd)
    xauusd = load_rows(args.xauusd)
    total = min(len(eurusd), len(xauusd))
    if args.train_bars <= 0 or args.test_bars <= 0 or args.train_bars + args.test_bars > total:
        raise SystemExit("train-bars + test-bars must be positive and fit within both feeds")

    results = []
    with tempfile.TemporaryDirectory(prefix="observer_wf_") as tmp:
        root = Path(tmp)
        window = 0
        start = 0
        while start + args.train_bars + args.test_bars <= total:
            train_e, test_e = eurusd[start:start + args.train_bars], eurusd[start + args.train_bars:start + args.train_bars + args.test_bars]
            train_x, test_x = xauusd[start:start + args.train_bars], xauusd[start + args.train_bars:start + args.train_bars + args.test_bars]
            train_dir, test_dir = root / f"w{window}_train", root / f"w{window}_test"
            train_dir.mkdir(); test_dir.mkdir()
            train_e_path, train_x_path = train_dir / "eurusd.csv", train_dir / "xauusd.csv"
            test_e_path, test_x_path = test_dir / "eurusd.csv", test_dir / "xauusd.csv"
            write_csv(train_e_path, train_e); write_csv(train_x_path, train_x)
            write_csv(test_e_path, test_e); write_csv(test_x_path, test_x)
            checkpoint = train_dir / "state.json"
            train_result = run(make_args(train_e_path, train_x_path, checkpoint, train_dir / "experience.jsonl", args.cash, args, True))
            test_result = run(make_args(test_e_path, test_x_path, checkpoint, test_dir / "experience.jsonl", train_result["final_value"], args, False))
            results.append({"window": window, "train_start": train_e[0][0], "test_start": test_e[0][0],
                            "train": train_result, "test": test_result})
            window += 1
            start += step

    summary = {
        "windows": len(results),
        "average_test_return_pct": (sum(r["test"]["return_pct"] for r in results) / len(results)) if results else 0.0,
        "results": results,
    }
    Path(args.output).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
