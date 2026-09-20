"""CTA 策略统一信号基类与结果类型（目标仓位制）。

定义两层信号架构的公共骨架，供 bar/tick 信号复用：

- ``SubSignal``：单标的子信号基类。每标的一组实例（由总信号按序聚合），状态隔离；
  ``on_tick(tick, prev) -> SignalResult`` / ``on_bar(bar, prev) -> SignalResult`` 接收
  链上前序子信号的累计结果 ``prev``，返回自己的产出（可基于 ``prev`` 决定保留/否定/改写）。
  返回值的 ``type`` 为 NONE 表示该子信号无产出或已否定。是否触发交易最终由总信号取链终态
  ``signal_result()`` 表达。
- ``PriorityCompositeSubSignal``：优先级组合子信号。按序短路运行一组子信号，首个非 NONE
  即返回。``on_tick`` 与 ``on_bar`` 均按此短路（CTA 的 bar 链也需短路）。
- ``SignalAggregator``：总信号基类。内部按 ``vt_symbol`` 维护一组有序子信号实例，
  ``on_tick`` / ``on_bar`` 按序运行并把累计结果逐个传递给下一个子信号（链式），``signal_result(vt)``
  返回链终态缓存（零计算）；``sub_factories`` 由子类提供有序工厂列表。子信号只判触发，
  命中后的下单由总信号 ``_execute(result, market)`` 按 ``result.executor`` 分派到策略执行器。

``SignalResult`` 采用目标仓位制：携带 ``target_pos + reason + price``，不含成交量；执行价由
信号层按当前行情算好（``price``），策略执行器只负责守卫/取整/下单，``type`` 仅作"是否产出"+日志。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

from vnpy.trader.object import BarData, TickData

if TYPE_CHECKING:
    # 仅供类型标注用（from __future__ import annotations 使标注不求值），
    # 运行时不需要。
    from ..bottom_friction_strategy import BottomFrictionStrategy


class SignalType(Enum):
    """信号操作类型。"""

    NONE = "无"        # 无操作
    BUY = "买入"       # 买入（含补仓/上穿/回到底仓）
    SELL = "卖出"      # 部分卖出（减摩擦仓/减半/摩擦止盈）
    CLEAR = "清仓"     # 全清


# executor 分派键：总信号 ``_execute`` 据此选择策略执行器（集中定义，避免字符串散落）
EXECUTOR_TARGET: str = "target"            # 策略 _set_target_position(bar, target_pos, reason)
EXECUTOR_DRAWDOWN: str = "drawdown"        # 策略 _submit_profit_drawdown_exit(price)
EXECUTOR_FRICTION_TP: str = "friction_tp"  # 策略 _submit_friction_take_profit(price)
EXECUTOR_STOP_EXIT: str = "stop_exit"      # 策略 _submit_sector_strong_stop_exit(price)


@dataclass(frozen=True, slots=True)
class SignalResult:
    """单标的信号判定结果（目标仓位制，当前快照，可重复读）。

    ``type`` 为 NONE 表示无操作；``executor`` 是执行器分派键，由总信号
    （``SignalAggregator._execute``）分派到对应策略执行器：
    - ``EXECUTOR_TARGET``（默认）：``_set_target_position(bar, target_pos, reason)``
      （内部按 target vs pos 算买卖方向）。
    - ``EXECUTOR_DRAWDOWN``：``_submit_profit_drawdown_exit(price)``（撤单+T+1+整手取整+卖）。
    - ``EXECUTOR_FRICTION_TP``：``_submit_friction_take_profit(price)``（到价卖摩擦仓）。
    - ``EXECUTOR_STOP_EXIT``：``_submit_sector_strong_stop_exit(price)``（按 price 挂卖出停止单，
      卖出摩擦位以上的持仓；触发价由信号层算好）。

    ``price`` 是信号层按当前行情算好的执行价（``EXECUTOR_DRAWDOWN`` /
    ``EXECUTOR_FRICTION_TP`` / ``EXECUTOR_STOP_EXIT`` 用：买一价优先，买一无效时回落最新价 /
    bar 收盘价 / 入场价×止盈系数），策略执行器不再自行取价；``0.0`` 表示无建议价
    （如 ``EXECUTOR_TARGET``，由执行器按 market 取价）。
    ``reason`` 供日志/企微复用。链式聚合中，子信号返回的结果作为下一个子信号的 ``prev``。
    """

    type: SignalType = SignalType.NONE
    target_pos: int = 0
    reason: str = ""
    executor: str = EXECUTOR_TARGET
    price: float = 0.0


class SubSignal:
    """单标的子信号基类：持有该标的的隔离运行时状态。

    子类在 ``__init__`` 中初始化各自的状态，由总信号按需创建。``on_tick`` / ``on_bar``
    接收前序累计结果 ``prev``，返回自己的产出（可基于 ``prev`` 决定保留/否定/改写）；
    所有计算与副作用都在此完成，``signal_result`` 仅返回缓存结果。
    """

    def __init__(self, vt_symbol: str, strategy: BottomFrictionStrategy) -> None:
        """构造函数：绑定标的与所属策略。"""
        self.vt_symbol: str = vt_symbol
        self.strategy: BottomFrictionStrategy = strategy

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """处理 tick，更新内部状态；返回本子信号产出（可基于 prev）。"""
        raise NotImplementedError

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """处理 bar，更新内部状态；返回本子信号产出（可基于 prev）。"""
        raise NotImplementedError

    def signal_result(self) -> SignalResult:
        """返回 ``on_tick`` / ``on_bar`` 已缓存的判定结果（零计算）。"""
        raise NotImplementedError


class PriorityCompositeSubSignal(SubSignal):
    """优先级组合子信号：按序短路运行一组子信号，首个非 NONE 即返回。

    ``on_tick`` / ``on_bar`` 均把 ``prev`` 传给每个子信号并按序运行；首个返回非 NONE 的
    子信号结果即作为本组合产出，后续子信号不跑（短路）。全部 NONE 则返回 NONE。对链来说
    是一个子信号节点。子信号工厂由类属性 ``sub_factories`` 提供。
    """

    # 子类覆盖：按优先级排列的子信号工厂列表
    sub_factories: list[Callable[[str, BottomFrictionStrategy], SubSignal]] = []

    def __init__(self, vt_symbol: str, strategy: BottomFrictionStrategy) -> None:
        """构造函数：按 sub_factories 创建子信号实例并初始化结果缓存。"""
        super().__init__(vt_symbol, strategy)
        self._subs: list[SubSignal] = [f(vt_symbol, strategy) for f in self.sub_factories]
        self._result: SignalResult = SignalResult()

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """按序短路运行子信号，取首个非 NONE 结果（同 prev 传入每个子信号）。"""
        for sub in self._subs:
            r: SignalResult = sub.on_tick(tick, prev)
            if r.type != SignalType.NONE:
                self._result = r
                return r
        self._result = SignalResult()
        return self._result

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """按序短路运行子信号，取首个非 NONE 结果（同 prev 传入每个子信号）。"""
        for sub in self._subs:
            r: SignalResult = sub.on_bar(bar, prev)
            if r.type != SignalType.NONE:
                self._result = r
                return r
        self._result = SignalResult()
        return self._result

    def signal_result(self) -> SignalResult:
        """返回 ``on_tick`` / ``on_bar`` 已缓存的判定结果（零计算）。"""
        return self._result


class SignalAggregator:
    """总信号基类：按 vt_symbol 维护一组有序子信号，链式分发行情，并按 executor 执行。

    子类通过类属性 ``sub_factories`` 指定有序子信号工厂列表
    （``Callable[[str, BottomFrictionStrategy], SubSignal]``）。``on_tick`` / ``on_bar`` 按序
    运行子信号，把累计结果逐个传递给下一个子信号（链式）；``signal_result(vt)`` 返回
    链终态缓存。子信号在首次见到 vt_symbol 时按需创建，退订时由 ``remove`` 清理。

    链跑完后，总信号调 ``_execute(result, market)``：由基类默认不分派（返回 False），
    BottomFriction 的总信号覆写它，按 ``result.executor`` 调策略执行器（下单/清仓），
    因此策略只需"送行情 + 刷新 UI"，不再判 executor。
    """

    # 子类覆盖：有序子信号工厂列表，按序判定（前序结果传入后序）
    sub_factories: list[Callable[[str, BottomFrictionStrategy], SubSignal]] = []

    def __init__(self, strategy: BottomFrictionStrategy) -> None:
        """构造函数：持有策略引用并初始化子信号映射与结果缓存。"""
        self.strategy: BottomFrictionStrategy = strategy
        # vt_symbol -> 有序子信号实例列表
        self._subs: dict[str, list[SubSignal]] = {}
        # vt_symbol -> 链终态结果缓存（signal_result 零计算取此）
        self._results: dict[str, SignalResult] = {}

    def on_tick(self, tick: TickData) -> bool:
        """按序分发 tick 到该标的的子信号链，累计结果逐个传递，缓存链终态后按需执行。"""
        subs = self._get_or_create(tick.vt_symbol)
        result: SignalResult = SignalResult()
        for sub in subs:
            result = sub.on_tick(tick, result)
        self._results[tick.vt_symbol] = result
        self._execute(result, tick)
        return True

    def on_bar(self, bar: BarData) -> bool:
        """按序分发 bar 到该标的的子信号链，累计结果逐个传递，缓存链终态后按需执行。"""
        subs = self._get_or_create(bar.vt_symbol)
        result: SignalResult = SignalResult()
        for sub in subs:
            result = sub.on_bar(bar, result)
        self._results[bar.vt_symbol] = result
        self._execute(result, bar)
        return True

    def _execute(self, result: SignalResult, market: BarData | TickData) -> bool:
        """按 ``result.executor`` 分派到策略执行器；基类默认不分派（返回 False）。

        子类覆写：命中链终态后调对应策略执行器（``result.price`` 是信号层算好的执行价），
        返回是否已分派。``market`` 为本次 bar/tick，供 ``EXECUTOR_TARGET`` 按 bar 下单。
        """
        return False

    def signal_result(self, vt_symbol: str) -> SignalResult:
        """取该标的的链终态结果；无子信号时返回空结果（无操作）。"""
        return self._results.get(vt_symbol, SignalResult())

    def remove(self, vt_symbol: str) -> None:
        """退订时清理子信号及其持有的 per-symbol 运行时状态与结果缓存。"""
        self._subs.pop(vt_symbol, None)
        self._results.pop(vt_symbol, None)

    def _get_or_create(self, vt_symbol: str) -> list[SubSignal]:
        """取子信号列表，不存在则按 sub_factories 逐个创建并登记。"""
        subs = self._subs.get(vt_symbol)
        if subs is None:
            subs = [factory(vt_symbol, self.strategy) for factory in self.sub_factories]
            self._subs[vt_symbol] = subs
        return subs
