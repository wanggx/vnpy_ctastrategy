"""BottomFriction 的 bar 信号：bar_signal 持有 friction/sentiment，内部 prepare + 决策 + 执行。

``BottomFrictionBarSignal`` 是 bar 总信号，持有 ``friction_signal`` 与 ``sentiment_signal``，
``on_bar`` 内部完成三件事：

1. **prepare（指标维护，门控）**：``am.update_bar`` → 收益回撤优先判定（总跑，prepare 之前，
   与原 ``on_bar`` 早检查一致）→ ``ready = am.inited and _prepare_intraday_macd`` → 若 ready
   则 ``cancel_all`` / 日线 MA / 摩擦 EMA(每根 bar 必算) / UI 同步。策略不再内联这些。
2. **decide（优先级短路）**：收益回撤触发即返回 CLEAR(executor=drawdown)；否则 6 个决策子信号
   按 intraday-ready 门控、优先级短路（止损/破10日线/情绪/均线/补仓/摩擦），产出
   BUY/SELL/CLEAR(executor=target)。
3. **execute（执行器分派）**：链终态交给 ``_execute``，按 ``executor`` 直接调策略执行器
   （drawdown → ``_submit_profit_drawdown_exit(result.price=bar.close_price)``，其余 →
   ``_set_target_position(bar, target_pos, reason)``），策略 ``on_bar`` 不再判 executor。

6 个决策子信号经 ``BottomFrictionBarPriority``（优先级组合，``on_bar`` 在 not ready 时直接
返回 NONE）短路；收益回撤由 ``ProfitDrawdownBarSubSignal`` 单独在 prepare 之前总跑。
``SentimentRiskSubSignal`` 经 ``self.strategy.bar_signal.sentiment_signal`` 访问情绪信号
（由 bar_signal 持有，不再是策略顶层属性）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vnpy.trader.object import BarData, TickData

from .base import (
    EXECUTOR_DRAWDOWN,
    EXECUTOR_STOP_EXIT,
    PriorityCompositeSubSignal,
    SignalAggregator,
    SignalResult,
    SignalType,
    SubSignal,
)
from .friction import FrictionResult, FrictionSignal
from .market_sentiment import MarketSentimentSignal

if TYPE_CHECKING:
    from ..bottom_friction_strategy import BottomFrictionStrategy


class ProfitDrawdownBarSubSignal(SubSignal):
    """收益回撤 bar 子信号：经 profit_drawdown.check 判定，触发 → 清仓(drawdown, 带收盘价)。

    ``on_bar`` 调 ``strategy.profit_drawdown.check(bar.close_price, pos, pos_avg_price)``
    （状态机由策略持有，bar/tick 共用），触发 → CLEAR(0, "收益回撤清仓", executor=drawdown,
    price=bar.close_price)，否则 NONE。在 bar_signal 中于 prepare 之前总跑（不受
    am.inited/intraday 门控），与原 ``on_bar`` 早检查一致。不下单（总信号 ``_execute`` 调
    ``_submit_profit_drawdown_exit``）。
    """

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """收益回撤触发 → CLEAR(drawdown, 带收盘价)，否则 NONE。"""
        s = self.strategy
        if s.profit_drawdown.check(bar.close_price, s.pos, s.pos_avg_price):
            return SignalResult(
                type=SignalType.CLEAR,
                target_pos=0,
                reason="收益回撤清仓",
                executor=EXECUTOR_DRAWDOWN,
                price=bar.close_price,
            )
        return SignalResult()

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """tick 维度不判定（tick 链有 ProfitDrawdownSubSignal），原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """bar 信号不缓存 tick 结果，返回空结果。"""
        return SignalResult()


class StopLossSubSignal(SubSignal):
    """止损子信号：持仓亏损超过 stop_loss_points → 清仓(target)。"""

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """持仓亏损超过止损点数 → CLEAR(0, "止损")，否则 NONE。"""
        s = self.strategy
        if s.pos > 0 and s.pos_avg_price > 0:
            current_loss: float = round(
                (s.pos_avg_price - bar.close_price) / s.pos_avg_price * 100,
                2,
            )
            if current_loss > s.stop_loss_points:
                return SignalResult(
                    type=SignalType.CLEAR,
                    target_pos=0,
                    reason="止损",
                )
        return SignalResult()

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """tick 维度不判定，原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """bar 信号不缓存 tick 结果，返回空结果。"""
        return SignalResult()


class BreakMa10SubSignal(SubSignal):
    """跌破 10 日线子信号：收盘低于 ma10 → 清仓(target)。"""

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """收盘跌破 10 日均线 → CLEAR(0, "跌破10日线")，否则 NONE。"""
        if bar.close_price < self.strategy.ma10:
            return SignalResult(
                type=SignalType.CLEAR,
                target_pos=0,
                reason="跌破10日线",
            )
        return SignalResult()

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """tick 维度不判定，原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """bar 信号不缓存 tick 结果，返回空结果。"""
        return SignalResult()


class SentimentRiskSubSignal(SubSignal):
    """大盘/行业情绪子信号：包装 bar_signal.sentiment_signal，同步 UI 变量，按目标仓产出信号。

    ``on_bar`` 调 ``strategy.bar_signal.sentiment_signal.on_bar(bar)``（由 bar_signal 持有），
    同步 2 个可观测字段（情绪分 / 下跌家数）到策略 UI 变量；``should_block`` 时按
    ``target_pos`` 与当前持仓的关系产出信号（0 → CLEAR；高于持仓 → BUY；低于持仓 → SELL，
    executor=target），覆盖大盘减半/清仓、行业偏弱减摩擦仓、行业极强买入摩擦仓三种动作；
    否则 NONE。仅在链到达时执行（被短路则不同步，保持原"情绪 UI 变量在级联未到达时保持旧值"
    的行为）。板块数/弱板块比/行业情绪分是信号内部状态（``MarketSentimentSignal`` 持有），
    不作为策略 UI 变量暴露。
    """

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """取情绪结果，同步 UI 变量；should_block 时返回 CLEAR/BUY/SELL，否则 NONE。"""
        s = self.strategy
        result = s.bar_signal.sentiment_signal.on_bar(bar)

        s.market_sentiment_score = result.score
        s.market_declining_count = result.declining_count

        if not result.should_block:
            return SignalResult()

        if result.target_pos == 0:
            return SignalResult(
                type=SignalType.CLEAR,
                target_pos=0,
                reason=result.mark,
            )
        if result.target_pos > s.pos:
            return SignalResult(
                type=SignalType.BUY,
                target_pos=result.target_pos,
                reason=result.mark,
            )
        return SignalResult(
            type=SignalType.SELL,
            target_pos=result.target_pos,
            reason=result.mark,
        )

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """tick 维度不判定，原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """bar 信号不缓存 tick 结果，返回空结果。"""
        return SignalResult()


class MaAlignmentSubSignal(SubSignal):
    """均线多头排列子信号：快线 > 慢线 → 回到底仓(target)。"""

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """快线 > 慢线 → BUY(base_size, "均线多头排列，回到底仓")，否则 NONE。"""
        if self.strategy.fast_ma > self.strategy.slow_ma:
            return SignalResult(
                type=SignalType.BUY,
                target_pos=self.strategy.base_size,
                reason="均线多头排列，回到底仓",
            )
        return SignalResult()

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """tick 维度不判定，原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """bar 信号不缓存 tick 结果，返回空结果。"""
        return SignalResult()


class BelowBaseSubSignal(SubSignal):
    """低于底仓子信号：持仓 < 底仓 → 补仓回底仓(target)。"""

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """持仓低于底仓 → BUY(base_size, "仓位低于底仓，补仓")，否则 NONE。"""
        if self.strategy.pos < self.strategy.base_size:
            return SignalResult(
                type=SignalType.BUY,
                target_pos=self.strategy.base_size,
                reason="仓位低于底仓，补仓",
            )
        return SignalResult()

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """tick 维度不判定，原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """bar 信号不缓存 tick 结果，返回空结果。"""
        return SignalResult()


class FrictionSubSignal(SubSignal):
    """摩擦仓子信号：读预算好的 _friction_result，MACD 交叉时按目标仓位买/减(target)。

    ``on_bar`` 读协调器在 prepare 预算的 ``strategy._friction_result``（每根 bar 必算以维持
    EMA 状态）；``mark == ""``（无交叉）→ NONE；有交叉时：``entered`` → 副作用记
    ``friction_entry_price = bar.close_price``，再按 ``target_pos`` vs ``pos`` 返回 BUY/SELL。
    """

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """读 _friction_result：无交叉 NONE；上穿记入场价+BUY；下穿 SELL。"""
        s = self.strategy
        fr = s._friction_result
        if fr is None or fr.mark == "":
            return SignalResult()

        if fr.entered:
            s.friction_entry_price = bar.close_price

        if fr.target_pos > s.pos:
            return SignalResult(
                type=SignalType.BUY,
                target_pos=fr.target_pos,
                reason=fr.mark,
            )
        if fr.target_pos < s.pos:
            return SignalResult(
                type=SignalType.SELL,
                target_pos=fr.target_pos,
                reason=fr.mark,
            )
        # target_pos == pos（如已在最大仓时上穿）：返回 BUY，_set_target_position 判 no-op。
        return SignalResult(
            type=SignalType.BUY,
            target_pos=fr.target_pos,
            reason=fr.mark,
        )

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """tick 维度不判定，原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """bar 信号不缓存 tick 结果，返回空结果。"""
        return SignalResult()


class SectorStrongStopExitSubSignal(SubSignal):
    """行业极强仓止盈：持有该仓位时，按"入场价 × (1+止盈点数)"挂卖出停止单。

    ``on_bar`` 读策略的 ``sector_strong_entry_price``（行业极强买入的成交均价，由策略在
    ``on_trade`` 里记录）与持仓：持有摩擦位以上仓位（``pos > base_size``）且入场价已知 →
    返回 SELL(executor=stop_exit, price=入场价×(1+``sector_strong_take_profit_points``/100))，
    由策略以该价挂卖出停止单（engine 语义：``last_price <= 触发价`` 时触发卖出）；否则 NONE。

    放在决策链最末位：前面的信号命中（卖出/清仓）即短路，不会与本根 bar 的卖出动作抢
    T+1 可卖量；正常持有（链上无动作）时才重挂——每根 bar 的 ``cancel_all`` 会撤掉上一根的
    挂单，所以这里是"每根 bar 刷新挂单"。
    """

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """持有行业极强仓 → SELL(stop_exit, 止盈触发价)，否则 NONE。"""
        s = self.strategy
        if s.sector_strong_entry_price <= 0:
            return SignalResult()
        if s.pos <= s.base_size:
            return SignalResult()

        target_price: float = round(
            s.sector_strong_entry_price
            * (1 + s.sector_strong_take_profit_points / 100),
            2,
        )
        return SignalResult(
            type=SignalType.SELL,
            target_pos=int(s.base_size),
            reason="行业极强仓止盈",
            executor=EXECUTOR_STOP_EXIT,
            price=target_price,
        )

    def on_tick(self, tick: TickData, prev: SignalResult) -> SignalResult:
        """tick 维度不判定，原样返回 prev。"""
        return prev

    def signal_result(self) -> SignalResult:
        """bar 信号不缓存 tick 结果，返回空结果。"""
        return SignalResult()


class BottomFrictionBarPriority(PriorityCompositeSubSignal):
    """bar 决策优先级组合：止损 → 破10日线 → 情绪 → 均线 → 补仓 → 摩擦 → 行业极强仓止盈挂单。

    ``on_bar`` 在 ``strategy._intraday_ready`` 为 False（预热/盘前）时直接返回 NONE
    （决策均门控），与原 ``on_bar`` 的 am.inited + 09:30 早返回一致。
    """

    sub_factories = [
        StopLossSubSignal,
        BreakMa10SubSignal,
        SentimentRiskSubSignal,
        MaAlignmentSubSignal,
        BelowBaseSubSignal,
        FrictionSubSignal,
        SectorStrongStopExitSubSignal,
    ]

    def on_bar(self, bar: BarData, prev: SignalResult) -> SignalResult:
        """not ready 时直接 NONE；ready 时按优先级短路。"""
        if not self.strategy._intraday_ready:
            self._result = SignalResult()
            return self._result
        return super().on_bar(bar, prev)


class BottomFrictionBarSignal(SignalAggregator):
    """bar 总信号：持有 friction/sentiment，on_bar 内部 prepare + 决策 + 执行器分派。

    - 持有 ``friction_signal``（每根 bar 必算 EMA）与 ``sentiment_signal``（由
      ``SentimentRiskSubSignal`` 经 ``strategy.bar_signal.sentiment_signal`` 访问）。
    - ``on_bar``：``am.update_bar`` → 收益回撤优先(总跑) → prepare(门控：09:30 + 指标维护) →
      6 决策优先级链(intraday 门控)。收益回撤触发即短路返回 CLEAR(drawdown)。
    - 链终态交给 ``_execute`` 按 ``executor`` 调策略执行器，策略 ``on_bar`` 不再判 executor。
    - ``reset`` / ``check_health`` 供策略 on_init / on_start 委托。
    """

    sub_factories = [BottomFrictionBarPriority]

    def __init__(self, strategy: BottomFrictionStrategy) -> None:
        """构造：持有 friction/sentiment，创建收益回撤子信号。"""
        super().__init__(strategy)
        self.friction_signal: FrictionSignal = FrictionSignal(
            strategy=strategy,
            macd_fast=strategy.macd_fast,
            macd_slow=strategy.macd_slow,
            macd_signal=strategy.macd_signal,
        )
        self.sentiment_signal: MarketSentimentSignal = MarketSentimentSignal(
            strategy=strategy,
            decline_reduce_threshold=strategy.market_decline_reduce_threshold,
            decline_exit_threshold=strategy.market_decline_exit_threshold,
            sector_decline_ratio_threshold=strategy.sector_decline_ratio_threshold,
            sector_score_exit_threshold=strategy.sector_score_exit_threshold,
            sector_score_entry_threshold=strategy.sector_score_entry_threshold,
        )
        self._drawdown_sub: ProfitDrawdownBarSubSignal = ProfitDrawdownBarSubSignal(
            strategy.vt_symbol, strategy
        )

    def reset(self) -> None:
        """重置 friction/sentiment（策略 on_init 调用）。"""
        self.friction_signal.reset()
        self.sentiment_signal.reset()

    def check_health(self) -> None:
        """情绪引擎健康检查（策略 on_start 调用）。"""
        self.sentiment_signal.check_health()

    def on_bar(self, bar: BarData) -> bool:
        """prepare + 决策 + 执行：收益回撤优先 → 门控指标维护 → 6 决策链 → 分派执行器。"""
        s = self.strategy
        s.am.update_bar(bar)

        # 1. 收益回撤优先(总跑，prepare 之前)，与原 on_bar 早检查一致
        result: SignalResult = self._drawdown_sub.on_bar(bar, SignalResult())
        if result.type != SignalType.NONE:
            self._results[bar.vt_symbol] = result
            self._execute(result, bar)
            return True

        # 2. prepare(门控)：am.inited + 09:30；ready 才做指标维护
        ready: bool = s.am.inited and s._prepare_intraday_macd(bar)
        if ready:
            s.cancel_all()
            s._update_daily_close_series(bar)
            s.fast_ma = s._calc_daily_ma(s.fast_window)
            s.slow_ma = s._calc_daily_ma(s.slow_window)
            s.ma10 = s._calc_daily_ma(s.ma10_window)
            s._friction_result = self.friction_signal.on_bar(bar)
            s.macd_line = s._friction_result.macd_line
            s.signal_line = s._friction_result.signal_line
            s.macd_hist = s._friction_result.macd_hist
            s.last_macd_hist = s._friction_result.last_macd_hist
            s.fast_slow_ma = f"{s.fast_ma}/{s.slow_ma}"
            s.macd_triple = f"{s.macd_line}/{s.signal_line}/{s.macd_hist}"
        s._intraday_ready = ready

        # 3. 6 决策优先级链（intraday 门控：not ready 时 BottomFrictionBarPriority 返回 NONE）
        subs = self._get_or_create(bar.vt_symbol)
        result = SignalResult()
        for sub in subs:
            result = sub.on_bar(bar, result)
        self._results[bar.vt_symbol] = result

        # 4. 执行器分派（NONE 时 _execute 直接返回 False）
        self._execute(result, bar)
        return True

    def _execute(self, result: SignalResult, market: BarData) -> bool:
        """按 ``result.executor`` 调策略执行器；返回是否已分派。

        - ``EXECUTOR_DRAWDOWN``：``_submit_profit_drawdown_exit(result.price)``
          （价格由 ``ProfitDrawdownBarSubSignal`` 取 bar 收盘价）。
        - ``EXECUTOR_STOP_EXIT``：``_submit_sector_strong_stop_exit(result.price)``
          （价格由 ``SectorStrongStopExitSubSignal`` 取"入场价×止盈系数"）。
        - 其余（``EXECUTOR_TARGET``）：``_set_target_position(bar, target_pos, reason)``，
          执行器内部按 target vs pos 算买卖方向。
        """
        if result.type == SignalType.NONE:
            return False

        s = self.strategy
        if result.executor == EXECUTOR_DRAWDOWN:
            s._submit_profit_drawdown_exit(result.price)
            return True
        if result.executor == EXECUTOR_STOP_EXIT:
            s._submit_sector_strong_stop_exit(result.price)
            return True

        s._set_target_position(market, result.target_pos, result.reason)
        return True
