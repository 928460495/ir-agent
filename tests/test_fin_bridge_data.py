"""非经营性金融资产（财务公司）—— 数据层映射（spec 2026-11-20-nonop-financial-assets，Task 1）。不联网。

茅台 2025-12-31：拆出资金 990.96 亿、买入返售 88.80 亿、一年内到期的非流动资产 268.71 亿、债权投资 11.13 亿、
吸收存款及同业存放 180.38 亿 —— 此前都没映射进账本，被「其他流动资产」倒挤吸收进营运资本。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from ir_agent import dcf_data
from ir_agent.ledger import FactLedger
from ir_agent.sources.eastmoney import CompanyType, parse_em_rows, specs_for

ROW = {"NOTICE_DATE": "2026-04-17 00:00:00", "REPORT_DATE": "2025-12-31 00:00:00",
       "MONETARYFUNDS": 51690610946.5, "LEND_FUND": 99096000000, "BUY_RESALE_FINASSET": 8880000000,
       "NONCURRENT_ASSET_1YEAR": 26871114612.71, "CREDITOR_INVEST": 1113000000, "OTHER_CREDITOR_INVEST": None,
       "ACCEPT_DEPOSIT_INTERBANK": 18038000000, "SELL_REPO_FINASSET": None,
       "TOTAL_CURRENT_ASSETS": 252519000000, "TOTAL_ASSETS": 303835000000}

NEW = {"lend_fund": "拆出资金", "buy_resale_finasset": "买入返售金融资产", "debt_investment": "债权投资",
       "other_debt_investment": "其他债权投资", "noncurrent_asset_1y": "一年内到期的非流动资产",
       "accept_deposit_interbank": "吸收存款及同业存放", "sell_repo_finasset": "卖出回购金融资产款"}


def test_东财字段映射到账本键():
    spec = specs_for(CompanyType.GENERAL)["balance"]
    facts = {f.key: f.value for f in parse_em_rows([ROW], spec, "em_g_balance_x")}
    assert facts["lend_fund"] == D("99096000000") and facts["accept_deposit_interbank"] == D("18038000000")
    assert facts["noncurrent_asset_1y"] == D("26871114612.71") and facts["buy_resale_finasset"] == D("8880000000")
    assert "other_debt_investment" not in facts and "sell_repo_finasset" not in facts     # 缺失就是缺失


def test_账本铺成报表时带上中文列():
    for k, cn in NEW.items():
        assert dcf_data.BALANCE[k] == cn
    spec = specs_for(CompanyType.GENERAL)["balance"]
    led = FactLedger()
    for f in parse_em_rows([ROW], spec, "em_g_balance_x"):
        led.put(f)
    bal = {}
    for key, cn in dcf_data.BALANCE.items():
        try:
            bal[cn] = float(led.get(key, "2025FY", as_of=date(2026, 11, 20)).value)
        except KeyError:
            pass
    assert bal["拆出资金"] == 99096000000 and bal["吸收存款及同业存放"] == 18038000000


def test_财务公司科目不触发金融企业判定():
    """吸收存款及同业存放进了报表后，FIN_BS_MARKERS 的「吸收存款」会命中 —— 按占总资产比重判定，茅台 5.9% 不应拒绝。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ashare-dcf"))
    import pandas as pd
    import ashare_data as AD
    bal = pd.DataFrame({"资产总计": [3038.35e8], "吸收存款及同业存放": [180.38e8], "拆出资金": [990.96e8]},
                       index=["20251231"])
    AD.assert_non_financial(bal, "20251231")
