from typing import Any

from vnpy_ctastrategy.engine import CtaEngine
from vnpy_ctastrategy.template import CtaTemplate


class WecomEngine:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_wecom(self, msg: str) -> bool:
        self.messages.append(msg)
        return True


class MainEngine:
    def __init__(self) -> None:
        self.emails: list[tuple[str, str, str | None]] = []
        self.wecom_engine = WecomEngine()

    def send_email(
        self,
        subject: str,
        content: str,
        receiver: str | None = None,
    ) -> None:
        self.emails.append((subject, content, receiver))

    def get_engine(self, engine_name: str) -> WecomEngine | None:
        if engine_name == "wecom":
            return self.wecom_engine
        return None


class TemplateEngine:
    def __init__(self) -> None:
        self.wecom_messages: list[tuple[str, CtaTemplate]] = []

    def send_wecom(self, msg: str, strategy: CtaTemplate) -> None:
        self.wecom_messages.append((msg, strategy))


class DummyStrategy(CtaTemplate):
    def on_init(self) -> None:
        pass


def create_cta_engine(main_engine: Any) -> CtaEngine:
    engine: CtaEngine = object.__new__(CtaEngine)
    engine.main_engine = main_engine
    return engine


def test_send_email_keeps_original_channel() -> None:
    main_engine = MainEngine()
    engine = create_cta_engine(main_engine)

    engine.send_email("risk warning")

    assert main_engine.emails == [("CTA策略引擎", "risk warning", None)]
    assert not main_engine.wecom_engine.messages


def test_send_wecom_uses_wecom_engine() -> None:
    main_engine = MainEngine()
    engine = create_cta_engine(main_engine)

    class Strategy:
        strategy_name = "demo"

    engine.send_wecom("trade filled", Strategy())  # type: ignore[arg-type]

    assert main_engine.wecom_engine.messages == ["demo\ntrade filled"]
    assert not main_engine.emails


def test_template_only_sends_wecom_after_initialization() -> None:
    engine = TemplateEngine()
    strategy = DummyStrategy(engine, "demo", "000001.SZSE", {})

    strategy.send_wecom("before init")
    assert not engine.wecom_messages

    strategy.inited = True
    strategy.send_wecom("after init")

    assert engine.wecom_messages == [("after init", strategy)]
