from collections import deque
from datetime import datetime
from math import isfinite

import numpy as np
import polars as pl

from vnpy.alpha.dataset.utility import calculate_by_expression

from vnpy_ctastrategy import (
    ArrayManager,
    BarData,
    BarGenerator,
    CtaTemplate,
    Direction,
    OrderData,
    StopOrder,
    TickData,
    TradeData,
)
from vnpy_ctastrategy.base import StopOrderStatus


class VolumeBreakoutStrategy(CtaTemplate):
    """
    趋势和 vnpy.alpha 成交量因子共同确认的突破策略。

    入场条件：
    1. 快速均线位于慢速均线的正确方向；
    2. 收盘价突破此前一段时间的最高价/最低价；
    3. Alpha158 VMA 成交量因子确认当前 K 线显著放量。

    持仓后使用 ATR 初始止损、ATR 移动止损和价格通道退出。
    价格通道排除当前 K 线，避免信号中混入未来数据。

    Alpha158 VMA 定义为：
        ts_mean(volume, window) / (volume + 1e-12)

    因子越小，表示当前成交量相对滚动均量越大。
    """

    author = "OpenAI"

    fast_window: int = 10
    slow_window: int = 30
    breakout_window: int = 20
    exit_window: int = 10
    volume_window: int = 20
    volume_threshold: float = 1.5
    atr_window: int = 14
    stop_atr: float = 2.0
    trailing_atr: float = 3.0
    fixed_size: int = 1
    price_add: float = 0.0
    allow_short: bool = True
    cancel_price_percent: float = 0.3
    entry_order_timeout: int = 0
    stop_update_atr: float = 0.25

    fast_ma: float = 0.0
    slow_ma: float = 0.0
    alpha_vma: float = 0.0
    atr_value: float = 0.0
    entry_up: float = 0.0
    entry_down: float = 0.0
    exit_up: float = 0.0
    exit_down: float = 0.0
    entry_price: float = 0.0
    intra_trade_high: float = 0.0
    intra_trade_low: float = 0.0
    pending_entry_count: int = 0
    active_stop_price: float = 0.0

    parameters = [
        "fast_window",
        "slow_window",
        "breakout_window",
        "exit_window",
        "volume_window",
        "volume_threshold",
        "atr_window",
        "stop_atr",
        "trailing_atr",
        "fixed_size",
        "price_add",
        "allow_short",
        "cancel_price_percent",
        "entry_order_timeout",
        "stop_update_atr",
    ]
    variables = [
        "fast_ma",
        "slow_ma",
        "alpha_vma",
        "atr_value",
        "entry_up",
        "entry_down",
        "exit_up",
        "exit_down",
        "entry_price",
        "intra_trade_high",
        "intra_trade_low",
        "pending_entry_count",
        "active_stop_price",
    ]

    def on_init(self) -> None:
        """初始化策略。"""
        self.write_log("成交量突破策略初始化")

        self.bg: BarGenerator = BarGenerator(self.on_bar)

        required_size: int = max(
            self.slow_window,
            self.breakout_window,
            self.exit_window,
            self.volume_window,
            self.atr_window,
        ) + 2
        self.am: ArrayManager = ArrayManager(size=max(100, required_size))
        self.factor_datetimes: deque[datetime] = deque(maxlen=required_size)
        self.factor_volumes: deque[float] = deque(maxlen=required_size)
        self.bar_count: int = 0
        self.active_entry_orders: dict[
            str,
            tuple[Direction, float, int],
        ] = {}
        self.active_stop_orders: dict[str, float] = {}
        self.cancel_requested_orderids: set[str] = set()
        self.pending_stop_order: tuple[
            Direction,
            float,
            float,
            str,
        ] | None = None

        self.load_bar(10)

    def on_start(self) -> None:
        """启动策略。"""
        self.write_log("成交量突破策略启动")
        self.put_event()

    def on_stop(self) -> None:
        """停止策略。"""
        self.write_log("成交量突破策略停止")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """用最新成交价管理排队委托，并合成分钟 K 线。"""
        if tick.last_price > 0:
            self._cancel_stale_entry_orders(tick.last_price)
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """处理一根已完成的 K 线。"""
        self.bar_count += 1
        self.factor_datetimes.append(bar.datetime)
        self.factor_volumes.append(float(bar.volume))
        self.am.update_bar(bar)
        if not self.am.inited:
            return

        if not self._parameters_valid():
            return

        close_array: np.ndarray = self.am.close
        high_array: np.ndarray = self.am.high
        low_array: np.ndarray = self.am.low

        self.fast_ma = float(np.mean(close_array[-self.fast_window:]))
        self.slow_ma = float(np.mean(close_array[-self.slow_window:]))
        self.atr_value = float(self.am.atr(self.atr_window))

        self.alpha_vma = self._calculate_alpha_vma()

        # 突破和退出通道不包含当前 K 线。
        self.entry_up = float(np.max(high_array[-self.breakout_window - 1:-1]))
        self.entry_down = float(np.min(low_array[-self.breakout_window - 1:-1]))
        self.exit_up = float(np.max(high_array[-self.exit_window - 1:-1]))
        self.exit_down = float(np.min(low_array[-self.exit_window - 1:-1]))

        volume_confirmed: bool = (
            bar.volume > 0
            and isfinite(self.alpha_vma)
            and self.alpha_vma > 0
            and self.alpha_vma <= 1 / self.volume_threshold
        )
        long_signal: bool = (
            self.fast_ma > self.slow_ma
            and bar.close_price > self.entry_up
            and volume_confirmed
        )
        short_signal: bool = (
            self.allow_short
            and self.fast_ma < self.slow_ma
            and bar.close_price < self.entry_down
            and volume_confirmed
        )

        self._cancel_stale_entry_orders(bar.close_price)

        if self.pos == 0:
            self._cancel_protective_orders()
            self.entry_price = 0.0
            self.intra_trade_high = bar.high_price
            self.intra_trade_low = bar.low_price

            if not self.active_entry_orders and long_signal:
                vt_orderids: list[str] = self.buy(
                    bar.close_price + self.price_add,
                    self.fixed_size,
                    mark="放量向上突破",
                )
                self._track_entry_orders(
                    vt_orderids,
                    Direction.LONG,
                    bar.close_price + self.price_add,
                )
            elif not self.active_entry_orders and short_signal:
                vt_orderids = self.short(
                    bar.close_price - self.price_add,
                    self.fixed_size,
                    mark="放量向下突破",
                )
                self._track_entry_orders(
                    vt_orderids,
                    Direction.SHORT,
                    bar.close_price - self.price_add,
                )

        elif self.pos > 0:
            self._cancel_entry_orders("已有多头持仓")
            self.intra_trade_high = max(
                self.intra_trade_high,
                bar.high_price,
            )
            self.intra_trade_low = bar.low_price

            initial_stop: float = self.entry_price - self.stop_atr * self.atr_value
            trailing_stop: float = (
                self.intra_trade_high - self.trailing_atr * self.atr_value
            )
            long_stop: float = max(
                initial_stop,
                trailing_stop,
                self.exit_down,
            )
            self._update_protective_stop(
                Direction.SHORT,
                long_stop,
                abs(self.pos),
                "多头保护止损",
            )

        else:
            self._cancel_entry_orders("已有空头持仓")
            self.intra_trade_low = min(
                self.intra_trade_low,
                bar.low_price,
            )
            self.intra_trade_high = bar.high_price

            initial_stop = self.entry_price + self.stop_atr * self.atr_value
            trailing_stop = (
                self.intra_trade_low + self.trailing_atr * self.atr_value
            )
            short_stop: float = min(
                initial_stop,
                trailing_stop,
                self.exit_up,
            )
            self._update_protective_stop(
                Direction.LONG,
                short_stop,
                abs(self.pos),
                "空头保护止损",
            )

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        """处理委托状态更新。"""
        if order.is_active():
            return

        self.cancel_requested_orderids.discard(order.vt_orderid)
        self.active_entry_orders.pop(order.vt_orderid, None)
        self.active_stop_orders.pop(order.vt_orderid, None)
        self._sync_order_variables()
        self._submit_pending_stop_order()

    def on_trade(self, trade: TradeData) -> None:
        """记录实际成交价，作为初始止损的计算基准。"""
        self._cancel_entry_orders("入场委托已有成交")

        if trade.direction == Direction.LONG:
            if self.pos > 0:
                self.entry_price = trade.price
                self.intra_trade_high = trade.price
                self.intra_trade_low = trade.price
            elif self.pos == 0:
                self.entry_price = 0.0
        else:
            if self.pos < 0:
                self.entry_price = trade.price
                self.intra_trade_high = trade.price
                self.intra_trade_low = trade.price
            elif self.pos == 0:
                self.entry_price = 0.0

        if self.pos == 0:
            self.pending_stop_order = None

        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """处理本地停止单状态更新。"""
        if stop_order.status == StopOrderStatus.WAITING:
            self.active_stop_orders[stop_order.stop_orderid] = stop_order.price
        else:
            self.cancel_requested_orderids.discard(stop_order.stop_orderid)
            self.active_stop_orders.pop(stop_order.stop_orderid, None)

        self._sync_order_variables()
        self._submit_pending_stop_order()

    def _track_entry_orders(
        self,
        vt_orderids: list[str],
        direction: Direction,
        price: float,
    ) -> None:
        """记录新发出的入场限价单。"""
        for vt_orderid in vt_orderids:
            self.active_entry_orders[vt_orderid] = (
                direction,
                price,
                self.bar_count,
            )
        self._sync_order_variables()

    def _cancel_stale_entry_orders(self, latest_price: float) -> None:
        """仅撤销价格明显偏离、趋势反转或超时的入场单。"""
        if not self.active_entry_orders:
            return

        deviation: float = self.cancel_price_percent / 100

        for vt_orderid, order_info in list(self.active_entry_orders.items()):
            if vt_orderid in self.cancel_requested_orderids:
                continue

            direction, order_price, created_bar = order_info
            age: int = self.bar_count - created_bar
            timed_out: bool = (
                self.entry_order_timeout > 0
                and age >= self.entry_order_timeout
            )

            if direction == Direction.LONG:
                price_moved_away: bool = (
                    latest_price > order_price * (1 + deviation)
                )
                trend_reversed: bool = self.fast_ma <= self.slow_ma
            else:
                price_moved_away = (
                    latest_price < order_price * (1 - deviation)
                )
                trend_reversed = self.fast_ma >= self.slow_ma

            if price_moved_away or trend_reversed or timed_out:
                reasons: list[str] = []
                if price_moved_away:
                    reasons.append("最新价偏离委托价")
                if trend_reversed:
                    reasons.append("均线趋势反转")
                if timed_out:
                    reasons.append("委托超时")

                self.write_log(
                    f"撤销入场单 {vt_orderid}：{','.join(reasons)}，"
                    f"委托价={order_price:.4f}，最新价={latest_price:.4f}"
                )
                self.cancel_requested_orderids.add(vt_orderid)
                self.cancel_order(vt_orderid)

    def _cancel_entry_orders(self, reason: str) -> None:
        """撤销所有尚未完成的入场单。"""
        for vt_orderid in list(self.active_entry_orders):
            if vt_orderid in self.cancel_requested_orderids:
                continue
            self.write_log(f"撤销入场单 {vt_orderid}：{reason}")
            self.cancel_requested_orderids.add(vt_orderid)
            self.cancel_order(vt_orderid)

    def _update_protective_stop(
        self,
        direction: Direction,
        price: float,
        volume: float,
        mark: str,
    ) -> None:
        """仅在止损价显著收紧时撤旧挂新。"""
        if not self.active_stop_orders:
            self._send_protective_stop(direction, price, volume, mark)
            return

        current_price: float = next(iter(self.active_stop_orders.values()))
        update_distance: float = self.atr_value * self.stop_update_atr

        if direction == Direction.SHORT:
            should_update: bool = price > current_price + update_distance
        else:
            should_update = price < current_price - update_distance

        if not should_update:
            return

        self.pending_stop_order = (direction, price, volume, mark)
        for vt_orderid in list(self.active_stop_orders):
            if vt_orderid in self.cancel_requested_orderids:
                continue
            self.cancel_requested_orderids.add(vt_orderid)
            self.cancel_order(vt_orderid)

    def _send_protective_stop(
        self,
        direction: Direction,
        price: float,
        volume: float,
        mark: str,
    ) -> None:
        """发送保护停止单并记录委托号。"""
        if direction == Direction.SHORT:
            vt_orderids: list[str] = self.sell(
                price,
                volume,
                stop=True,
                mark=mark,
            )
        else:
            vt_orderids = self.cover(
                price,
                volume,
                stop=True,
                mark=mark,
            )

        for vt_orderid in vt_orderids:
            self.active_stop_orders[vt_orderid] = price
        self._sync_order_variables()

    def _submit_pending_stop_order(self) -> None:
        """旧停止单撤单确认后，发送等待中的新停止单。"""
        if self.active_stop_orders or self.pending_stop_order is None:
            return

        direction, price, volume, mark = self.pending_stop_order
        self.pending_stop_order = None

        position_matches: bool = (
            direction == Direction.SHORT
            and self.pos > 0
            or direction == Direction.LONG
            and self.pos < 0
        )
        if position_matches:
            self._send_protective_stop(direction, price, volume, mark)

    def _cancel_protective_orders(self) -> None:
        """空仓时撤销残留的保护停止单。"""
        self.pending_stop_order = None
        for vt_orderid in list(self.active_stop_orders):
            if vt_orderid in self.cancel_requested_orderids:
                continue
            self.cancel_requested_orderids.add(vt_orderid)
            self.cancel_order(vt_orderid)

    def _sync_order_variables(self) -> None:
        """同步界面展示用的委托状态变量。"""
        self.pending_entry_count = len(self.active_entry_orders)
        if self.active_stop_orders:
            self.active_stop_price = next(
                iter(self.active_stop_orders.values())
            )
        else:
            self.active_stop_price = 0.0

    def _calculate_alpha_vma(self) -> float:
        """使用 vnpy.alpha 表达式引擎计算 Alpha158 VMA 因子。"""
        factor_df: pl.DataFrame = pl.DataFrame(
            {
                "datetime": list(self.factor_datetimes),
                "vt_symbol": [self.vt_symbol] * len(self.factor_datetimes),
                "volume": list(self.factor_volumes),
            }
        )
        expression: str = (
            f"ts_mean(volume, {self.volume_window}) / (volume + 1e-12)"
        )
        result_df: pl.DataFrame = calculate_by_expression(
            factor_df,
            expression,
        )
        factor_value: float | None = result_df["data"][-1]

        if factor_value is None:
            return float("nan")
        return float(factor_value)

    def _parameters_valid(self) -> bool:
        """检查会影响数组切片和下单的关键参数。"""
        valid: bool = (
            self.fast_window > 0
            and self.slow_window > 0
            and self.breakout_window > 0
            and self.exit_window > 0
            and self.volume_window > 0
            and self.volume_threshold > 1
            and self.atr_window > 0
            and self.stop_atr > 0
            and self.trailing_atr > 0
            and self.fixed_size > 0
            and self.price_add >= 0
            and self.cancel_price_percent >= 0
            and self.entry_order_timeout >= 0
            and self.stop_update_atr >= 0
        )
        if not valid:
            self.write_log("策略参数无效，请检查窗口、ATR、手数和超价设置")
        return valid
