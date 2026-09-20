import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from vnpy.trader.object import TickData

from vnpy_ctastrategy.strategies.signals.base import (
    EXECUTOR_DRAWDOWN,
    EXECUTOR_FRICTION_TP,
    SignalResult,
    SignalType,
)
from vnpy_ctastrategy.strategies.signals.tick_signals import (
    BottomFrictionTickSignal,
    FrictionTakeProfitSubSignal,
    ProfitDrawdownSubSignal,
)


VT_SYMBOL = "000001.SZSE"


def _tick(last_price: float, bid_price_1: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        vt_symbol=VT_SYMBOL, last_price=last_price, bid_price_1=bid_price_1
    )


def _strategy(**attrs) -> SimpleNamespace:
    """构造 tick 子信号/总信号所需属性的 mock 策略。

    执行器（``_submit_profit_drawdown_exit`` / ``_submit_friction_take_profit``）记录信号层
    分派过来的执行价到 ``calls``，用于验证"信号出价 + 信号分派执行器"。
    """
    s = SimpleNamespace(
        pos=0,
        pos_avg_price=0.0,
        base_size=1000,
        friction_entry_price=0.0,
        friction_tp_pending=False,
        friction_take_profit_points=2.0,
        # profit_drawdown.check(price, pos, pos_avg_price) 默认返回 False
        profit_drawdown=SimpleNamespace(check=lambda price, pos, avg: False),
    )
    s.calls = []
    s._submit_profit_drawdown_exit = lambda price: s.calls.append(
        (EXECUTOR_DRAWDOWN, price)
    )
    s._submit_friction_take_profit = lambda price: s.calls.append(
        (EXECUTOR_FRICTION_TP, price)
    )
    for name, value in attrs.items():
        setattr(s, name, value)
    return s


# ============================================================
# ProfitDrawdownSubSignal
# ============================================================


class ProfitDrawdownSubSignalTest(unittest.TestCase):
    def test_triggered_clears(self) -> None:
        s = _strategy()
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: True)
        r = ProfitDrawdownSubSignal(VT_SYMBOL, s).on_tick(_tick(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(r.target_pos, 0)
        self.assertEqual(r.reason, "收益回撤清仓")
        self.assertEqual(r.executor, EXECUTOR_DRAWDOWN)
        self.assertEqual(r.price, 10.0)

    def test_exit_price_prefers_bid_price_1(self) -> None:
        """信号出价：买一价有效时用买一价（原策略 on_tick 里的取值逻辑移入信号）。"""
        s = _strategy()
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: True)
        r = ProfitDrawdownSubSignal(VT_SYMBOL, s).on_tick(
            _tick(10.5, bid_price_1=10.4), SignalResult()
        )
        self.assertEqual(r.price, 10.4)

    def test_exit_price_falls_back_to_last_price(self) -> None:
        """信号出价：买一无效（0）时回落最新价。"""
        s = _strategy()
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: True)
        r = ProfitDrawdownSubSignal(VT_SYMBOL, s).on_tick(_tick(10.5), SignalResult())
        self.assertEqual(r.price, 10.5)

    def test_not_triggered_none(self) -> None:
        s = _strategy()
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: False)
        r = ProfitDrawdownSubSignal(VT_SYMBOL, s).on_tick(_tick(10.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_on_bar_is_passthrough(self) -> None:
        s = _strategy()
        prev = SignalResult(type=SignalType.BUY, target_pos=1, reason="x")
        r = ProfitDrawdownSubSignal(VT_SYMBOL, s).on_bar(MagicMock(), prev)
        self.assertIs(r, prev)

    def test_passes_last_price_to_check(self) -> None:
        """子信号把 tick.last_price/pos/pos_avg_price 透传给 profit_drawdown.check。"""
        received: list = []
        s = _strategy(pos=1000, pos_avg_price=10.0)
        s.profit_drawdown = SimpleNamespace(
            check=lambda price, pos, avg: received.append((price, pos, avg)) or False
        )
        ProfitDrawdownSubSignal(VT_SYMBOL, s).on_tick(_tick(12.34), SignalResult())
        self.assertEqual(received, [(12.34, 1000, 10.0)])


# ============================================================
# FrictionTakeProfitSubSignal
# ============================================================


class FrictionTakeProfitSubSignalTest(unittest.TestCase):
    def setUp(self) -> None:
        # entry=10, tp=2% → target_price=10.2
        self.s = _strategy(
            pos=1500, base_size=1000, friction_entry_price=10.0,
            friction_tp_pending=False, friction_take_profit_points=2.0,
        )

    def test_no_entry_price_none(self) -> None:
        self.s.friction_entry_price = 0.0
        r = FrictionTakeProfitSubSignal(VT_SYMBOL, self.s).on_tick(_tick(11.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_pending_none(self) -> None:
        self.s.friction_tp_pending = True
        r = FrictionTakeProfitSubSignal(VT_SYMBOL, self.s).on_tick(_tick(11.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_pos_at_base_none(self) -> None:
        self.s.pos = 1000  # == base_size
        r = FrictionTakeProfitSubSignal(VT_SYMBOL, self.s).on_tick(_tick(11.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_price_below_target_none(self) -> None:
        r = FrictionTakeProfitSubSignal(VT_SYMBOL, self.s).on_tick(_tick(10.19), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_price_at_target_sells_base(self) -> None:
        r = FrictionTakeProfitSubSignal(VT_SYMBOL, self.s).on_tick(_tick(10.2), SignalResult())
        self.assertEqual(r.type, SignalType.SELL)
        self.assertEqual(r.target_pos, 1000)  # base_size
        self.assertEqual(r.reason, "摩擦仓止盈")
        self.assertEqual(r.executor, EXECUTOR_FRICTION_TP)
        self.assertEqual(r.price, 10.2)

    def test_sell_price_uses_bid_price_1_when_valid(self) -> None:
        """卖出价由信号层给出：买一有效用买一价。"""
        r = FrictionTakeProfitSubSignal(VT_SYMBOL, self.s).on_tick(
            _tick(10.3, bid_price_1=10.28), SignalResult()
        )
        self.assertEqual(r.type, SignalType.SELL)
        self.assertEqual(r.price, 10.28)

    def test_price_above_target_sells_base(self) -> None:
        r = FrictionTakeProfitSubSignal(VT_SYMBOL, self.s).on_tick(_tick(10.5), SignalResult())
        self.assertEqual(r.type, SignalType.SELL)
        self.assertEqual(r.target_pos, 1000)

    def test_on_bar_is_passthrough(self) -> None:
        prev = SignalResult(type=SignalType.BUY, target_pos=1, reason="x")
        r = FrictionTakeProfitSubSignal(VT_SYMBOL, self.s).on_bar(MagicMock(), prev)
        self.assertIs(r, prev)


# ============================================================
# BottomFrictionTickSignal 全链短路
# ============================================================


class BottomFrictionTickSignalTest(unittest.TestCase):
    """回撤优先于摩擦止盈，命中即短路。"""

    def _run_chain(self, s) -> SignalResult:
        agg = BottomFrictionTickSignal(s)
        agg.on_tick(_tick(10.5))
        return agg.signal_result(VT_SYMBOL)

    def test_drawdown_short_circuits_friction_tp(self) -> None:
        # 同时命中回撤与摩擦止盈：回撤在前
        s = _strategy(
            pos=1500, base_size=1000, friction_entry_price=10.0,
            friction_tp_pending=False, friction_take_profit_points=2.0,
        )
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: True)
        r = self._run_chain(s)
        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(r.reason, "收益回撤清仓")

    def test_friction_tp_fires_when_no_drawdown(self) -> None:
        s = _strategy(
            pos=1500, base_size=1000, friction_entry_price=10.0,
            friction_tp_pending=False, friction_take_profit_points=2.0,
        )
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: False)
        r = self._run_chain(s)
        self.assertEqual(r.type, SignalType.SELL)
        self.assertEqual(r.reason, "摩擦仓止盈")

    def test_all_none_when_neither_fires(self) -> None:
        s = _strategy(pos=1000, base_size=1000, friction_entry_price=0.0)
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: False)
        r = self._run_chain(s)
        self.assertEqual(r.type, SignalType.NONE)


# ============================================================
# BottomFrictionTickSignal 执行器分派
# ============================================================


class BottomFrictionTickSignalDispatchTest(unittest.TestCase):
    """总信号 on_tick：跑链后按 executor 分派策略执行器，价格用信号层算好的 result.price。

    策略 on_tick 不再判 executor（原 ``if result.executor == "drawdown": ...`` 逻辑已移入信号）。
    """

    def test_drawdown_dispatch_uses_signal_price(self) -> None:
        s = _strategy(pos=1000, pos_avg_price=10.0)
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: True)

        BottomFrictionTickSignal(s).on_tick(_tick(10.5, bid_price_1=10.4))

        self.assertEqual(s.calls, [(EXECUTOR_DRAWDOWN, 10.4)])

    def test_friction_tp_dispatch_uses_signal_price(self) -> None:
        s = _strategy(
            pos=1500, base_size=1000, friction_entry_price=10.0,
            friction_take_profit_points=2.0,
        )

        BottomFrictionTickSignal(s).on_tick(_tick(10.5))

        self.assertEqual(s.calls, [(EXECUTOR_FRICTION_TP, 10.5)])

    def test_drawdown_dispatch_short_circuits_friction_tp_executor(self) -> None:
        s = _strategy(
            pos=1500, base_size=1000, friction_entry_price=10.0,
            friction_take_profit_points=2.0,
        )
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: True)

        BottomFrictionTickSignal(s).on_tick(_tick(10.5))

        self.assertEqual(len(s.calls), 1)
        self.assertEqual(s.calls[0][0], EXECUTOR_DRAWDOWN)

    def test_none_dispatches_nothing(self) -> None:
        s = _strategy(pos=1000, base_size=1000, friction_entry_price=0.0)

        BottomFrictionTickSignal(s).on_tick(_tick(10.5))

        self.assertEqual(s.calls, [])


if __name__ == "__main__":
    unittest.main()
