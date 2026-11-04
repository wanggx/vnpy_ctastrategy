from vnpy.trader.constant import Direction, Offset

from vnpy_ctastrategy import CtaTemplate, TickData, BarData, TradeData, OrderData


class StopAlgoExampleStrategy(CtaTemplate):
    """Example strategy that starts vnpy_algotrading StopAlgo."""

    author = "example"

    stop_price: float = 100.0
    order_volume: int = 1
    price_add: float = 1.0
    direction: str = "long"
    offset: str = "open"

    parameters = [
        "stop_price",
        "order_volume",
        "price_add",
        "direction",
        "offset",
    ]
    variables = ["algo_name"]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        """Constructor."""
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

        self.algo_name: str = ""

    def on_init(self) -> None:
        """Callback when strategy is inited."""
        self.write_log("策略初始化")

    def on_start(self) -> None:
        """Callback when strategy is started."""
        self.write_log("策略启动")
        self.start_stop_algo()
        self.put_event()

    def on_stop(self) -> None:
        """Callback when strategy is stopped."""
        self.write_log("策略停止")
        self.stop_stop_algo()
        self.put_event()

    def start_stop_algo(self) -> None:
        """Start the built-in StopAlgo from vnpy_algotrading."""
        if self.algo_name:
            return

        algo_engine = self.cta_engine.main_engine.get_engine("AlgoTrading")
        if not algo_engine:
            self.write_log("未找到AlgoTrading引擎，请确认已加载vnpy_algotrading")
            return

        direction: Direction = self.get_direction()
        offset: Offset = self.get_offset()

        setting: dict = {"price_add": self.price_add}

        self.algo_name = algo_engine.start_algo(
            "StopAlgo",
            self.vt_symbol,
            direction,
            offset,
            self.stop_price,
            self.order_volume,
            setting,
        )

        if self.algo_name:
            self.write_log(f"StopAlgo已启动：{self.algo_name}")
        else:
            self.write_log("StopAlgo启动失败")

    def stop_stop_algo(self) -> None:
        """Stop the running StopAlgo."""
        if not self.algo_name:
            return

        algo_engine = self.cta_engine.main_engine.get_engine("AlgoTrading")
        if algo_engine:
            algo_engine.stop_algo(self.algo_name)

        self.algo_name = ""

    def get_direction(self) -> Direction:
        """Return algo order direction from strategy parameter."""
        if self.direction.lower() == "long":
            return Direction.LONG
        elif self.direction.lower() == "short":
            return Direction.SHORT
        else:
            raise ValueError(f"Unsupported direction: {self.direction}")

    def get_offset(self) -> Offset:
        """Return algo order offset from strategy parameter."""
        value: str = self.offset.lower()

        if value == "open":
            return Offset.OPEN
        elif value == "close":
            return Offset.CLOSE
        elif value == "closetoday":
            return Offset.CLOSETODAY
        elif value == "closeyesterday":
            return Offset.CLOSEYESTERDAY
        else:
            raise ValueError(f"Unsupported offset: {self.offset}")

    def on_tick(self, tick: TickData) -> None:
        """StopAlgo receives ticks from AlgoTrading engine, not this callback."""
        pass

    def on_bar(self, bar: BarData) -> None:
        """Callback of new bar data update."""
        pass

    def on_order(self, order: OrderData) -> None:
        """Callback of order data update."""
        self.write_log(f"委托更新：{order.vt_orderid} {order.status}")

    def on_trade(self, trade: TradeData) -> None:
        """Callback of trade data update."""
        self.write_log(f"成交：{trade.vt_tradeid} {trade.price} {trade.volume}")
