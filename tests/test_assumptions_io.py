"""读取人工填好的 assumptions.yaml。

解析层的职责是**把填写错误变成清楚的报错**，而不是让它悄悄穿到估值里:
留空、日期格式错、依据类型不认识 —— 每一种都要指名道姓地说出哪一项有问题。

未填完的模板必须报错而非报「已审核」。审核门的价值全在这里。
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ir_agent.assumptions_io import AssumptionFileError, load_assumptions

D = Decimal

FILLED = """
wacc: 0.09
wacc_basis:
  - kind: external
    url: https://yield.chinabond.com.cn/
    quote: 十年期国债到期收益率 2.50%
    retrieved: 2026-06-01
terminal_growth: 0.025
terminal_growth_basis:
  - kind: external
    url: https://stats.gov.cn/
    quote: 名义 GDP 增速约 5%
    retrieved: 2026-06-01
growth_rates:
  - year: 1
    rate: 0.03
  - year: 2
    rate: 0.04
growth_rates_basis:
  - kind: fact
    ref: "[[revenue.yoy@2025FY]]"
"""


def write(tmp_path, text) -> Path:
    p = tmp_path / "a.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class TestLoad:
    def test_reads_the_values(self, tmp_path):
        a = load_assumptions(write(tmp_path, FILLED))
        assert a.wacc == D("0.09") and a.terminal_growth == D("0.025")

    def test_growth_path_is_ordered_by_year(self, tmp_path):
        a = load_assumptions(write(tmp_path, FILLED))
        assert a.growth_rates == [D("0.03"), D("0.04")]

    def test_values_are_decimal_not_float(self, tmp_path):
        """YAML 会把 0.09 解析成 float —— 直接用会把浮点误差带进估值。"""
        a = load_assumptions(write(tmp_path, FILLED))
        assert isinstance(a.wacc, D)

    def test_external_basis_is_reconstructed(self, tmp_path):
        from ir_agent.valuation.basis import BasisKind
        a = load_assumptions(write(tmp_path, FILLED))
        b = a.basis["wacc"][0]
        assert b.kind is BasisKind.EXTERNAL and b.retrieved == date(2026, 6, 1)

    def test_fact_basis_is_reconstructed(self, tmp_path):
        from ir_agent.valuation.basis import BasisKind
        a = load_assumptions(write(tmp_path, FILLED))
        assert a.basis["growth_rates"][0].kind is BasisKind.FACT

    def test_loaded_assumptions_are_not_approved(self, tmp_path):
        """读文件不等于审核通过 —— 审核是带署名的独立动作。"""
        assert load_assumptions(write(tmp_path, FILLED)).approved is False


class TestFillErrorsAreNamed:
    def test_unfilled_template_is_refused(self, tmp_path):
        from ir_agent.research import assumption_template
        with pytest.raises(AssumptionFileError, match="wacc"):
            load_assumptions(write(tmp_path, assumption_template(3)))

    def test_missing_value_names_the_key(self, tmp_path):
        text = FILLED.replace("terminal_growth: 0.025", "terminal_growth: null")
        with pytest.raises(AssumptionFileError, match="terminal_growth"):
            load_assumptions(write(tmp_path, text))

    def test_blank_url_is_refused(self, tmp_path):
        text = FILLED.replace("url: https://yield.chinabond.com.cn/", 'url: ""')
        with pytest.raises(AssumptionFileError, match="URL"):
            load_assumptions(write(tmp_path, text))

    def test_blank_retrieved_date_is_refused(self, tmp_path):
        text = FILLED.replace("retrieved: 2026-06-01", 'retrieved: ""', 1)
        with pytest.raises(AssumptionFileError, match="抓取日期|日期"):
            load_assumptions(write(tmp_path, text))

    def test_unknown_basis_kind_is_refused(self, tmp_path):
        text = FILLED.replace("kind: fact", "kind: 随便")
        with pytest.raises(AssumptionFileError, match="随便"):
            load_assumptions(write(tmp_path, text))

    def test_growth_rate_left_blank_is_refused(self, tmp_path):
        text = FILLED.replace("rate: 0.04", "rate: null")
        with pytest.raises(AssumptionFileError, match="第 2 年|growth"):
            load_assumptions(write(tmp_path, text))

    def test_missing_file_says_so(self, tmp_path):
        with pytest.raises(AssumptionFileError, match="找不到"):
            load_assumptions(tmp_path / "nope.yaml")

    def test_malformed_yaml_says_so(self, tmp_path):
        with pytest.raises(AssumptionFileError, match="解析"):
            load_assumptions(write(tmp_path, "wacc: [unclosed"))
