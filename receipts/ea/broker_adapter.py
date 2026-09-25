"""Synthetic broker and broker adapter boundary for the demonstration room.

No network calls, credentials, or real-account functionality are present. The
synthetic broker is deterministic and intended for visual demonstrations and
integration tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    quantity: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    client_order_id: str | None = None


@dataclass(frozen=True)
class OrderResult:
    accepted: bool
    broker_order_id: str | None
    message: str
    fill_price: float | None = None
    commission: float = 0.0
    slippage: float = 0.0


class BrokerAdapter(Protocol):
    def account_equity(self) -> float: ...
    def positions(self) -> dict[str, float]: ...
    def submit(self, request: OrderRequest) -> OrderResult: ...
    def cancel(self, broker_order_id: str) -> bool: ...
    def close_position(self, symbol: str) -> OrderResult: ...


@dataclass
class SyntheticPosition:
    quantity: float = 0.0
    average_price: float = 0.0
    mark_price: float = 0.0
    realized_pnl: float = 0.0


class SyntheticBroker:
    """Deterministic synthetic broker for Fractal You Universe demonstrations.

    The broker maintains cash, positions, equity, margin usage, and cumulative
    friction. A buy pays half-spread plus slippage; a sell receives the bid-side
    price. Commission is charged on notional. Short positions are allowed only
    while gross notional remains below ``max_leverage * equity``.
    """

    def __init__(self, starting_cash: float = 100_000.0, spread_bps: float = 2.0,
                 commission_pct: float = 0.00002, slippage_pct: float = 0.00005,
                 max_leverage: float = 3.0):
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.spread_bps = float(spread_bps)
        self.commission_pct = float(commission_pct)
        self.slippage_pct = float(slippage_pct)
        self.max_leverage = float(max_leverage)
        self.positions_by_symbol: dict[str, SyntheticPosition] = {}
        self.prices: dict[str, float] = {}
        self.total_commission = 0.0
        self.total_slippage = 0.0
        self._next_id = 1

    @property
    def spread_fraction(self) -> float:
        return self.spread_bps / 10000.0

    def mark(self, symbol: str, price: float) -> None:
        if price <= 0:
            raise ValueError("synthetic prices must be positive")
        self.prices[symbol] = float(price)
        self.positions_by_symbol.setdefault(symbol, SyntheticPosition()).mark_price = float(price)

    def account_equity(self) -> float:
        return self.cash + sum(p.quantity * p.mark_price for p in self.positions_by_symbol.values())

    def gross_notional(self) -> float:
        return sum(abs(p.quantity * p.mark_price) for p in self.positions_by_symbol.values())

    def margin_used(self) -> float:
        return self.gross_notional() / max(self.max_leverage, 1e-9)

    def margin_available(self) -> float:
        return max(0.0, self.account_equity() - self.margin_used())

    def positions(self) -> dict[str, float]:
        return {symbol: position.quantity for symbol, position in self.positions_by_symbol.items()
                if position.quantity}

    def submit(self, request: OrderRequest) -> OrderResult:
        if request.side not in {"buy", "sell"} or request.quantity <= 0:
            return OrderResult(False, None, "invalid side or quantity")
        market = float(request.price or self.prices.get(request.symbol, 0.0))
        if market <= 0:
            return OrderResult(False, None, "no market price available")
        direction = 1.0 if request.side == "buy" else -1.0
        half_spread = self.spread_fraction / 2.0
        fill = market * (1.0 + direction * (half_spread + self.slippage_pct))
        notional = request.quantity * fill
        commission = notional * self.commission_pct
        projected = self.gross_notional() + notional
        if projected / max(self.account_equity(), 1e-9) > self.max_leverage:
            return OrderResult(False, None, "synthetic margin limit exceeded")

        position = self.positions_by_symbol.setdefault(request.symbol, SyntheticPosition())
        old_qty = position.quantity
        new_qty = old_qty + direction * request.quantity
        if old_qty and (old_qty > 0) != (new_qty > 0) and new_qty != 0:
            closed_qty = min(abs(old_qty), request.quantity)
            position.realized_pnl += closed_qty * (market - position.average_price) * (1 if old_qty > 0 else -1)
        if new_qty == 0:
            position.average_price = 0.0
        elif old_qty == 0 or (old_qty > 0) == (new_qty > 0):
            position.average_price = ((abs(old_qty) * position.average_price) + (request.quantity * fill)) / abs(new_qty)
        else:
            position.average_price = fill
        position.quantity = new_qty
        position.mark_price = market
        self.cash -= direction * notional + commission
        self.total_commission += commission
        self.total_slippage += request.quantity * abs(fill - market)
        broker_id = f"SYNTH-{self._next_id:06d}"
        self._next_id += 1
        return OrderResult(True, broker_id, "synthetic order filled", fill, commission, abs(fill - market) * request.quantity)

    def cancel(self, broker_order_id: str) -> bool:
        return broker_order_id.startswith("SYNTH-")

    def close_position(self, symbol: str) -> OrderResult:
        quantity = self.positions_by_symbol.get(symbol, SyntheticPosition()).quantity
        if quantity == 0:
            return OrderResult(True, None, "already flat")
        request = OrderRequest(symbol, "sell" if quantity > 0 else "buy", abs(quantity))
        return self.submit(request)

    def snapshot(self) -> dict:
        equity = self.account_equity()
        return {
            "cash": self.cash,
            "equity": equity,
            "gross_notional": self.gross_notional(),
            "margin_used": self.margin_used(),
            "margin_available": self.margin_available(),
            "positions": self.positions(),
            "total_commission": self.total_commission,
            "total_slippage": self.total_slippage,
            "return_pct": 100.0 * (equity / self.starting_cash - 1.0),
        }


class PaperBrokerAdapter:
    """Backward-compatible lightweight paper adapter."""

    def __init__(self, starting_equity: float = 100_000.0):
        self.equity = float(starting_equity)
        self._positions: dict[str, float] = {}
        self._next_id = 1

    def account_equity(self) -> float:
        return self.equity

    def positions(self) -> dict[str, float]:
        return dict(self._positions)

    def submit(self, request: OrderRequest) -> OrderResult:
        if request.side not in {"buy", "sell"} or request.quantity <= 0:
            return OrderResult(False, None, "invalid side or quantity")
        signed = request.quantity if request.side == "buy" else -request.quantity
        self._positions[request.symbol] = self._positions.get(request.symbol, 0.0) + signed
        broker_id = f"PAPER-{self._next_id:06d}"
        self._next_id += 1
        return OrderResult(True, broker_id, "paper order accepted", request.price)

    def cancel(self, broker_order_id: str) -> bool:
        return broker_order_id.startswith("PAPER-")

    def close_position(self, symbol: str) -> OrderResult:
        current = self._positions.get(symbol, 0.0)
        if current == 0:
            return OrderResult(True, None, "already flat")
        self._positions[symbol] = 0.0
        broker_id = f"PAPER-{self._next_id:06d}"
        self._next_id += 1
        return OrderResult(True, broker_id, "paper position closed")


class LiveBrokerAdapterNotConfigured:
    def __getattr__(self, name):
        raise RuntimeError("No live broker adapter is configured; this project is synthetic-only.")


__all__ = ["BrokerAdapter", "OrderRequest", "OrderResult", "SyntheticBroker", "SyntheticPosition",
           "PaperBrokerAdapter", "LiveBrokerAdapterNotConfigured"]
