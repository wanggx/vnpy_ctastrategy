from __future__ import annotations

from datetime import date, time

import numpy as np

from vnpy.trader.constant import Direction, Offset
from vnpy.trader.utility import load_json

from vnpy_ctastrategy import (
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    BarGenerator,
    ArrayManager,
)

from .base import CtaTemplateService
from .signals import (
    SECTOR_STRONG_ENTRY_MARK,
    BottomFrictionBarSignal,
    BottomFrictionTickSignal,
    FrictionResult,
    ProfitDrawdownState,
    SignalType,
)


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
    7. 标的所属行业情绪分低于阈值（默认 40，即偏弱及以下）时，卖出摩擦仓回到底仓。
    8. 标的所属行业情绪分不低于阈值（默认 80，即极强）且摩擦位为空时，买入摩擦仓位（一天最多一次）。
    9. 买入摩擦仓位后，记录入场价；涨幅超过摩擦止盈点数时，卖出摩擦仓位回到底仓。
    10. 行业极强买入的摩擦仓，按成交价 + 行业极强仓止盈点数挂卖出停止单（默认 2 个点）。
    11. 持仓最高收益率回撤达到设定点数时，清仓。
    12. 最高收益率超过保护阈值后，回落到最低保护收益时，清仓。
    13. 当天已经清仓后，当天不再买入。
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
    sector_score_exit_threshold: float = 40.0
    # 固定常量（不进 parameters）：行业极强入场线与行业极强仓止盈点数
    sector_score_entry_threshold: float = 80.0
    sector_strong_take_profit_points: float = 2.0

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
    cleared_today: bool = False
    cleared_day: str = ""

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
        "sector_score_exit_threshold",
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
        "sector_score_exit_threshold": "行业情绪减仓线",
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
        "cleared_today",
        "cleared_day",
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
        "cleared_today": "当天已清仓",
        "cleared_day": "清仓日期",
    }

    def __init__(
        self,
        cta_engine: object,
        strategy_name: str,
        vt_symbol: str,
        setting: dict,
    ) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        # bar 决策总信号：持有 friction/sentiment，on_bar 内部 prepare + 决策
        self.bar_signal: BottomFrictionBarSignal = BottomFrictionBarSignal(self)
        # tick 保护总信号：聚合收益回撤 + 摩擦止盈优先级链
        self.tick_signal: BottomFrictionTickSignal = BottomFrictionTickSignal(self)
        # 收益回撤判定状态机：bar/tick 共用同一实例，check 后反向写 UI 变量
        self.profit_drawdown: ProfitDrawdownState = ProfitDrawdownState(self)
        self._friction_result: FrictionResult | None = None
        self._intraday_ready: bool = False
        self.daily_close_prices: list[float] = []
        self.current_day: date | None = None
        self.day_close_price: float = 0.0
        self.macd_trading_day: date | None = None
        self.avg_trading_day: date | None = None
        self.active_buy_orders: dict[str, float] = {}
        self.friction_tp_pending: bool = False
        self.profit_drawdown_pending: bool = False
        # 行业极强买入仓位：成交均价 + 累计成交量/金额（分笔成交加权）+ 我方委托号
        self.sector_strong_entry_price: float = 0.0
        self.sector_strong_filled_volume: float = 0.0
        self.sector_strong_filled_amount: float = 0.0
        self.sector_strong_orderids: set[str] = set()
        # 行业极强操作一天只做一次：最后成交日 + 当天是否已操作（按交易日刷新）
        self.sector_strong_day: str = ""
        self.sector_strong_today: bool = False
        self.cleared_today = False
        self.cleared_day = ""
        self.cleared_trading_day: date | None = None

    def on_init(self) -> None:
        """策略初始化。"""
        self.bg: BarGenerator = BarGenerator(self.on_bar)
        self.am: ArrayManager = ArrayManager()

        self.bar_signal.reset()
        self.profit_drawdown.reset()
        self._intraday_ready = False
        self._friction_result = None
        self.daily_close_prices = []
        self.current_day = None
        self.day_close_price = 0.0
        self.macd_trading_day = None
        self.avg_trading_day = None
        self.active_buy_orders.clear()
        self.friction_tp_pending = False
        self.profit_drawdown_pending = False
        self._reset_sector_strong_position()
        self.sector_strong_day = ""
        self.sector_strong_today = False
        self.avg_price = 0.0
        self.pos_avg_price = 0.0
        self.friction_entry_price = 0.0
        self.macd_line = 0.0
        self.signal_line = 0.0
        self.macd_hist = 0.0
        self.last_macd_hist = 0.0
        self.fast_slow_ma = ""
        self.macd_triple = ""
        self.cleared_today = False
        self.cleared_day = ""
        self.cleared_trading_day = None
        self.market_sentiment_score = 0.0
        self.market_declining_count = 0

        self.load_bar(60)
        self._load_cleared_state()

    def on_start(self) -> None:
        """策略启动。"""
        self.bar_signal.check_health()
        self._load_cleared_state()
        self.put_event()

    def on_stop(self) -> None:
        """策略停止。"""
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """收到 tick 数据时：送入分钟级 BarGenerator，再跑 tick 保护信号链（判定 + 执行）。

        ``bg.update_tick`` 可能同步触发 ``on_bar``（bar_signal 内含收益回撤早检查）；
        随后 ``tick_signal.on_tick`` 跑优先级链（收益回撤 → 摩擦止盈），命中即由信号层按
        ``SignalResult.executor`` 分派到 ``_submit_profit_drawdown_exit`` /
        ``_submit_friction_take_profit``，执行价 ``SignalResult.price`` 也由信号层按 tick 算好。
        策略这里只做行情维护（交易日/均价）+ 命中后的 UI 事件刷新，不再判 executor。

        收益回撤判定由 ``profit_drawdown`` 状态机（``ProfitDrawdownState``）完成，bar/tick 共用，
        每 tick 更新峰值/激活标记并回写 UI 变量。
        """
        self.bg.update_tick(tick)

        trading_day: date = tick.datetime.date()
        self._sync_cleared_trading_day(trading_day)
        if trading_day != self.avg_trading_day:
            self.avg_trading_day = trading_day
            self.avg_price = 0.0

        if tick.volume > 0 and tick.turnover > 0 and self.shares_per_lot > 0:
            self.avg_price = round(
                float(tick.turnover / (tick.volume * self.shares_per_lot)),
                2,
            )

        self.tick_signal.on_tick(tick)
        if self.tick_signal.signal_result(self.vt_symbol).type != SignalType.NONE:
            self.put_event()

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
            self._mark_cleared_today()

    def _submit_friction_take_profit(self, price: float) -> None:
        """执行器：到价卖出摩擦仓回到底仓（触发条件与执行价由信号层给出）。

        信号侧 ``FrictionTakeProfitSubSignal`` 判定触发并算好 ``price``（买一价优先，买一无效
        时回落最新价）；本执行器只做：pending 守卫、T+1 可卖量、卖出量、卖单、pending 标记、
        清仓标记；不做整手取整（保持原 ``_check_friction_take_profit`` 行为）。
        """
        if self.friction_tp_pending:
            return

        available: float = max(self.yd_pos - self.local_sell_frozen, 0)
        sell_volume: int = int(min(self.pos - self.base_size, available))
        if sell_volume <= 0:
            return

        vt_orderids: list[str] = self.sell(
            price,
            sell_volume,
            mark="摩擦仓止盈"
        )
        if vt_orderids:
            self.friction_tp_pending = True
            if sell_volume >= self.pos:
                self._mark_cleared_today()

    def _reset_sector_strong_position(self) -> None:
        """清空行业极强仓位的入场价/累计成交（仓位回到摩擦位以下时调用）。"""
        self.sector_strong_entry_price = 0.0
        self.sector_strong_filled_volume = 0.0
        self.sector_strong_filled_amount = 0.0
        self.sector_strong_orderids.clear()

    def _record_sector_strong_fill(self, trade: TradeData) -> None:
        """记录行业极强买入的成交，按成交量加权算出该仓位的成交均价，并标记当天已操作。

        ``sector_strong_day``/``sector_strong_today`` 使这套操作**一天最多做一次**：
        当天成交过之后，即使仓位又被其它规则卖回底仓，当天也不再重复买入。
        """
        self.sector_strong_filled_volume += float(trade.volume)
        self.sector_strong_filled_amount += float(trade.price) * float(trade.volume)
        if self.sector_strong_filled_volume > 0:
            self.sector_strong_entry_price = round(
                self.sector_strong_filled_amount / self.sector_strong_filled_volume,
                2,
            )
        self.sector_strong_day = trade.datetime.date().isoformat()
        self.sector_strong_today = True

    def _submit_sector_strong_stop_exit(self, price: float) -> None:
        """执行器：按 ``price`` 挂卖出停止单，卖出摩擦位以上的全部可卖持仓。

        信号侧 ``SectorStrongStopExitSubSignal`` 判定"持有行业极强仓"并算好触发价
        （成交均价 × (1+行业极强仓止盈点数/100)）。本地停止单语义：``last_price <= 触发价``
        时触发卖出（成交价为卖五价/跌停价）。每根 bar 的 ``cancel_all`` 会撤掉上一根的挂单，
        所以由信号链每根 bar 刷新一次；仓位回到摩擦位以下时信号层不再下发。
        """
        available: float = (
            max(self.yd_pos - self.local_sell_frozen, 0)
            if self.t1
            else self.pos
        )
        sell_volume: int = int(min(self.pos - self.base_size, available))
        if sell_volume <= 0:
            return

        self.sell(price, sell_volume, stop=True, mark="行业极强仓止盈")

    def on_bar(self, bar: BarData) -> None:
        """收到 1 分钟 K 线时执行（薄协调器）。

        ``bar_signal.on_bar`` 内部完成 prepare（am.update_bar/收益回撤早检查/09:30门控/MA/
        摩擦EMA/cancel_all/UI同步）+ 决策（收益回撤优先 + 6 决策优先级链），命中即由信号层按
        ``SignalResult.executor`` 分派到 ``_submit_profit_drawdown_exit`` /
        ``_set_target_position``。策略这里只做命中后的 UI 事件刷新。
        """
        self.bar_signal.on_bar(bar)
        if self.bar_signal.signal_result(self.vt_symbol).type != SignalType.NONE:
            self.put_event()

    def _read_persisted_strategy_data(self) -> dict:
        """读取本策略在 CTA 数据文件中的持久化变量。"""
        engine: object = self.cta_engine
        filename: str = str(getattr(engine, "data_filename", "") or "")
        file_data: dict | None = None
        if filename:
            loaded = load_json(filename)
            if isinstance(loaded, dict):
                file_data = loaded
                cached = getattr(engine, "strategy_data", None)
                if isinstance(cached, dict) and self.strategy_name in file_data:
                    cached[self.strategy_name] = file_data[self.strategy_name]

        if isinstance(file_data, dict):
            data = file_data.get(self.strategy_name)
            if isinstance(data, dict):
                return data

        cached = getattr(engine, "strategy_data", None)
        if isinstance(cached, dict):
            data = cached.get(self.strategy_name)
            if isinstance(data, dict):
                return data
        return {}

    def _load_cleared_state(self) -> None:
        """启动/初始化时加载清仓日期，并按当天刷新禁买标记。"""
        data: dict = self._read_persisted_strategy_data()
        cleared_day = data.get("cleared_day")
        if isinstance(cleared_day, str) and cleared_day:
            self.cleared_day = cleared_day

        self._sync_cleared_trading_day(date.today())
        if self.cleared_day:
            status: str = "当天不再买入" if self.cleared_today else "非当天，允许买入"
            self.write_log(f"已加载清仓日期{self.cleared_day}，{status}")

    def _sync_cleared_trading_day(self, trading_day: date) -> None:
        """按当前交易日刷新当天清仓标记，兼容进程重启后的持久化恢复。"""
        self.cleared_trading_day = trading_day
        self.cleared_today = self._is_cleared_on(trading_day)
        # 行业极强操作一天只做一次：跨交易日后自动解锁
        self.sector_strong_today = bool(self.sector_strong_day) and (
            self.sector_strong_day == trading_day.isoformat()
        )

    def _is_cleared_on(self, trading_day: date) -> bool:
        """判断指定交易日是否已经清仓。"""
        return bool(self.cleared_day) and self.cleared_day == trading_day.isoformat()

    def _mark_cleared_today(self) -> None:
        """标记当天已清仓，并立即写入磁盘，避免进程重启后丢失。"""
        if not self.inited:
            return

        trading_day: date = (
            self.cleared_trading_day
            or self.macd_trading_day
            or self.avg_trading_day
            or date.today()
        )
        day_text: str = trading_day.isoformat()
        newly_marked: bool = self.cleared_day != day_text
        self.cleared_day = day_text
        self.cleared_today = True
        if newly_marked:
            self.write_log("当天已清仓，当天不再买入")
        self.sync_data()

    def _prepare_intraday_macd(self, bar: BarData) -> bool:
        """切换交易日并过滤 09:30 之前的无效分钟数据。"""
        trading_day: date = bar.datetime.date()
        self._sync_cleared_trading_day(trading_day)

        if trading_day != self.macd_trading_day:
            self.macd_trading_day = trading_day
            self.bar_signal.friction_signal.reset()
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
            if self._is_cleared_on(bar.datetime.date()):
                return

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
                    # 行业极强入场：登记委托号，成交后据此记录该仓位的成交均价
                    if mark == SECTOR_STRONG_ENTRY_MARK:
                        self.sector_strong_orderids.update(vt_orderids)
        elif target_pos < self.pos:
            sell_volume: int = int(self.pos - target_pos)
            if sell_volume > 0:
                self.sell(bar.close_price, sell_volume, mark=mark)
                if target_pos == 0:
                    self._mark_cleared_today()

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
            # 行业极强入场的成交：记录成交均价，供止盈停止单算触发价
            if trade.vt_orderid in self.sector_strong_orderids:
                self._record_sector_strong_fill(trade)
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
            self.profit_drawdown.reset_on_open(
                trade.price, self.pos_avg_price, new_position
            )
        elif trade.direction == Direction.SHORT and trade.offset == Offset.CLOSE:
            if self.pos <= 0:
                self.pos_avg_price = 0.0
                self.profit_drawdown.reset_on_close()
                self._mark_cleared_today()

        if self.pos <= self.base_size:
            self.friction_entry_price = 0.0
            # 摩擦位已清空：行业极强仓不复存在，入场价与累计成交一并清零
            self._reset_sector_strong_position()

        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """停止单更新回调。"""
        pass


# 历史别名：部分测试/示例仍以 BottomFrictionMacdStrategy 引用本策略。
BottomFrictionMacdStrategy = BottomFrictionStrategy
