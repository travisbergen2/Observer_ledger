"""XAUUSD bounded-observer reality model.

Gold has a distinct volatility and gap profile, so it maintains a separate state
and must be learned independently from EURUSD.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import log
from typing import Optional


@dataclass
class RealityState:
    instrument: str
    estimate: float = 0.0
    return_mean: float = 0.0
    volatility: float = 1e-5
    change_score: float = 0.0
    detected: bool = False
    alpha: float = 0.08
    integration_window: float = 24.0
    gain: float = 1.0
    criterion: float = 2.8
    evidence: float = 0.0
    samples: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class XAUUSDRealityModel:
    """Adaptive XAUUSD state estimator with resettable change detection."""

    def __init__(self, state: Optional[dict] = None):
        self.state = RealityState(**state) if state else RealityState(instrument="XAUUSD")
        self.prev_close: Optional[float] = None

    def update(self, close: float, high: float, low: float) -> RealityState:
        if close <= 0 or high <= 0 or low <= 0:
            raise ValueError("XAUUSD prices must be positive")
        if self.prev_close is None:
            self.prev_close = close
            self.state.estimate = close
            self.state.samples = 1
            return self.state

        ret = log(close / self.prev_close)
        true_range = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close)) / close
        vol = max(0.82 * self.state.volatility + 0.18 * true_range, 1e-7)
        innovation = (ret - self.state.return_mean) / vol
        self.state.change_score = 0.82 * self.state.change_score + 0.18 * abs(innovation)
        self.state.detected = abs(innovation) * self.state.gain > self.state.criterion
        if self.state.detected:
            self.state.evidence = 0.0
            self.state.integration_window = 2.0
            self.state.alpha = min(0.72, max(0.22, self.state.alpha * 1.40))
        else:
            self.state.integration_window = min(120.0, max(3.0, 2.0 / max(self.state.alpha, 1e-6)))
            self.state.alpha = max(0.02, min(0.32, 0.995 * self.state.alpha + 0.005 / self.state.integration_window))

        self.state.return_mean += self.state.alpha * (ret - self.state.return_mean)
        self.state.volatility = vol
        self.state.estimate = close * (1.0 + self.state.return_mean)
        self.state.evidence += self.state.return_mean / vol
        self.state.samples += 1
        self.prev_close = close
        return self.state

    def feedback(self, realized_return: float, action: int) -> None:
        if action == 0:
            return
        correct = action * realized_return > 0
        target = 0.50 if correct else -0.50
        self.state.gain = max(0.25, min(3.2, self.state.gain + 0.02 * (target - (self.state.gain - 1.0))))
        if not correct:
            self.state.criterion = min(5.0, self.state.criterion + 0.02)
        else:
            self.state.criterion = max(1.3, self.state.criterion - 0.005)

    def snapshot(self) -> dict:
        return self.state.to_dict()


def build_model(state: Optional[dict] = None) -> XAUUSDRealityModel:
    return XAUUSDRealityModel(state)


__all__ = ["XAUUSDRealityModel", "RealityState", "build_model"]


if __name__ == "__main__":
    print("XAUUSDRealityModel ready; feed OHLC bars through update().")
