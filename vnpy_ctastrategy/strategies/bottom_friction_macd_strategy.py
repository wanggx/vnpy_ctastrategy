from datetime import date, time

import numpy as np

from vnpy.trader.constant import Direction, Offset

from vnpy_ctastrategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager,
)


class BottomFrictionMacdStrategy(CtaTemplate):
    """
    底仓 + 摩擦仓位 + MACD 盘中做 T 的 1 分钟 CTA 策略。

    规则：
    1. 允许设置底仓和摩擦仓位；仓位不会低于底仓。
    2. 3日均线大于5日均线时，只保留底仓。
    3. 收盘价低于10日均线时，清仓。
    4. 亏损超过 5 点时，清仓。
    5. MACD 上穿 0 轴时，买入摩擦仓位；MACD 下穿 0 轴时，卖出摩擦仓位。
    """

    author = "Copilot"

    t1: bool = True

    base_size: int = 100
    friction_size: int = 50
    fast_window: int = 3
    slow_window: int = 5
    ma10_window: int = 10
    stop_loss_points: float = 5.0
    profit_take_points: float = 3.0
    profit_take_min_points: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    fast_ma: float = 0.0
    slow_ma: float = 0.0
    ma10: float = 0.0
    macd_line: float = 0.0
    signal_line: float = 0.0
    macd_hist: float = 0.0
    last_macd_hist: float = 0.0
    avg_price: float = 0.0

    parameters = [
        "base_size",
        "friction_size",
        "fast_window",
        "slow_window",
        "ma10_window",
        "stop_loss_points",
        "profit_take_points",
        "profit_take_min_points",
    ]
    variables = [
        "fast_ma",
        "slow_ma",
        "ma10",
        "macd_line",
        "signal_line",
        "macd_hist",
        "last_macd_hist",
        "avg_price",
    ]

    def __init__(
        self,
        cta_engine: object,
        strategy_name: str,
        vt_symbol: str,
        setting: dict,
    ) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.close_prices: list[float] = []
        self.macd_values: list[float] = []
        self.daily_close_prices: list[float] = []
        self.current_day: date | None = None
        self.day_close_price: float = 0.0
        self.macd_trading_day: date | None = None
        self.active_buy_orders: dict[str, float] = {}

    def on_init(self) -> None:
        """策略初始化。"""
        self.write_log("策略初始化：A股T+1模式开启，卖出仅用于平仓/减仓")

        self.bg: BarGenerator = BarGenerator(self.on_bar)
        self.am: ArrayManager = ArrayManager()

        self.close_prices = []
        self.macd_values = []
        self.daily_close_prices = []
        self.current_day = None
        self.day_close_price = 0.0
        self.macd_trading_day = None
        self.active_buy_orders.clear()
        self.avg_price = 0.0
        self.macd_line = 0.0
        self.signal_line = 0.0
        self.macd_hist = 0.0
        self.last_macd_hist = 0.0

        self.load_bar(60)

    def on_start(self) -> None:
        """策略启动。"""
        self.write_log("策略启动")
        self.put_event()

    def on_stop(self) -> None:
        """策略停止。"""
        self.write_log("策略停止")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """收到 tick 数据时，送入分钟级 BarGenerator。"""
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """收到 1 分钟 K 线时执行。"""
        if not self._prepare_intraday_macd(bar):
            return

        self.cancel_all()

        am: ArrayManager = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        self._update_daily_close_series(bar)
        self.fast_ma = self._calc_daily_ma(self.fast_window)
        self.slow_ma = self._calc_daily_ma(self.slow_window)
        self.ma10 = self._calc_daily_ma(self.ma10_window)

        prev_macd_hist: float = self.macd_hist
        self.macd_line, self.signal_line, self.macd_hist = self._calc_macd(bar.close_price)
        self.last_macd_hist = prev_macd_hist

        macd_cross_up: bool = prev_macd_hist <= 0 and self.macd_hist > 0
        macd_cross_down: bool = prev_macd_hist >= 0 and self.macd_hist < 0

        if self.pos > 0 and self.avg_price > 0 and (self.avg_price - bar.close_price) > self.stop_loss_points:
            self.write_log(
                f"止损触发：均价={self.avg_price:.2f}，当前价={bar.close_price:.2f}，"
                f"浮亏={self.avg_price - bar.close_price:.2f}，清空仓位"
            )
            self._set_target_position(bar, 0)
            self.put_event()
            return

        if bar.close_price < self.ma10:
            self.write_log(f"跌破10日线：价格={bar.close_price:.2f}，10日线={self.ma10:.2f}，清仓")
            self._set_target_position(bar, 0)
            self.put_event()
            return

        if self.fast_ma > self.slow_ma:
            self.write_log("均线多头排列：3日线大于5日线，维持底仓")
            self._set_target_position(bar, self.base_size)
            self.put_event()
            return

        if self.pos < self.base_size:
            self.write_log(f"当前仓位低于底仓：当前={self.pos}，目标={self.base_size}，补到底仓")
            self._set_target_position(bar, self.base_size)
            self.put_event()
            return

        if macd_cross_up:
            self.write_log("MACD上穿零轴：加仓摩擦仓位")
            self._set_target_position(bar, self.base_size + self.friction_size)
        elif macd_cross_down:
            if self.pos > self.base_size and self.avg_price > 0:
                current_profit: float = bar.close_price - self.avg_price
                should_reduce: bool = (
                    self.profit_take_min_points <= current_profit <= self.profit_take_points
                    or True
                )
                if should_reduce:
                    self.write_log(
                        f"MACD下穿零轴或浮盈达到目标：当前价={bar.close_price:.2f}，"
                        f"均价={self.avg_price:.2f}，浮盈={current_profit:.2f}，减仓摩擦仓位"
                    )
                    self._set_target_position(bar, max(self.base_size, self.pos - self.friction_size))
                else:
                    self.write_log(
                        f"MACD下穿零轴但浮盈未达到目标：当前价={bar.close_price:.2f}，"
                        f"均价={self.avg_price:.2f}，浮盈={current_profit:.2f}"
                    )
                    self._set_target_position(bar, self.pos)
            else:
                self.write_log("MACD下穿零轴但无有效摩擦仓位或均价，维持当前仓位")
                self._set_target_position(bar, self.pos)
        else:
            self.write_log("MACD未穿零轴：维持当前仓位")
            self._set_target_position(bar, self.pos)

        self.put_event()

    def _prepare_intraday_macd(self, bar: BarData) -> bool:
        """切换交易日并过滤 09:30 之前的无效分钟数据。"""
        trading_day: date = bar.datetime.date()

        if trading_day != self.macd_trading_day:
            self.macd_trading_day = trading_day
            self.close_prices.clear()
            self.macd_values.clear()
            self.macd_line = 0.0
            self.signal_line = 0.0
            self.macd_hist = 0.0
            self.last_macd_hist = 0.0
            self.write_log(f"MACD切换交易日={trading_day}，分钟数据和指标已重置")

        if bar.datetime.time() < time(9, 30):
            self.write_log(
                f"忽略开盘前数据：时间={bar.datetime}，"
                f"收盘价={bar.close_price:.2f}，09:30起才参与MACD计算"
            )
            return False

        return True

    def _update_daily_close_series(self, bar: BarData) -> None:
        """基于 1 分钟数据构造日线收盘价序列。"""
        current_day = bar.datetime.date()

        if self.current_day is None:
            self.current_day = current_day
            self.day_close_price = float(bar.close_price)
            return

        if current_day != self.current_day:
            self.daily_close_prices.append(self.day_close_price)
            self.current_day = current_day
            self.day_close_price = float(bar.close_price)
        else:
            self.day_close_price = float(bar.close_price)

    def _calc_daily_ma(self, window: int) -> float:
        """基于日线收盘价序列计算移动平均。"""
        if len(self.daily_close_prices) < window:
            return 0.0

        values: list[float] = self.daily_close_prices[-window:]
        return float(np.mean(values))

    def _calc_macd(self, close_price: float) -> tuple[float, float, float]:
        """基于最新 1 分钟收盘价计算 MACD。"""
        self.close_prices.append(float(close_price))

        if len(self.close_prices) < max(self.macd_fast, self.macd_slow):
            self.write_log(
                f"MACD计算来源=自定义代码，收盘价={close_price:.2f}，"
                "MACD=0.000000，Signal=0.000000，Hist=0.000000（数据不足）"
            )
            return 0.0, 0.0, 0.0

        ema_fast: float = self._ema(self.close_prices, self.macd_fast)
        ema_slow: float = self._ema(self.close_prices, self.macd_slow)
        macd_line: float = ema_fast - ema_slow

        self.macd_values.append(macd_line)
        if len(self.macd_values) < self.macd_signal:
            signal_line: float = 0.0
        else:
            signal_line = self._ema(self.macd_values[-self.macd_signal:], self.macd_signal)

        macd_hist: float = macd_line - signal_line
        self.write_log(
            f"MACD计算来源=自定义代码，收盘价={close_price:.2f}，"
            f"MACD={macd_line:.6f}，Signal={signal_line:.6f}，Hist={macd_hist:.6f}"
        )
        return macd_line, signal_line, macd_hist

    def _ema(self, values: list[float], period: int) -> float:
        """计算指数移动平均。"""
        if not values:
            return 0.0

        if len(values) == 1:
            return float(values[-1])

        alpha: float = 2.0 / (period + 1)
        ema: float = float(values[0])
        for value in values[1:]:
            ema = alpha * value + (1 - alpha) * ema
        return ema

    def _set_target_position(self, bar: BarData, target_pos: float) -> None:
        """根据目标仓位调整多头持仓。"""
        max_pos: int = max(0, int(self.base_size)) + max(0, int(self.friction_size))
        target_pos = min(max_pos, max(0, int(target_pos)))
        pending_buy_volume: float = sum(self.active_buy_orders.values())
        effective_pos: float = self.pos + pending_buy_volume

        self.write_log(
            f"目标仓位={target_pos}，当前仓位={self.pos}，"
            f"未成交买量={pending_buy_volume}，预计总仓位={effective_pos}，"
            f"价格={bar.close_price:.2f}"
        )

        if target_pos > effective_pos:
            buy_volume: int = int(target_pos - effective_pos)
            if buy_volume > 0:
                self.write_log(f"买入开仓：数量={buy_volume}，价格={bar.close_price:.2f}，A股T+1模式下仅允许开多")
                vt_orderids: list[str] = self.buy(bar.close_price, buy_volume)
                if vt_orderids:
                    reserved_volume: float = buy_volume / len(vt_orderids)
                    for vt_orderid in vt_orderids:
                        self.active_buy_orders[vt_orderid] = reserved_volume
        elif target_pos < self.pos:
            sell_volume: int = int(self.pos - target_pos)
            if sell_volume > 0:
                self.write_log(f"卖出减仓/平仓：数量={sell_volume}，价格={bar.close_price:.2f}")
                self.sell(bar.close_price, sell_volume)

    def on_order(self, order: OrderData) -> None:
        """订单更新回调。"""
        if order.direction == Direction.LONG and order.offset in {Offset.OPEN, Offset.NONE}:
            if order.is_active():
                self.active_buy_orders[order.vt_orderid] = max(
                    float(order.volume - order.traded), 0.0
                )
            else:
                self.active_buy_orders.pop(order.vt_orderid, None)

    def on_trade(self, trade: TradeData) -> None:
        """成交更新回调。"""
        self.write_log(
            f"成交通知：方向={trade.direction.value}，开平={trade.offset.value}，"
            f"价格={trade.price:.2f}，数量={trade.volume}"
        )

        if trade.direction == Direction.LONG and trade.offset == Offset.OPEN:
            if self.pos > trade.volume:
                self.avg_price = (
                    self.avg_price * (self.pos - trade.volume) + trade.price * trade.volume
                ) / self.pos
            else:
                self.avg_price = trade.price
        elif trade.direction == Direction.SHORT and trade.offset == Offset.CLOSE:
            self.avg_price = 0.0

        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """停止单更新回调。"""
        pass
