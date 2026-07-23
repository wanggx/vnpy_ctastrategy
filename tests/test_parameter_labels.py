from vnpy_ctastrategy import CtaTemplate


class LabelledStrategy(CtaTemplate):
    sample_window: int = 10
    parameters = ["sample_window"]
    parameter_labels = {"sample_window": "周期"}
    variables = ["sample_value"]
    variable_labels = {"sample_value": "数值"}

    def on_init(self) -> None:
        pass


def test_parameter_labels_include_custom_and_fallback_names() -> None:
    labels = LabelledStrategy.get_class_parameter_labels()

    assert labels == {
        "t1": "t1",
        "sample_window": "周期",
    }


def test_variable_labels_merge_template_and_strategy_names() -> None:
    labels = LabelledStrategy.get_class_variable_labels()

    assert labels["pos"] == "持仓"
    assert labels["sample_value"] == "数值"
