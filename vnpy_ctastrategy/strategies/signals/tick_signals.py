"""BottomFriction 的 tick 信号链：2 个实时保护子信号按优先级短路 + 命中后分派执行器。

tick 总信号 ``BottomFrictionTickSignal`` 内部聚合一个优先级组合子信号
``BottomFrictionTickPriority``，其按序短路运行 2 个 tick 子信号：

1. ``ProfitDrawdownSubSignal``（优先）：经 ``strategy.profit_drawdown.check`` 判定，最高收益
   回撤达阈值或跌破保底收益 → CLEAR(0, "收益回撤清仓", executor=drawdown)。
2. ``FrictionTakeProfitSubSignal``（其次）：摩擦仓到价止盈触发条件命中 →
   SELL(base_size, "摩擦仓止盈", executor=friction_tp)。

子信号只判触发 + 出价（``SignalResult.price``：买一价优先，买一无效时回落最新价），不下单；
链终态由总信号 ``_execute`` 按 ``executor`` 直接分派到策略执行器
(``_submit_profit_drawdown_exit`` / ``_submit_friction_take_profit``，执行器内部含 pending
守卫、T+1 可卖量、整手取整等)，策略 ``on_tick`` 不再判 executor。每个子信号 ``on_bar`` 为
pass-through（tick 信号不在 bar 维度判定）。收益回撤状态机 ``ProfitDrawdownState`` 由策略持有，
bar/tick 共用同一实例。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vnpy.trader.object import BarData, TickData

from .base import (
    EXECUTOR_DRAWDOWN,
    EXECUTOR_FRICTION_TP,
    PriorityCompositeSubSignal,
    SignalAggregator,
    SignalResult,
    SignalType,
    SubSignal,
)

if TYPE_CHECKING:
    from ..bottom_friction_strategy import BottomFrictionStrategy


def _exit_price(tick: TickData) -> float:
    """退出执行价：买一价优先，买一价无效（<=0）时回落最新价。"""
    return tick.bid_price_1 if tick.bid_price_1 > 0 else tick.last_price


class ProfitDrawdownSubSignal(SubSignal):
    """收益回撤子信号：经 profit_drawdown.check 判定，触发 → 清仓(drawdown, 带退出价)。

    ``on_tick`` 调 ``strategy.profit_drawdown.check(tick.last_price, pos, pos_avg_price)``
    （状态机由策略持有，bar/tick 共用；每 tick 更新 peak/active/protection_active 并回写
    策略 UI 变量），触发 → CLEAR(0, "收益回撤清仓", executor=drawdown, price=退出价)，否则 NONE。
    不下单（总信号 ``_execute`` 调 ``_submit_profit_drawdown_exit``）。
    """

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """收益回撤触发 → CLEAR(drawdown, 带退出价)，否则 NONE。"""
        s = self.strategy
        if s.profit_drawdown.check(tick.last_price, s.pos, s.pos_avg_price):
            return SignalResult(
                type=SignalType.CLEAR,
                target_pos=0,
                reason="收益回撤清仓",
                executor=EXECUTOR_DRAWDOWN,
                price=_exit_price(tick),
            )
        return SignalResult()

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """bar 维度不判定（bar 链有 ProfitDrawdownBarSubSignal），原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """tick 信号不缓存 bar 结果，返回空结果。"""
        return SignalResult()


class FrictionTakeProfitSubSignal(SubSignal):
    """摩擦仓到价止盈子信号：触发条件命中 → SELL(base_size, "摩擦仓止盈", 带卖出价)。

    ``on_tick`` 复刻原 ``_check_friction_take_profit`` 的触发条件：
    ``friction_entry_price > 0`` 且未 pending 且 ``pos > base_size`` 且现价 >=
    ``friction_entry_price * (1 + friction_take_profit_points/100)`` → SELL(base_size,
    "摩擦仓止盈", price=退出价)，否则 NONE。不下单（总信号 ``_execute`` 调
    ``_submit_friction_take_profit``）。
    """

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """摩擦仓到价止盈触发 → SELL(base_size, 带卖出价)，否则 NONE。"""
        s = self.strategy
        if (
            s.friction_entry_price <= 0
            or s.friction_tp_pending
            or s.pos <= s.base_size
        ):
            return SignalResult()

        target_price: float = round(
            s.friction_entry_price * (1 + s.friction_take_profit_points / 100),
            2,
        )
        if tick.last_price < target_price:
            return SignalResult()

        return SignalResult(
            type=SignalType.SELL,
            target_pos=s.base_size,
            reason="摩擦仓止盈",
            executor=EXECUTOR_FRICTION_TP,
            price=_exit_price(tick),
        )

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """bar 维度不判定，原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """tick 信号不缓存 bar 结果，返回空结果。"""
        return SignalResult()


class BottomFrictionTickPriority(PriorityCompositeSubSignal):
    """tick 优先级组合：收益回撤 → 摩擦仓止盈（回撤优先，命中即短路）。"""

    sub_factories = [ProfitDrawdownSubSignal, FrictionTakeProfitSubSignal]


class BottomFrictionTickSignal(SignalAggregator):
    """tick 总信号：聚合 BottomFrictionTickPriority 优先级链，跑链 + 按 executor 分派执行器。

    ``on_tick`` 继承基类：跑优先级链（收益回撤 → 摩擦止盈）→ 缓存链终态 → ``_execute``。
    ``_execute`` 用 ``SignalResult.price``（子信号按 tick 算好的退出价）调策略执行器，
    策略 ``on_tick`` 只负责行情维护 + UI 事件刷新，不再判 executor。
    """

    sub_factories = [BottomFrictionTickPriority]

    def _execute(self, result: SignalResult, market: TickData) -> bool:
        """按 ``result.executor`` 调策略执行器（价格取 ``result.price``）；返回是否已分派。"""
        s = self.strategy
        if result.executor == EXECUTOR_DRAWDOWN:
            s._submit_profit_drawdown_exit(result.price)
            return True
        if result.executor == EXECUTOR_FRICTION_TP:
            s._submit_friction_take_profit(result.price)
            return True
        return False
