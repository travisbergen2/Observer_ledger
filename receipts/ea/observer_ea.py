"""Three-file observer EA with realistic paper-trading controls.

Windows example:
    py -m pip install -r requirements.txt
    py observer_ea.py --eurusd data\\eurusd.csv --xauusd data\\xauusd.csv

The implementation is deliberately paper/backtest-only. It models spread,
commission, slippage, leverage, risk-based sizing, and a drawdown kill switch
through Backtrader's broker. It does not connect to a live account.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import backtrader as bt

from eurusd_reality import EURUSDRealityModel
from xauusd_reality import XAUUSDRealityModel


class PercentCommission(bt.CommInfoBase):
    """Percentage commission on notional value, with explicit leverage metadata."""

    params = dict(commission=0.00002, leverage=30.0, stocklike=False, percabs=True)

    def _getcommission(self, size, price, pseudoexec):
        return abs(size) * price * self.p.commission


class ObserverActor(bt.Strategy):
    params = dict(
        min_samples=40,
        risk_fraction=0.005,
        max_leverage=3.0,
        stop_vol_multiple=2.5,
        min_stop_pct=0.002,
        max_drawdown=0.20,
        checkpoint="observer_state.json",
        experience_log="experience.jsonl",
        transcript_log="observer_transcript.txt",
        spread_bps=1.5,
        commission_pct=0.00002,
        slippage_pct=0.00005,
        learn=True,
    )

    def __init__(self):
        if len(self.datas) != 2:
            raise ValueError("ObserverActor requires exactly two feeds: EURUSD and XAUUSD")
        checkpoint = Path(self.p.checkpoint)
        saved = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
        self.eurusd = EURUSDRealityModel(saved.get("EURUSD"))
        self.xauusd = XAUUSDRealityModel(saved.get("XAUUSD"))
        self.last_close = {"EURUSD": None, "XAUUSD": None}
        self.last_action = {"EURUSD": 0, "XAUUSD": 0}
        self.last_order = {"EURUSD": None, "XAUUSD": None}
        self.experience_log = Path(self.p.experience_log)
        self.decisions = []
        self.peak_value = None
        self.max_drawdown_observed = 0.0
        self.halted = False
        self.halt_reason = None
        self.trade_count = 0

    def _state(self, data, model):
        state = model.update(float(data.close[0]), float(data.high[0]), float(data.low[0]))
        if state.samples < self.p.min_samples:
            return 0, state
        evidence = state.evidence / max(state.volatility, 1e-7)
        threshold = state.criterion * (1.0 + 0.15 * state.change_score)
        action = 1 if evidence > threshold else -1 if evidence < -threshold else 0
        return action, state

    def _drawdown_guard(self) -> float:
        value = float(self.broker.getvalue())
        self.peak_value = value if self.peak_value is None else max(self.peak_value, value)
        drawdown = 0.0 if self.peak_value <= 0 else 1.0 - value / self.peak_value
        self.max_drawdown_observed = max(self.max_drawdown_observed, drawdown)
        if drawdown >= self.p.max_drawdown and not self.halted:
            self.halted = True
            self.halt_reason = f"max_drawdown={drawdown:.4f}"
            for data in self.datas:
                if self.getposition(data).size:
                    self.close(data=data)
        return drawdown

    def _size_for_risk(self, data, state) -> int:
        """Size so a volatility stop risks risk_fraction of current equity."""
        equity = max(float(self.broker.getvalue()), 0.0)
        price = max(float(data.close[0]), 1e-12)
        stop_distance = max(price * self.p.min_stop_pct,
                            price * max(state.volatility, 1e-7) * self.p.stop_vol_multiple)
        risk_units = equity * self.p.risk_fraction / stop_distance
        leverage_units = equity * self.p.max_leverage / price
        return max(0, int(min(risk_units, leverage_units)))

    def _rebalance(self, data, name, action, state):
        if self.halted:
            return
        desired = action * self._size_for_risk(data, state) if action else 0
        current = int(self.getposition(data).size)
        if desired == current:
            return
        # A target-size order makes reversals and partial de-risking explicit.
        order = self.order_target_size(data=data, target=desired)
        self.last_order[name] = order.ref if order else None

    def _explanation(self, name, action, state, other_action):
        labels = {1: "BUY", -1: "SELL", 0: "HOLD"}
        signal = labels[action]
        evidence = state.evidence / max(state.volatility, 1e-7)
        threshold = state.criterion * (1.0 + 0.15 * state.change_score)
        confirmation = "confirmed by the other market" if other_action in (0, action) else "blocked because the other market disagrees"
        gross = action * state.return_mean
        friction = (self.p.spread_bps / 10000.0) + (2.0 * self.p.slippage_pct) + (2.0 * self.p.commission_pct) if action else 0.0
        net = gross - friction
        if self.halted:
            reason = f"the drawdown guard is active ({self.halt_reason})"
        elif state.samples < self.p.min_samples:
            reason = f"the model is still warming up ({state.samples}/{self.p.min_samples} observations)"
        elif action == 0:
            reason = f"evidence {evidence:.3f} has not cleared threshold {threshold:.3f}"
        else:
            reason = f"evidence {evidence:.3f} cleared threshold {threshold:.3f}; {confirmation}"
        return {
            "action": signal,
            "reason": reason,
            "anticipated_gross_return_pct": 100.0 * gross,
            "estimated_friction_pct": 100.0 * friction,
            "anticipated_net_return_pct": 100.0 * net,
            "horizon": "next bar",
        }, f"{name}: {signal} because {reason}. Anticipated next-bar gross return {100.0 * gross:+.4f}%, estimated spread/slippage/commission {100.0 * friction:.4f}%, and anticipated net return {100.0 * net:+.4f}%."

    def next(self):
        drawdown = self._drawdown_guard()
        action_e, state_e = self._state(self.datas[0], self.eurusd)
        action_x, state_x = self._state(self.datas[1], self.xauusd)
        confirmed_e = action_e if action_x in (0, action_e) else 0
        confirmed_x = action_x if action_e in (0, action_x) else 0
        decisions = {"EURUSD": confirmed_e, "XAUUSD": confirmed_x}
        forecast_e, transcript_e = self._explanation("EURUSD", confirmed_e, state_e, action_x)
        forecast_x, transcript_x = self._explanation("XAUUSD", confirmed_x, state_x, action_e)

        for name, action, data, model in (("EURUSD", confirmed_e, self.datas[0], self.eurusd),
                                           ("XAUUSD", confirmed_x, self.datas[1], self.xauusd)):
            close = float(data.close[0])
            previous = self.last_close[name]
            if previous is not None and self.p.learn:
                model.feedback(close / previous - 1.0, self.last_action[name])
            self.last_close[name] = close
            self.last_action[name] = action

        self._rebalance(self.datas[0], "EURUSD", confirmed_e, state_e)
        self._rebalance(self.datas[1], "XAUUSD", confirmed_x, state_x)
        record = {
            "datetime": self.datas[0].datetime.datetime(0).isoformat(),
            "drawdown": drawdown,
            "halted": self.halted,
            "decisions": decisions,
            "forecast": {"EURUSD": forecast_e, "XAUUSD": forecast_x},
            "transcript": [transcript_e, transcript_x],
            "positions": {"EURUSD": self.getposition(self.datas[0]).size,
                          "XAUUSD": self.getposition(self.datas[1]).size},
            "EURUSD": state_e.to_dict(),
            "XAUUSD": state_x.to_dict(),
        }
        self.decisions.append(record)
        self.experience_log.parent.mkdir(parents=True, exist_ok=True)
        with self.experience_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        transcript_path = Path(self.p.transcript_log)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{record['datetime']}] " + " ".join(record["transcript"]) + "\n")

    def notify_order(self, order):
        if order.status in (order.Completed, order.Canceled, order.Margin, order.Rejected):
            if order.status == order.Completed:
                self.trade_count += 1

    def stop(self):
        state = {"EURUSD": self.eurusd.snapshot(), "XAUUSD": self.xauusd.snapshot()}
        Path(self.p.checkpoint).parent.mkdir(parents=True, exist_ok=True)
        Path(self.p.checkpoint).write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(json.dumps({
            "bars": len(self.decisions),
            "trades": self.trade_count,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "max_drawdown_observed": self.max_drawdown_observed,
            "final_state": state,
            "portfolio_value": self.broker.getvalue(),
        }, indent=2))


class GenericCSV(bt.feeds.GenericCSVData):
    params = dict(dtformat="%Y-%m-%d %H:%M:%S", datetime=0, open=1, high=2, low=3,
                  close=4, volume=5, openinterest=-1, nullvalue=0.0, headers=True)


def run(args: argparse.Namespace) -> dict:
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.broker.setcash(args.cash)
    # Backtrader's percentage slippage is applied to execution prices. The
    # half-spread is added to slippage so each side pays approximately half.
    total_slippage = args.slippage_pct + (args.spread_bps / 20000.0)
    cerebro.broker.set_slippage_perc(total_slippage, slip_open=True, slip_match=True,
                                     slip_out=False, slip_limit=True)
    comminfo = PercentCommission(commission=args.commission_pct, leverage=args.max_leverage)
    cerebro.broker.addcommissioninfo(comminfo, name="EURUSD")
    cerebro.broker.addcommissioninfo(comminfo, name="XAUUSD")
    cerebro.addstrategy(ObserverActor, checkpoint=args.checkpoint,
                        experience_log=args.experience_log, transcript_log=args.transcript_log,
                        spread_bps=args.spread_bps, commission_pct=args.commission_pct,
                        slippage_pct=args.slippage_pct,
                        min_samples=args.min_samples,
                        risk_fraction=args.risk_fraction, max_leverage=args.max_leverage,
                        stop_vol_multiple=args.stop_vol_multiple,
                        min_stop_pct=args.min_stop_pct, max_drawdown=args.max_drawdown,
                        learn=args.learn)
    cerebro.adddata(GenericCSV(dataname=args.eurusd, name="EURUSD"))
    cerebro.adddata(GenericCSV(dataname=args.xauusd, name="XAUUSD"))
    print(f"Starting backtest with ${cerebro.broker.getvalue():.2f}; "
          f"spread={args.spread_bps:g}bp commission={args.commission_pct:g} "
          f"slippage={args.slippage_pct:g} leverage={args.max_leverage:g}x")
    results = cerebro.run()
    strategy = results[0]
    return {
        "start_value": args.cash,
        "final_value": float(cerebro.broker.getvalue()),
        "return_pct": 100.0 * (float(cerebro.broker.getvalue()) / args.cash - 1.0),
        "bars": len(strategy.decisions),
        "trades": strategy.trade_count,
        "halted": strategy.halted,
        "halt_reason": strategy.halt_reason,
        "max_drawdown_observed": strategy.max_drawdown_observed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtrader three-file observer EA")
    parser.add_argument("--eurusd", required=True, help="EURUSD OHLCV CSV")
    parser.add_argument("--xauusd", required=True, help="XAUUSD OHLCV CSV")
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
    parser.add_argument("--checkpoint", default="observer_state.json")
    parser.add_argument("--experience-log", default="experience.jsonl")
    parser.add_argument("--transcript-log", default="observer_transcript.txt")
    parser.add_argument("--no-learn", action="store_true", help="disable feedback learning")
    parser.add_argument("--execute", action="store_true", help="reserved; live execution is not implemented")
    args = parser.parse_args()
    if args.execute:
        raise SystemExit("Live execution is not implemented. Use broker_adapter.py only after adding and reviewing a broker-specific adapter.")
    args.learn = not args.no_learn
    print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
