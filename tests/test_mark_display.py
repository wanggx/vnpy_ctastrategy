from vnpy_ctastrategy.engine import CtaEngine
from vnpy_ctastrategy.ui.widget import StopOrderMonitor


def test_stop_order_monitor_displays_mark() -> None:
    """The CTA stop-order table must expose the trigger mark."""
    header = StopOrderMonitor.headers["mark"]

    assert header["display"] == "标注"
    assert header["update"] is False


def test_order_reference_carries_mark() -> None:
    """Regular orders expose their mark through vn.py's reference column."""

    class Strategy:
        strategy_name = "demo"

    reference = CtaEngine.create_order_reference(Strategy(), "放量突破")

    assert reference == "CtaStrategy_demo:放量突破"
    assert CtaEngine.get_order_mark(reference) == "放量突破"
