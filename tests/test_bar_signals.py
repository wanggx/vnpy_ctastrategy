import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from vnpy.trader.object import BarData

from vnpy_ctastrategy.strategies.signals.bar_signals import (
    BelowBaseSubSignal,
    BottomFrictionBarPriority,
    BottomFrictionBarSignal,
    BreakMa10SubSignal,
    FrictionSubSignal,
    MaAlignmentSubSignal,
    ProfitDrawdownBarSubSignal,
    SectorStrongStopExitSubSignal,
    SentimentRiskSubSignal,
    StopLossSubSignal,
)
from vnpy_ctastrategy.strategies.signals.base import (
    EXECUTOR_DRAWDOWN,
    EXECUTOR_STOP_EXIT,
    SignalResult,
    SignalType,
)
from vnpy_ctastrategy.strategies.signals.market_sentiment import (
    SECTOR_STRONG_ENTRY_MARK,
)
from vnpy_ctastrategy.strategies.signals.friction import FrictionResult
from vnpy_ctastrategy.strategies.signals.market_sentiment import SentimentResult


VT_SYMBOL = "000001.SZSE"


def _bar(close_price: float) -> SimpleNamespace:
    return SimpleNamespace(close_price=close_price, vt_symbol=VT_SYMBOL)


def _strategy(**attrs) -> SimpleNamespace:
    """构造 bar 子信号所需属性的 mock 策略。

    SentimentRiskSubSignal 经 ``strategy.bar_signal.sentiment_signal`` 访问情绪信号
    （由 bar_signal 持有，不再是策略顶层属性）。``_intraday_ready`` 门控 6 决策链。
    """
    sentiment = attrs.pop("sentiment_signal", None) or SimpleNamespace(on_bar=lambda bar: SentimentResult(
        risk_level=0, score=0.0, declining_count=0, declining_sector_count=0,
        valid_sector_count=0, declining_sector_ratio=0.0, target_pos=-1,
        mark="", should_block=False,
    ))
    defaults = dict(
        pos=0,
        pos_avg_price=0.0,
        stop_loss_points=5.0,
        ma10=0.0,
        fast_ma=0.0,
        slow_ma=0.0,
        base_size=1000,
        friction_size=500,
        friction_entry_price=0.0,
        sector_score_exit_threshold=40.0,
        sector_strong_entry_price=0.0,
        sector_strong_take_profit_points=2.0,
        _friction_result=None,
        _intraday_ready=True,
        bar_signal=SimpleNamespace(sentiment_signal=sentiment),
        profit_drawdown=SimpleNamespace(check=lambda price, pos, avg: False),
        market_sentiment_score=0.0,
        market_declining_count=0,
    )
    defaults.update(attrs)
    return SimpleNamespace(**defaults)


# ============================================================
# StopLossSubSignal
# ============================================================


class StopLossSubSignalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bar = _bar(9.4)  # pos_avg_price=10 → loss=6%

    def test_loss_exceeds_threshold_clears(self) -> None:
        s = _strategy(pos=1000, pos_avg_price=10.0, stop_loss_points=5.0)
        r = StopLossSubSignal(VT_SYMBOL, s).on_bar(self.bar, SignalResult())

        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(r.target_pos, 0)
        self.assertEqual(r.reason, "止损")

    def test_loss_within_threshold_none(self) -> None:
        s = _strategy(pos=1000, pos_avg_price=10.0, stop_loss_points=7.0)
        r = StopLossSubSignal(VT_SYMBOL, s).on_bar(self.bar, SignalResult())

        self.assertEqual(r.type, SignalType.NONE)

    def test_no_position_none(self) -> None:
        s = _strategy(pos=0, pos_avg_price=10.0, stop_loss_points=5.0)
        r = StopLossSubSignal(VT_SYMBOL, s).on_bar(self.bar, SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_no_avg_price_none(self) -> None:
        s = _strategy(pos=1000, pos_avg_price=0.0, stop_loss_points=5.0)
        r = StopLossSubSignal(VT_SYMBOL, s).on_bar(self.bar, SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_on_tick_is_passthrough(self) -> None:
        s = _strategy()
        prev = SignalResult(type=SignalType.BUY, target_pos=1, reason="x")
        r = StopLossSubSignal(VT_SYMBOL, s).on_tick(MagicMock(), prev)
        self.assertIs(r, prev)


# ============================================================
# BreakMa10SubSignal
# ============================================================


class BreakMa10SubSignalTest(unittest.TestCase):
    def test_close_below_ma10_clears(self) -> None:
        s = _strategy(ma10=10.0)
        r = BreakMa10SubSignal(VT_SYMBOL, s).on_bar(_bar(9.5), SignalResult())

        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(r.target_pos, 0)
        self.assertEqual(r.reason, "跌破10日线")

    def test_close_above_ma10_none(self) -> None:
        s = _strategy(ma10=10.0)
        r = BreakMa10SubSignal(VT_SYMBOL, s).on_bar(_bar(10.5), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_close_equal_ma10_none(self) -> None:
        s = _strategy(ma10=10.0)
        r = BreakMa10SubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)


# ============================================================
# SentimentRiskSubSignal
# ============================================================


def _sentiment_result(**kw) -> SentimentResult:
    base = dict(
        risk_level=0, score=50.0, declining_count=1000,
        declining_sector_count=0, valid_sector_count=3,
        declining_sector_ratio=0.0, target_pos=-1, mark="", should_block=False,
    )
    base.update(kw)
    return SentimentResult(**base)


class SentimentRiskSubSignalTest(unittest.TestCase):
    def test_block_with_target_zero_clears_and_syncs_ui(self) -> None:
        s = _strategy()
        s.bar_signal.sentiment_signal = SimpleNamespace(
            on_bar=lambda bar: _sentiment_result(
                risk_level=2, score=20.0, declining_count=4001,
                declining_sector_count=1, valid_sector_count=2,
                declining_sector_ratio=0.5, target_pos=0,
                mark="市场超过4000家下跌，清仓", should_block=True,
            )
        )
        sub = SentimentRiskSubSignal(VT_SYMBOL, s)
        r = sub.on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(r.target_pos, 0)
        self.assertEqual(r.reason, "市场超过4000家下跌，清仓")
        # UI 变量已同步（只暴露情绪分 + 下跌家数，其余为信号内部状态）
        self.assertEqual(s.market_sentiment_score, 20.0)
        self.assertEqual(s.market_declining_count, 4001)

    def test_block_with_target_positive_sells(self) -> None:
        s = _strategy(pos=1000)
        s.bar_signal.sentiment_signal = SimpleNamespace(
            on_bar=lambda bar: _sentiment_result(
                risk_level=1, target_pos=500,
                mark="市场和板块偏弱，仓位减半", should_block=True,
            )
        )
        r = SentimentRiskSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.SELL)
        self.assertEqual(r.target_pos, 500)
        self.assertEqual(r.reason, "市场和板块偏弱，仓位减半")

    def test_block_with_base_target_sells_friction_position(self) -> None:
        """行业情绪偏弱：target_pos=base_size → SELL(底仓)，标注带行业名。"""
        s = _strategy(pos=1500, base_size=1000)
        s.bar_signal.sentiment_signal = SimpleNamespace(
            on_bar=lambda bar: _sentiment_result(
                risk_level=0, sector_name="汽车零部件", sector_score=35.0,
                target_pos=1000,
                mark="汽车零部件情绪偏弱，减摩擦仓", should_block=True,
            )
        )
        r = SentimentRiskSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.SELL)
        self.assertEqual(r.target_pos, 1000)  # base_size
        self.assertEqual(r.reason, "汽车零部件情绪偏弱，减摩擦仓")

    def test_block_with_strong_sector_buys_friction_position(self) -> None:
        """行业极强：target_pos 高于持仓 → BUY(底仓+摩擦仓)，标注=行业极强入场。"""
        s = _strategy(pos=1000, base_size=1000, friction_size=500)
        s.bar_signal.sentiment_signal = SimpleNamespace(
            on_bar=lambda bar: _sentiment_result(
                risk_level=0, sector_name="汽车零部件", sector_score=85.0,
                target_pos=1500, mark=SECTOR_STRONG_ENTRY_MARK, should_block=True,
            )
        )
        r = SentimentRiskSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.BUY)
        self.assertEqual(r.target_pos, 1500)
        self.assertEqual(r.reason, SECTOR_STRONG_ENTRY_MARK)

    def test_not_block_returns_none_but_syncs_ui(self) -> None:
        """should_block=False 仍同步可观测字段，但返回 NONE。"""
        s = _strategy()
        s.bar_signal.sentiment_signal = SimpleNamespace(
            on_bar=lambda bar: _sentiment_result(
                score=65.0, declining_count=800, should_block=False,
            )
        )
        r = SentimentRiskSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.NONE)
        self.assertEqual(s.market_sentiment_score, 65.0)


# ============================================================
# MaAlignmentSubSignal / BelowBaseSubSignal
# ============================================================


class MaAlignmentSubSignalTest(unittest.TestCase):
    def test_fast_above_slow_buys_base(self) -> None:
        s = _strategy(fast_ma=11.0, slow_ma=10.0, base_size=1000)
        r = MaAlignmentSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.BUY)
        self.assertEqual(r.target_pos, 1000)
        self.assertEqual(r.reason, "均线多头排列，回到底仓")

    def test_fast_below_slow_none(self) -> None:
        s = _strategy(fast_ma=9.0, slow_ma=10.0)
        r = MaAlignmentSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)


class BelowBaseSubSignalTest(unittest.TestCase):
    def test_pos_below_base_buys_base(self) -> None:
        s = _strategy(pos=500, base_size=1000)
        r = BelowBaseSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.BUY)
        self.assertEqual(r.target_pos, 1000)
        self.assertEqual(r.reason, "仓位低于底仓，补仓")

    def test_pos_at_base_none(self) -> None:
        s = _strategy(pos=1000, base_size=1000)
        r = BelowBaseSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)


# ============================================================
# FrictionSubSignal
# ============================================================


def _friction_result(**kw) -> FrictionResult:
    base = dict(
        target_pos=0, mark="", entered=False,
        macd_line=0.0, signal_line=0.0, macd_hist=0.0, last_macd_hist=0.0,
    )
    base.update(kw)
    return FrictionResult(**base)


class FrictionSubSignalTest(unittest.TestCase):
    def test_no_friction_result_none(self) -> None:
        s = _strategy(_friction_result=None)
        r = FrictionSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)

    def test_no_cross_mark_empty_none(self) -> None:
        s = _strategy(pos=1000, _friction_result=_friction_result(
            target_pos=1000, mark="", entered=False
        ))
        r = FrictionSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)
        # 无交叉不记入场价
        self.assertEqual(s.friction_entry_price, 0.0)

    def test_cross_up_buys_and_records_entry_price(self) -> None:
        s = _strategy(pos=1000, friction_entry_price=0.0, _friction_result=_friction_result(
            target_pos=1500, mark="MACD上穿零轴", entered=True
        ))
        r = FrictionSubSignal(VT_SYMBOL, s).on_bar(_bar(10.5), SignalResult())

        self.assertEqual(r.type, SignalType.BUY)
        self.assertEqual(r.target_pos, 1500)
        self.assertEqual(r.reason, "MACD上穿零轴")
        self.assertEqual(s.friction_entry_price, 10.5)

    def test_cross_down_sells_and_does_not_record_entry_price(self) -> None:
        s = _strategy(pos=1500, friction_entry_price=10.5, _friction_result=_friction_result(
            target_pos=1000, mark="MACD下穿零轴，减摩擦仓", entered=False
        ))
        r = FrictionSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.SELL)
        self.assertEqual(r.target_pos, 1000)
        self.assertEqual(s.friction_entry_price, 10.5)  # 未改写

    def test_cross_target_equals_pos_returns_buy(self) -> None:
        """target_pos == pos（已在最大仓上穿）：返回 BUY，由 _set_target_position 判 no-op。"""
        s = _strategy(pos=1500, _friction_result=_friction_result(
            target_pos=1500, mark="MACD上穿零轴", entered=True
        ))
        r = FrictionSubSignal(VT_SYMBOL, s).on_bar(_bar(10.5), SignalResult())
        self.assertEqual(r.type, SignalType.BUY)
        self.assertEqual(r.target_pos, 1500)


# ============================================================
# BottomFrictionBarPriority 6 决策链短路顺序（intraday 门控）
# ============================================================


class BottomFrictionBarPriorityTest(unittest.TestCase):
    """6 决策链短路：止损 > 破10日线 > 情绪 > 均线 > 补仓 > 摩擦。"""

    def _run_chain(self, s, close_price: float = 10.0) -> SignalResult:
        priority = BottomFrictionBarPriority(VT_SYMBOL, s)
        return priority.on_bar(_bar(close_price), SignalResult())

    def test_not_ready_returns_none(self) -> None:
        """_intraday_ready=False（预热/盘前）：6 决策链直接 NONE。"""
        s = _strategy(_intraday_ready=False, pos=1000, pos_avg_price=10.0,
                      stop_loss_points=5.0, ma10=11.0)
        r = self._run_chain(s, close_price=9.4)
        self.assertEqual(r.type, SignalType.NONE)

    def test_stop_loss_wins_over_ma10_break(self) -> None:
        # 同时满足止损(6%)与破10日线：止损在前。close=9.4 → loss=6% > 5% 且 < ma10=11
        s = _strategy(
            pos=1000, pos_avg_price=10.0, stop_loss_points=5.0, ma10=11.0
        )
        r = self._run_chain(s, close_price=9.4)
        self.assertEqual(r.reason, "止损")

    def test_ma10_wins_over_sentiment_when_no_stop(self) -> None:
        # 破10日线、且情绪 should_block：破10日线在前。close=9.5 < ma10=11，loss=5% 不超 5%
        s = _strategy(pos=1000, pos_avg_price=10.0, stop_loss_points=5.0, ma10=11.0)
        s.bar_signal.sentiment_signal = SimpleNamespace(
            on_bar=lambda bar: _sentiment_result(
                target_pos=0, mark="市场清仓", should_block=True
            )
        )
        r = self._run_chain(s, close_price=9.5)
        self.assertEqual(r.reason, "跌破10日线")

    def test_sentiment_wins_over_ma_alignment(self) -> None:
        # 无止损(loss=0)、未破10日线(close=10>=ma10=9)、情绪 should_block：情绪在前
        s = _strategy(
            pos=1000, pos_avg_price=10.0, stop_loss_points=5.0, ma10=9.0,
            fast_ma=11.0, slow_ma=10.0, base_size=1000,
        )
        s.bar_signal.sentiment_signal = SimpleNamespace(
            on_bar=lambda bar: _sentiment_result(
                target_pos=500, mark="市场和板块偏弱，仓位减半", should_block=True
            )
        )
        r = self._run_chain(s, close_price=10.0)
        self.assertEqual(r.reason, "市场和板块偏弱，仓位减半")

    def test_ma_alignment_wins_over_friction_when_no_risk(self) -> None:
        # 均线多头 + 摩擦上穿：均线在前。无止损、未破10日线、情绪正常
        s = _strategy(
            pos=1000, pos_avg_price=10.0, stop_loss_points=5.0, ma10=9.0,
            fast_ma=11.0, slow_ma=10.0, base_size=1000,
            _friction_result=_friction_result(
                target_pos=1500, mark="MACD上穿零轴", entered=True
            ),
        )
        r = self._run_chain(s, close_price=10.0)
        self.assertEqual(r.reason, "均线多头排列，回到底仓")

    def test_all_none_when_no_rule_fires(self) -> None:
        s = _strategy(
            pos=1000, pos_avg_price=10.0, stop_loss_points=5.0, ma10=9.0,
            fast_ma=9.0, slow_ma=10.0, base_size=1000,
            _friction_result=_friction_result(target_pos=1000, mark=""),
        )
        r = self._run_chain(s, close_price=10.0)
        self.assertEqual(r.type, SignalType.NONE)

    def test_friction_wins_when_no_higher_rule_fires(self) -> None:
        # 无止损、未破10日线、情绪正常、均线空头、pos>=base：摩擦上穿胜出
        s = _strategy(
            pos=1000, pos_avg_price=10.0, stop_loss_points=5.0, ma10=9.0,
            fast_ma=9.0, slow_ma=10.0, base_size=1000,
            _friction_result=_friction_result(
                target_pos=1500, mark="MACD上穿零轴", entered=True
            ),
        )
        r = self._run_chain(s, close_price=10.5)
        self.assertEqual(r.type, SignalType.BUY)
        self.assertEqual(r.target_pos, 1500)
        self.assertEqual(s.friction_entry_price, 10.5)


# ============================================================
# ProfitDrawdownBarSubSignal
# ============================================================


class ProfitDrawdownBarSubSignalTest(unittest.TestCase):
    """收益回撤 bar 子信号：经 profit_drawdown.check 判定，触发 → CLEAR(drawdown, 带收盘价)。"""

    def test_triggered_clears_drawdown(self) -> None:
        s = _strategy()
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: True)
        r = ProfitDrawdownBarSubSignal(VT_SYMBOL, s).on_bar(_bar(8.0), SignalResult())

        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(r.target_pos, 0)
        self.assertEqual(r.reason, "收益回撤清仓")
        self.assertEqual(r.executor, EXECUTOR_DRAWDOWN)
        # 执行价由信号层给出（bar 收盘价），策略执行器不再自己取价
        self.assertEqual(r.price, 8.0)

    def test_not_triggered_none(self) -> None:
        s = _strategy()
        s.profit_drawdown = SimpleNamespace(check=lambda price, pos, avg: False)
        r = ProfitDrawdownBarSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())
        self.assertEqual(r.type, SignalType.NONE)


class SectorStrongStopExitSubSignalTest(unittest.TestCase):
    """行业极强仓止盈挂单子信号：持有该仓位 → SELL(stop_exit, 入场价×止盈系数)。"""

    def test_holding_position_returns_stop_exit(self) -> None:
        s = _strategy(pos=1500, base_size=1000, sector_strong_entry_price=10.0)
        r = SectorStrongStopExitSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.SELL)
        self.assertEqual(r.executor, EXECUTOR_STOP_EXIT)
        self.assertEqual(r.price, 10.2)  # 10.0 × 1.02
        self.assertEqual(r.target_pos, 1000)
        self.assertEqual(r.reason, "行业极强仓止盈")

    def test_take_profit_points_applied(self) -> None:
        s = _strategy(
            pos=1500, base_size=1000, sector_strong_entry_price=20.0,
            sector_strong_take_profit_points=3.0,
        )
        r = SectorStrongStopExitSubSignal(VT_SYMBOL, s).on_bar(_bar(20.0), SignalResult())

        self.assertEqual(r.price, 20.6)

    def test_no_entry_price_none(self) -> None:
        """无行业极强入场价（如 MACD 摩擦仓）→ 不挂停止单。"""
        s = _strategy(pos=1500, base_size=1000, sector_strong_entry_price=0.0)
        r = SectorStrongStopExitSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.NONE)

    def test_at_base_position_none(self) -> None:
        """仓位已回到摩擦位 → 不挂停止单（避免卖出底仓）。"""
        s = _strategy(pos=1000, base_size=1000, sector_strong_entry_price=10.0)
        r = SectorStrongStopExitSubSignal(VT_SYMBOL, s).on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.type, SignalType.NONE)

    def test_on_tick_is_passthrough(self) -> None:
        s = _strategy()
        prev = SignalResult(type=SignalType.BUY, target_pos=1, reason="x")
        r = SectorStrongStopExitSubSignal(VT_SYMBOL, s).on_tick(MagicMock(), prev)
        self.assertIs(r, prev)

    def test_chain_short_circuits_before_stop_exit(self) -> None:
        """链尾位置：前面的规则（跌破10日线清仓）命中时短路，不挂停止单。"""
        s = _strategy(
            pos=1500, base_size=1000, sector_strong_entry_price=10.0,
            ma10=11.0,
        )
        priority = BottomFrictionBarPriority(VT_SYMBOL, s)
        r = priority.on_bar(_bar(10.5), SignalResult())

        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(r.executor, "target")

    def test_chain_reaches_stop_exit_when_no_other_rule_fires(self) -> None:
        """链上无其它动作时，最后一步产出 stop_exit（挂止盈停止单）。"""
        s = _strategy(
            pos=1500, base_size=1000, sector_strong_entry_price=10.0,
            ma10=0.0,
        )
        priority = BottomFrictionBarPriority(VT_SYMBOL, s)
        r = priority.on_bar(_bar(10.0), SignalResult())

        self.assertEqual(r.executor, EXECUTOR_STOP_EXIT)
        self.assertEqual(r.price, 10.2)


# ============================================================
# BottomFrictionBarSignal._execute 执行器分派
# ============================================================


class BottomFrictionBarSignalDispatchTest(unittest.TestCase):
    """bar 总信号 _execute：按 executor 分派策略执行器（策略 on_bar 不再判 executor）。"""

    def _make_signal(self) -> tuple[BottomFrictionBarSignal, list]:
        s = _strategy(
            vt_symbol=VT_SYMBOL,
            macd_fast=12,
            macd_slow=26,
            macd_signal=9,
            market_decline_reduce_threshold=3500,
            market_decline_exit_threshold=4000,
            sector_decline_ratio_threshold=0.33,
            sector_score_entry_threshold=80.0,
        )
        calls: list = []
        s._submit_profit_drawdown_exit = lambda price: calls.append(
            (EXECUTOR_DRAWDOWN, price)
        )
        s._submit_sector_strong_stop_exit = lambda price: calls.append(
            (EXECUTOR_STOP_EXIT, price)
        )
        s._set_target_position = lambda bar, pos, mark="": calls.append(
            ("target", bar, pos, mark)
        )
        return BottomFrictionBarSignal(s), calls

    def test_drawdown_dispatches_with_signal_price(self) -> None:
        signal, calls = self._make_signal()
        bar = _bar(8.0)

        signal._execute(
            SignalResult(
                type=SignalType.CLEAR,
                target_pos=0,
                reason="收益回撤清仓",
                executor=EXECUTOR_DRAWDOWN,
                price=8.0,
            ),
            bar,
        )

        self.assertEqual(calls, [(EXECUTOR_DRAWDOWN, 8.0)])

    def test_target_dispatches_to_set_target_position(self) -> None:
        signal, calls = self._make_signal()
        bar = _bar(10.0)

        signal._execute(
            SignalResult(type=SignalType.BUY, target_pos=1000, reason="仓位低于底仓，补仓"),
            bar,
        )

        self.assertEqual(calls, [("target", bar, 1000, "仓位低于底仓，补仓")])

    def test_stop_exit_dispatches_to_stop_order_submit(self) -> None:
        signal, calls = self._make_signal()
        bar = _bar(10.0)

        signal._execute(
            SignalResult(
                type=SignalType.SELL,
                target_pos=1000,
                reason="行业极强仓止盈",
                executor=EXECUTOR_STOP_EXIT,
                price=10.2,
            ),
            bar,
        )

        self.assertEqual(calls, [(EXECUTOR_STOP_EXIT, 10.2)])

    def test_none_dispatches_nothing(self) -> None:
        signal, calls = self._make_signal()

        signal._execute(SignalResult(), _bar(10.0))

        self.assertEqual(calls, [])


# ============================================================
# BottomFrictionBarSignal.on_bar prepare + 收益回撤早检查 + 执行（真实策略实例）
# ============================================================


class BottomFrictionBarSignalPrepareTest(unittest.TestCase):
    """bar_signal.on_bar 内部 prepare + 收益回撤早检查 + 执行器分派（真实 BottomFrictionStrategy）。"""

    orders: list

    def _make_strategy(self):
        from vnpy_ctastrategy.strategies.bottom_friction_strategy import (
            BottomFrictionStrategy,
        )

        orders: list = []
        self.orders = orders

        class FakeEngine:
            def send_order(self, *a):
                orders.append(a)
                return ["ORDER1"]
            def sync_strategy_data(self, *a): pass
            def write_log(self, *a): pass
            def put_strategy_event(self, *a): pass
            def send_wecom(self, *a): pass

        s = BottomFrictionStrategy(FakeEngine(), "prep", "000001.SZSE", {})
        s.bg = SimpleNamespace(update_tick=lambda t: None)
        from vnpy_ctastrategy import ArrayManager
        s.am = ArrayManager()
        s.inited = True
        s.trading = True
        return s

    def test_drawdown_short_circuits_before_prepare(self) -> None:
        """收益回撤触发时短路在 prepare 之前，且信号层直接分派 drawdown 执行器下单。"""
        s = self._make_strategy()
        s.pos = 1000
        s.pos_avg_price = 10.0
        s.profit_drawdown.peak_profit_points = 20.0
        s.yd_pos = 1000
        s.local_sell_frozen = 0
        s.shares_per_lot = 100
        s.t1 = True
        s.max_profit_drawdown_points = 10.0
        s.profit_protection_trigger_points = 10.0
        s.profit_protection_floor_points = 5.0
        s.cancel_all = lambda: None

        from datetime import datetime
        from vnpy.trader.constant import Exchange, Interval
        from vnpy.trader.object import BarData
        bar = BarData(
            gateway_name="t", symbol="000001", exchange=Exchange.SZSE,
            datetime=datetime(2026, 9, 26, 10, 0), interval=Interval.MINUTE,
            open_price=8.0, high_price=8.0, low_price=8.0,
            close_price=8.0, volume=1, turnover=8.0, open_interest=0,
        )
        s.bar_signal.on_bar(bar)
        r = s.bar_signal.signal_result(s.vt_symbol)

        self.assertEqual(r.type, SignalType.CLEAR)
        self.assertEqual(r.executor, EXECUTOR_DRAWDOWN)
        # prepare 未执行（_intraday_ready 仍 False）
        self.assertFalse(s._intraday_ready)
        # 信号层已按 executor 调执行器下单：清仓卖单，价格取信号价（bar 收盘价）
        self.assertEqual(len(self.orders), 1)
        self.assertIn("收益回撤清仓", self.orders[0])
        self.assertIn(8.0, self.orders[0])

    def test_sentiment_signal_takes_sector_threshold_from_strategy(self) -> None:
        """行业情绪阈值由策略参数透传给情绪信号（可在 UI/ setting 里调）。"""
        s = self._make_strategy()

        self.assertEqual(s.sector_score_exit_threshold, 40.0)
        self.assertEqual(
            s.bar_signal.sentiment_signal.sector_score_exit_threshold,
            s.sector_score_exit_threshold,
        )

    def test_not_ready_skips_prepare_and_decide_none(self) -> None:
        """am.inited=False（预热）：收益回撤不触发 → prepare 跳过 → 决策 NONE。"""
        s = self._make_strategy()
        s.pos = 0  # 无持仓，收益回撤不触发
        s.cancel_all = lambda: None

        from datetime import datetime
        from vnpy.trader.constant import Exchange, Interval
        from vnpy.trader.object import BarData
        bar = BarData(
            gateway_name="t", symbol="000001", exchange=Exchange.SZSE,
            datetime=datetime(2026, 9, 26, 9, 25),  # 09:30 前
            interval=Interval.MINUTE, open_price=10.0, high_price=10.0,
            low_price=10.0, close_price=10.0, volume=1, turnover=10.0, open_interest=0,
        )
        s.bar_signal.on_bar(bar)
        r = s.bar_signal.signal_result(s.vt_symbol)

        self.assertEqual(r.type, SignalType.NONE)
        self.assertFalse(s._intraday_ready)


if __name__ == "__main__":
    unittest.main()
