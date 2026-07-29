from datetime import date, time

import numpy as np

from vnpy.trader.constant import Direction, Offset

from vnpy_ctastrategy import (
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager,
)
from vnpy_ctastrategy.base import EngineType

from .base import CtaTemplateService
from .market_sentiment import (
    MarketSentimentService,
    MarketSentimentSnapshot,
)
from .talib_indicators import EmaMacdCalculator, MacdResult


class BottomFrictionStrategy(CtaTemplateService):
    """
    底仓 + 摩擦仓位 + MACD 盘中做 T 的 1 分钟 CTA 策略。

    规则：
    1. 允许设置底仓和摩擦仓位；仓位不会低于底仓。
    2. 3日均线大于5日均线时，只保留底仓。
    3. 收盘价低于10日均线时，清仓。
    4. 亏损超过 5 点时，清仓。
    5. MACD 上穿 0 轴时，买入摩擦仓位；MACD 下穿 0 轴时，卖出摩擦仓位。
    6. 下跌超过3500家且超过1/3板块偏弱时仓位减半；下跌超过4000家时清仓。
    7. 买入摩擦仓位后，记录入场价；涨幅超过摩擦止盈点数时，卖出摩擦仓位回到底仓。
    8. 持仓最高收益率回撤达到设定点数时，清仓。
    9. 最高收益率超过保护阈值后，回落到最低保护收益时，清仓。
    """

    author = "Copilot"

    t1: bool = True

    base_size: int = 1000
    friction_size: int = 500
    fast_window: int = 3
    slow_window: int = 5
    ma10_window: int = 10
    stop_loss_points: float = 5.0
    profit_take_points: float = 3.0
    profit_take_min_points: float = 2.0
    friction_take_profit_points: float = 2.0
    max_profit_drawdown_points: float = 10.0
    profit_protection_trigger_points: float = 10.0
    profit_protection_floor_points: float = 5.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    shares_per_lot: int = 100
    market_sentiment_enabled: bool = True
    market_decline_reduce_threshold: int = 3500
    market_decline_exit_threshold: int = 4000
    sector_decline_ratio_threshold: float = round(1 / 3, 2)

    fast_ma: float = 0.0
    slow_ma: float = 0.0
    ma10: float = 0.0
    macd_line: float = 0.0
    signal_line: float = 0.0
    macd_hist: float = 0.0
    last_macd_hist: float = 0.0
    fast_slow_ma: str = ""
    macd_triple: str = ""
    avg_price: float = 0.0
    pos_avg_price: float = 0.0
    friction_entry_price: float = 0.0
    peak_profit_points: float = 0.0
    profit_drawdown_active: bool = False
    profit_protection_active: bool = False
    market_sentiment_score: float = 0.0
    market_declining_count: int = 0
    declining_sector_count: int = 0
    valid_sector_count: int = 0
    declining_sector_ratio: float = 0.0
    sentiment_risk_level: int = 0
    sentiment_target_pos: int = -1

    parameters = [
        "base_size",
        "friction_size",
        "fast_window",
        "slow_window",
        "ma10_window",
        "stop_loss_points",
        "profit_take_points",
        "profit_take_min_points",
        "friction_take_profit_points",
        "max_profit_drawdown_points",
        "profit_protection_trigger_points",
        "profit_protection_floor_points",
        "market_sentiment_enabled",
        "market_decline_reduce_threshold",
        "market_decline_exit_threshold",
        "sector_decline_ratio_threshold",
    ]
    parameter_labels = {
        "t1": "T+1",
        "base_size": "底仓",
        "friction_size": "T仓",
        "fast_window": "快线",
        "slow_window": "慢线",
        "ma10_window": "清仓均线",
        "stop_loss_points": "止损",
        "profit_take_points": "止盈",
        "profit_take_min_points": "最低止盈",
        "friction_take_profit_points": "摩擦止盈",
        "max_profit_drawdown_points": "最大收益回撤",
        "profit_protection_trigger_points": "收益保护触发点",
        "profit_protection_floor_points": "最低保护收益",
        "market_sentiment_enabled": "情绪开关",
        "market_decline_reduce_threshold": "大盘减仓数",
        "market_decline_exit_threshold": "大盘清仓数",
        "sector_decline_ratio_threshold": "板块弱势比",
    }
    variables = [
        "fast_slow_ma",
        "ma10",
        "macd_triple",
        "last_macd_hist",
        "avg_price",
        "pos_avg_price",
        "friction_entry_price",
        "peak_profit_points",
        "profit_drawdown_active",
        "profit_protection_active",
        "market_sentiment_score",
        "market_declining_count",
        "declining_sector_count",
        "valid_sector_count",
        "declining_sector_ratio",
        "sentiment_risk_level",
        "sentiment_target_pos",
    ]
    variable_labels = {
        "fast_slow_ma": "快慢线值",
        "ma10": "清仓线值",
        "macd_triple": "MACD",
        "last_macd_hist": "前MACD柱",
        "avg_price": "均价",
        "pos_avg_price": "持仓均价",
        "friction_entry_price": "摩擦入场价",
        "peak_profit_points": "最高收益率",
        "profit_drawdown_active": "收益回撤清仓中",
        "profit_protection_active": "收益保护已激活",
        "market_sentiment_score": "情绪分",
        "market_declining_count": "下跌家数",
        "declining_sector_count": "弱势板块数",
        "valid_sector_count": "有效板块数",
        "declining_sector_ratio": "弱势板块比",
        "sentiment_risk_level": "情绪风险",
        "sentiment_target_pos": "情绪目标仓",
    }

    def __init__(
        self,
        cta_engine: object,
        strategy_name: str,
        vt_symbol: str,
        setting: dict,
    ) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.macd_calculator: EmaMacdCalculator = EmaMacdCalculator(
            fast_period=self.macd_fast,
            slow_period=self.macd_slow,
            signal_period=self.macd_signal,
        )
        # 保留原属性，兼容现有派生策略和指标比较脚本。
        self.close_prices: list[float] = (
            self.macd_calculator.close_prices
        )
        self.macd_values: list[float] = (
            self.macd_calculator.macd_values
        )
        self.daily_close_prices: list[float] = []
        self.current_day: date | None = None
        self.day_close_price: float = 0.0
        self.macd_trading_day: date | None = None
        self.avg_trading_day: date | None = None
        self.active_buy_orders: dict[str, float] = {}
        self.friction_tp_pending: bool = False
        self.profit_drawdown_pending: bool = False
        self.profit_drawdown_active = False
        self.profit_protection_active = False
        self.sentiment_service: MarketSentimentService | None = None

    def on_init(self) -> None:
        """策略初始化。"""
        self.bg: BarGenerator = BarGenerator(self.on_bar)
        self.am: ArrayManager = ArrayManager()

        self.macd_calculator.reset()
        self.daily_close_prices = []
        self.current_day = None
        self.day_close_price = 0.0
        self.macd_trading_day = None
        self.avg_trading_day = None
        self.active_buy_orders.clear()
        self.friction_tp_pending = False
        self.profit_drawdown_pending = False
        self.profit_drawdown_active = False
        self.profit_protection_active = False
        self.avg_price = 0.0
        self.pos_avg_price = 0.0
        self.friction_entry_price = 0.0
        self.peak_profit_points = 0.0
        self.macd_line = 0.0
        self.signal_line = 0.0
        self.macd_hist = 0.0
        self.last_macd_hist = 0.0
        self.fast_slow_ma = ""
        self.macd_triple = ""
        self.market_sentiment_score = 0.0
        self.market_declining_count = 0
        self.declining_sector_count = 0
        self.valid_sector_count = 0
        self.declining_sector_ratio = 0.0
        self.sentiment_risk_level = 0
        self.sentiment_target_pos = -1

        if (
            self.market_sentiment_enabled
            and self.get_engine_type() == EngineType.LIVE
        ):
            self.sentiment_service = (
                MarketSentimentService.get_shared(
                    name="a_share",
                    refresh_interval=10,
                    stale_after=30,
                )
            )
        else:
            self.sentiment_service = None

        self.load_bar(60)

    def on_start(self) -> None:
        """策略启动。"""
        if self.sentiment_service is not None:
            self.sentiment_service.start()
        self.put_event()

    def on_stop(self) -> None:
        """策略停止。"""
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """收到 tick 数据时，送入分钟级 BarGenerator。"""
        self.bg.update_tick(tick)

        trading_day: date = tick.datetime.date()
        if trading_day != self.avg_trading_day:
            self.avg_trading_day = trading_day
            self.avg_price = 0.0

        if tick.volume > 0 and tick.turnover > 0 and self.shares_per_lot > 0:
            self.avg_price = round(
                float(tick.turnover / (tick.volume * self.shares_per_lot)),
                2,
            )

        if self._check_profit_drawdown(tick.last_price):
            exit_price: float = (
                tick.bid_price_1
                if tick.bid_price_1 > 0
                else tick.last_price
            )
            self._submit_profit_drawdown_exit(exit_price)
            self.put_event()
            return

        self._check_friction_take_profit(tick)

    def _check_profit_drawdown(self, last_price: float) -> bool:
        """检查最高收益率是否已回撤到清仓阈值。"""
        if self.pos <= 0 or self.pos_avg_price <= 0 or last_price <= 0:
            if self.pos <= 0:
                self.peak_profit_points = 0.0
                self.profit_drawdown_pending = False
                self.profit_drawdown_active = False
                self.profit_protection_active = False
            return False

        if self.profit_drawdown_active:
            return True

        current_profit_points: float = round(
            (last_price - self.pos_avg_price)
            / self.pos_avg_price
            * 100,
            2,
        )
        self.peak_profit_points = max(
            self.peak_profit_points,
            current_profit_points,
        )

        if (
            self.profit_protection_trigger_points > 0
            and self.peak_profit_points
            > self.profit_protection_trigger_points
        ):
            self.profit_protection_active = True

        protected_profit_hit: bool = (
            self.profit_protection_active
            and current_profit_points
            <= self.profit_protection_floor_points
        )

        triggered: bool = (
            protected_profit_hit
            or (
                self.max_profit_drawdown_points > 0
                and self.peak_profit_points > 0
                and self.peak_profit_points - current_profit_points
                >= self.max_profit_drawdown_points
            )
        )
        if triggered:
            self.profit_drawdown_active = True
        return triggered

    def _submit_profit_drawdown_exit(self, price: float) -> None:
        """撤销未成交委托并卖出当前全部可卖持仓。"""
        if self.profit_drawdown_pending:
            return

        self.cancel_all()
        available: float = (
            max(self.yd_pos - self.local_sell_frozen, 0)
            if self.t1
            else self.pos
        )
        sell_volume: int = int(min(self.pos, available))
        if self.shares_per_lot > 0:
            sell_volume = (
                sell_volume // self.shares_per_lot
                * self.shares_per_lot
            )
        if sell_volume <= 0:
            return

        vt_orderids: list[str] = self.sell(
            price,
            sell_volume,
            mark="收益回撤清仓",
        )
        if vt_orderids:
            self.profit_drawdown_pending = True

    def _check_friction_take_profit(self, tick: TickData) -> None:
        """价格涨到摩擦入场价+止盈点数时，到价卖出摩擦仓回到底仓。"""
        if (
            self.friction_entry_price <= 0
            or self.friction_tp_pending
            or self.pos <= self.base_size
        ):
            return

        target_price: float = round(
            self.friction_entry_price
            * (1 + self.friction_take_profit_points / 100),
            2,
        )
        if tick.last_price < target_price:
            return

        available: float = max(self.yd_pos - self.local_sell_frozen, 0)
        sell_volume: int = int(min(self.pos - self.base_size, available))
        if sell_volume <= 0:
            return

        sell_price: float = (
            tick.bid_price_1 if tick.bid_price_1 > 0 else tick.last_price
        )
        vt_orderids: list[str] = self.sell(
            sell_price,
            sell_volume,
            mark="摩擦仓止盈"
        )
        if vt_orderids:
            self.friction_tp_pending = True

    def on_bar(self, bar: BarData) -> None:
        """收到 1 分钟 K 线时执行。"""
        am: ArrayManager = self.am
        am.update_bar(bar)

        if self._check_profit_drawdown(bar.close_price):
            self._submit_profit_drawdown_exit(bar.close_price)
            self.put_event()
            return

        if not am.inited:
            return

        if not self._prepare_intraday_macd(bar):
            return

        self.cancel_all()

        self._update_daily_close_series(bar)
        self.fast_ma = self._calc_daily_ma(self.fast_window)
        self.slow_ma = self._calc_daily_ma(self.slow_window)
        self.ma10 = self._calc_daily_ma(self.ma10_window)

        prev_macd_hist: float = self.macd_hist
        self.macd_line, self.signal_line, self.macd_hist = self._calc_macd(bar.close_price)
        self.last_macd_hist = prev_macd_hist

        self.fast_slow_ma = f"{self.fast_ma}/{self.slow_ma}"
        self.macd_triple = f"{self.macd_line}/{self.signal_line}/{self.macd_hist}"

        macd_cross_up: bool = prev_macd_hist <= 0 and self.macd_hist > 0
        macd_cross_down: bool = prev_macd_hist >= 0 and self.macd_hist < 0

        if self.pos > 0 and self.pos_avg_price > 0:
            current_loss: float = round(
                (self.pos_avg_price - bar.close_price)
                / self.pos_avg_price
                * 100,
                2,
            )
            if current_loss > self.stop_loss_points:
                self._set_target_position(bar, 0, "止损")
                self.put_event()
                return

        if bar.close_price < self.ma10:
            self._set_target_position(bar, 0, "跌破10日线")
            self.put_event()
            return

        if self._apply_market_sentiment_risk(bar):
            self.put_event()
            return

        if self.fast_ma > self.slow_ma:
            self._set_target_position(bar, self.base_size, "均线多头排列，回到底仓")
            self.put_event()
            return

        if self.pos < self.base_size:
            self._set_target_position(bar, self.base_size, "仓位低于底仓，补仓")
            self.put_event()
            return

        if macd_cross_up:
            self._set_target_position(
                bar,
                self.base_size + self.friction_size,
                "MACD上穿零轴"
            )
            self.friction_entry_price = bar.close_price
        elif macd_cross_down:
            if self.pos > self.base_size and self.pos_avg_price > 0:
                current_profit: float = round(
                    (bar.close_price - self.pos_avg_price)
                    / self.pos_avg_price
                    * 100,
                    2,
                )
                should_reduce: bool = (
                    self.profit_take_min_points <= current_profit <= self.profit_take_points
                    or True
                )
                if should_reduce:
                    self._set_target_position(
                        bar,
                        max(self.base_size, self.pos - self.friction_size),
                        "MACD下穿零轴，减摩擦仓"
                    )
                else:
                    self._set_target_position(bar, self.pos)
            else:
                self._set_target_position(bar, self.pos)
        else:
            self._set_target_position(bar, self.pos)

        self.put_event()

    def _apply_market_sentiment_risk(self, bar: BarData) -> bool:
        """在普通交易信号前执行全市场和板块情绪风控。"""
        if (
            not self.market_sentiment_enabled
            or self.sentiment_service is None
        ):
            return False

        snapshot: MarketSentimentSnapshot = (
            self.sentiment_service.get_latest()
        )
        if not snapshot.available:
            self.sentiment_risk_level = 0
            self.sentiment_target_pos = -1
            return False

        risk_level: int = self._evaluate_market_sentiment(snapshot)

        if risk_level == 0:
            self.sentiment_risk_level = 0
            self.sentiment_target_pos = -1
            return False

        if risk_level != self.sentiment_risk_level:
            if risk_level == 2:
                self.sentiment_target_pos = 0
            else:
                self.sentiment_target_pos = max(
                    0,
                    int(self.pos / 2),
                )
            self.sentiment_risk_level = risk_level

        mark: str = (
            "市场超过4000家下跌，清仓"
            if risk_level == 2
            else "市场和板块偏弱，仓位减半"
        )
        self._set_target_position(
            bar,
            self.sentiment_target_pos,
            mark,
        )
        return True

    def _evaluate_market_sentiment(
        self,
        snapshot: MarketSentimentSnapshot,
    ) -> int:
        """更新情绪变量并返回风险级别：0正常、1减半、2清仓。"""
        self.market_sentiment_score = round(float(snapshot.score), 2)
        self.market_declining_count = snapshot.breadth.declining

        sector_states = list(snapshot.sectors.values())
        self.valid_sector_count = len(sector_states)
        self.declining_sector_count = sum(
            state.breadth.declining > state.breadth.advancing
            for state in sector_states
        )
        if self.valid_sector_count:
            self.declining_sector_ratio = round(
                self.declining_sector_count / self.valid_sector_count,
                2,
            )
        else:
            self.declining_sector_ratio = 0.0

        if (
            self.market_declining_count
            > self.market_decline_exit_threshold
        ):
            return 2

        if (
            self.market_declining_count
            > self.market_decline_reduce_threshold
            and self.declining_sector_ratio
            > self.sector_decline_ratio_threshold
        ):
            return 1

        return 0

    def _prepare_intraday_macd(self, bar: BarData) -> bool:
        """切换交易日并过滤 09:30 之前的无效分钟数据。"""
        trading_day: date = bar.datetime.date()

        if trading_day != self.macd_trading_day:
            self.macd_trading_day = trading_day
            self.macd_calculator.reset()
            self.macd_line = 0.0
            self.signal_line = 0.0
            self.macd_hist = 0.0
            self.last_macd_hist = 0.0

        if bar.datetime.time() < time(9, 30):
            return False

        return True

    def _update_daily_close_series(self, bar: BarData) -> None:
        """基于 1 分钟数据构造日线收盘价序列。"""
        current_day = bar.datetime.date()

        if self.current_day is None:
            self.current_day = current_day
            self.day_close_price = round(float(bar.close_price), 2)
            return

        if current_day != self.current_day:
            self.daily_close_prices.append(self.day_close_price)
            self.current_day = current_day
            self.day_close_price = round(float(bar.close_price), 2)
        else:
            self.day_close_price = round(float(bar.close_price), 2)

    def _calc_daily_ma(self, window: int) -> float:
        """基于日线收盘价序列计算移动平均。"""
        if len(self.daily_close_prices) < window:
            return 0.0

        values: list[float] = self.daily_close_prices[-window:]
        return round(float(np.mean(values)), 2)

    def _calc_macd(self, close_price: float) -> tuple[float, float, float]:
        """通过共享流式组件计算最新 1 分钟 MACD。"""
        result: MacdResult = self.macd_calculator.update(close_price)
        if not result.ready:
            return 0.0, 0.0, 0.0

        macd, signal, histogram = result.as_tuple()
        return round(macd, 2), round(signal, 2), round(histogram, 2)

    def _set_target_position(
        self,
        bar: BarData,
        target_pos: float,
        mark: str = ""
    ) -> None:
        """根据目标仓位调整多头持仓。"""
        max_pos: int = max(0, int(self.base_size)) + max(0, int(self.friction_size))
        target_pos = min(max_pos, max(0, int(target_pos)))
        pending_buy_volume: float = round(
            sum(self.active_buy_orders.values()),
            2,
        )
        effective_pos: float = round(self.pos + pending_buy_volume, 2)

        if target_pos > effective_pos:
            buy_volume: int = int(target_pos - effective_pos)
            if buy_volume > 0:
                vt_orderids: list[str] = self.buy(
                    bar.close_price,
                    buy_volume,
                    mark=mark
                )
                if vt_orderids:
                    reserved_volume: float = round(
                        buy_volume / len(vt_orderids),
                        2,
                    )
                    for vt_orderid in vt_orderids:
                        self.active_buy_orders[vt_orderid] = reserved_volume
        elif target_pos < self.pos:
            sell_volume: int = int(self.pos - target_pos)
            if sell_volume > 0:
                self.sell(bar.close_price, sell_volume, mark=mark)

    def on_order(self, order: OrderData) -> None:
        """订单更新回调。"""
        mark: str = self.get_order_mark(order)
        direction: str = order.direction.value if order.direction else ""
        message: str = (
            f"订单通知：状态={order.status.value}，方向={direction}，"
            f"开平={order.offset.value}，价格={order.price:.2f}，"
            f"委托数量={order.volume}，成交数量={order.traded}，"
            f"委托号={order.vt_orderid}"
        )
        if mark:
            message += f"，标注={mark}"
        self.send_wecom(message)

        if order.direction == Direction.LONG and order.offset in {Offset.OPEN, Offset.NONE}:
            if order.is_active():
                self.active_buy_orders[order.vt_orderid] = max(
                    round(float(order.volume - order.traded), 2),
                    0.0,
                )
            else:
                self.active_buy_orders.pop(order.vt_orderid, None)

        if mark == "摩擦仓止盈" and not order.is_active():
            self.friction_tp_pending = False
        if mark == "收益回撤清仓" and not order.is_active():
            self.profit_drawdown_pending = False

    def on_trade(self, trade: TradeData) -> None:
        """成交更新回调。"""
        direction: str = trade.direction.value if trade.direction else ""
        message: str = (
            f"成交通知：方向={direction}，开平={trade.offset.value}，"
            f"价格={trade.price:.2f}，数量={trade.volume}"
        )
        self.send_wecom(message)

        if trade.direction == Direction.LONG and trade.offset == Offset.OPEN:
            new_position: bool = self.pos <= trade.volume
            if self.pos > trade.volume:
                self.pos_avg_price = round(
                    (
                        self.pos_avg_price * (self.pos - trade.volume)
                        + trade.price * trade.volume
                    ) / self.pos,
                    2,
                )
            else:
                self.pos_avg_price = round(float(trade.price), 2)
            self.peak_profit_points = max(
                round(
                    (trade.price - self.pos_avg_price)
                    / self.pos_avg_price
                    * 100,
                    2,
                ),
                0.0,
            )
            if new_position:
                self.profit_drawdown_active = False
                self.profit_drawdown_pending = False
                self.profit_protection_active = False
        elif trade.direction == Direction.SHORT and trade.offset == Offset.CLOSE:
            if self.pos <= 0:
                self.pos_avg_price = 0.0
                self.peak_profit_points = 0.0
                self.profit_drawdown_pending = False
                self.profit_drawdown_active = False
                self.profit_protection_active = False

        if self.pos <= self.base_size:
            self.friction_entry_price = 0.0

        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """停止单更新回调。"""
        pass
