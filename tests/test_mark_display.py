from vnpy.trader.constant import Exchange
from vnpy.trader.object import OrderData
from vnpy.trader.ui.widget import OrderMonitor

from vnpy_ctastrategy.engine import CtaEngine
from vnpy_ctastrategy.template import CtaTemplate
from vnpy_ctastrategy.ui.widget import StopOrderMonitor


def test_stop_order_monitor_displays_mark() -> None:
    """The CTA stop-order table must expose the trigger mark."""
    header = StopOrderMonitor.headers["mark"]

    assert header["display"] == "标注"
    assert header["update"] is False


def test_order_monitor_displays_mark() -> None:
    """The order table must expose the trigger mark."""
    header = OrderMonitor.headers["mark"]

    assert header["display"] == "标注"
    assert header["update"] is False


def test_order_reference_carries_strategy_name() -> None:
    """The order reference only carries the strategy name, without any mark."""

    class Strategy:
        strategy_name = "demo"

    assert CtaEngine.create_order_reference(Strategy()) == "CtaStrategy_demo"


def test_order_mark_carried_by_field() -> None:
    """The trigger mark is carried by the dedicated mark field."""
    order = OrderData(
        gateway_name="test",
        symbol="000001",
        exchange=Exchange.SZSE,
        orderid="1",
        reference="CtaStrategy_demo",
        mark="放量突破",
    )

    assert order.mark == "放量突破"
    assert CtaTemplate.get_order_mark(order) == "放量突破"
