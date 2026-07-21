import numpy as np

from vnpy_ctastrategy import (
    ArrayManager,
    BarData,
    BarGenerator,
    CtaTemplate,
    OrderData,
    StopOrder,
    TickData,
    TradeData,
)


class BoxTrendSwingStrategy(CtaTemplate):
    """在箱体或上升通道内低吸高抛的 A 股多头策略。"""

    author = "Codex"

    t1: bool = True

    channel_window: int = 30
    fast_window: int = 10
    slow_window: int = 30
    trend_lookback: int = 5
    box_max_width_pct: float = 10.0
    box_max_slope_pct: float = 0.5
    uptrend_min_slope_pct: float = 0.2
    buy_zone_ratio: float = 0.2
    sell_zone_ratio: float = 0.8
    stop_loss_pct: float = 2.0
    base_size: int = 100
    swing_size: int = 100
    lot_size: int = 100

    regime: int = 0
    channel_high: float = 0.0
    channel_low: float = 0.0
    buy_price: float = 0.0
    sell_price: float = 0.0
    fast_ma: float = 0.0
    slow_ma: float = 0.0
    slow_ma_slope_pct: float = 0.0
    channel_width_pct: float = 0.0

    parameters = [
        "channel_window",
        "fast_window",
        "slow_window",
        "trend_lookback",
        "box_max_width_pct",
        "box_max_slope_pct",
        "uptrend_min_slope_pct",
        "buy_zone_ratio",
        "sell_zone_ratio",
        "stop_loss_pct",
        "base_size",
        "swing_size",
        "lot_size",
    ]
    variables = [
        "regime",
        "channel_high",
        "channel_low",
        "buy_price",
        "sell_price",
        "fast_ma",
        "slow_ma",
        "slow_ma_slope_pct",
        "channel_width_pct",
    ]

    def on_init(self) -> None:
        """初始化分钟线生成器和指标缓存。"""
        self.bg: BarGenerator = BarGenerator(self.on_bar)
        required_size: int = max(
            self.channel_window + 1,
            self.slow_window + self.trend_lookback,
            self.fast_window,
        )
        self.am: ArrayManager = ArrayManager(size=required_size)
        self.load_bar(10)

    def on_start(self) -> None:
        """启动策略。"""
        self.put_event()

    def on_stop(self) -> None:
        """停止策略。"""
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """将 tick 合成为分钟线。"""
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """更新市场状态并执行低吸、高抛或止损。"""
        self.cancel_all()

        am: ArrayManager = self.am
        am.update_bar(bar)
        if not am.inited or not self._update_indicators():
            return

        if self.pos > 0 and bar.close_price < self._stop_price():
            self._sell_to_target(bar, 0, "跌破通道止损")
            self.put_event()
            return

        if self.regime == 0:
            self.put_event()
            return

        if bar.close_price <= self.buy_price:
            target_pos: int = (
                self.base_size + self.swing_size
                if self.regime == 2
                else self.swing_size
            )
            self._buy_to_target(bar, target_pos)
        elif bar.close_price >= self.sell_price:
            target_pos = self.base_size if self.regime == 2 else 0
            self._sell_to_target(bar, target_pos, "通道上沿高抛")

        self.put_event()

    def _update_indicators(self) -> bool:
        """计算上一窗口通道、均线和当前市场状态。"""
        am: ArrayManager = self.am
        closes: np.ndarray = am.close_array
        prior_highs: np.ndarray = am.high_array[-self.channel_window - 1:-1]
        prior_lows: np.ndarray = am.low_array[-self.channel_window - 1:-1]

        if not len(prior_highs) or not len(prior_lows):
            return False

        channel_high: float = float(np.max(prior_highs))
        channel_low: float = float(np.min(prior_lows))
        channel_range: float = channel_high - channel_low
        channel_mid: float = (channel_high + channel_low) / 2
        if channel_low <= 0 or channel_range <= 0 or channel_mid <= 0:
            return False

        fast_ma: float = float(np.mean(closes[-self.fast_window:]))
        slow_ma: float = float(np.mean(closes[-self.slow_window:]))
        previous_slow: float = float(np.mean(
            closes[
                -self.slow_window - self.trend_lookback:
                -self.trend_lookback
            ]
        ))
        if previous_slow <= 0:
            return False

        slope_pct: float = (slow_ma / previous_slow - 1) * 100
        width_pct: float = channel_range / channel_mid * 100

        self.channel_high = round(channel_high, 2)
        self.channel_low = round(channel_low, 2)
        self.fast_ma = round(fast_ma, 2)
        self.slow_ma = round(slow_ma, 2)
        self.slow_ma_slope_pct = round(slope_pct, 2)
        self.channel_width_pct = round(width_pct, 2)
        self.buy_price = round(
            channel_low + channel_range * self.buy_zone_ratio,
            2,
        )
        self.sell_price = round(
            channel_low + channel_range * self.sell_zone_ratio,
            2,
        )

        is_box: bool = (
            self.channel_width_pct <= self.box_max_width_pct
            and abs(self.slow_ma_slope_pct) <= self.box_max_slope_pct
        )
        is_uptrend: bool = (
            self.fast_ma > self.slow_ma
            and self.slow_ma_slope_pct >= self.uptrend_min_slope_pct
        )

        if is_uptrend:
            self.regime = 2
        elif is_box:
            self.regime = 1
        else:
            self.regime = 0
        return True

    def _stop_price(self) -> float:
        """返回通道下沿之外的止损价格。"""
        return round(self.channel_low * (1 - self.stop_loss_pct / 100), 2)

    def _buy_to_target(self, bar: BarData, target_pos: int) -> None:
        """补仓到目标仓位。"""
        max_pos: int = max(0, self.base_size) + max(0, self.swing_size)
        target_pos = min(max_pos, max(0, int(target_pos)))
        buy_volume: int = target_pos - int(self.pos)
        buy_volume = self._round_down_to_lot(buy_volume)
        if buy_volume > 0:
            self.buy(bar.close_price, buy_volume, mark="通道下沿低吸")

    def _sell_to_target(
        self,
        bar: BarData,
        target_pos: int,
        mark: str,
    ) -> None:
        """在 T+1 可卖数量范围内减仓到目标仓位。"""
        requested: int = max(0, int(self.pos) - max(0, int(target_pos)))
        if self.t1:
            available: int = max(
                0,
                int(self.yd_pos - self.local_sell_frozen),
            )
            requested = min(requested, available)

        sell_volume: int = self._round_down_to_lot(requested)
        if sell_volume > 0:
            self.sell(bar.close_price, sell_volume, mark=mark)

    def _round_down_to_lot(self, volume: int) -> int:
        """按最小交易单位向下取整。"""
        lot_size: int = max(1, int(self.lot_size))
        return max(0, int(volume) // lot_size * lot_size)

    def on_order(self, order: OrderData) -> None:
        """处理订单更新。"""
        direction: str = order.direction.value if order.direction else ""
        mark: str = self.get_order_mark(order)
        message: str = (
            f"订单通知：状态={order.status.value}，方向={direction}，"
            f"开平={order.offset.value}，价格={order.price:.2f}，"
            f"委托数量={order.volume}，成交数量={order.traded}，"
            f"委托号={order.vt_orderid}"
        )
        if mark:
            message += f"，标注={mark}"
        self.send_wecom(message)

    def on_trade(self, trade: TradeData) -> None:
        """成交后刷新策略状态。"""
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """处理停止单更新。"""
        pass
