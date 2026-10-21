"""腾讯行情批量取数 —— 相对估值要一次拿到全行业几十家的市值。"""

from __future__ import annotations

from ir_agent.sources import quotes
from ir_agent.sources.quotes import fetch_quotes, parse_tencent_quotes
from tests.test_sources import TENCENT_RAW

# 真实样本（贵州茅台）换成另一个代码，拼成两家的批量响应
OTHER = TENCENT_RAW.replace("v_sh600519", "v_sz000858").replace("~600519~", "~000858~")
BATCH = TENCENT_RAW + "\n" + OTHER


def test_批量解析按代码取回():
    got = parse_tencent_quotes(BATCH)
    assert set(got) == {"600519", "000858"}
    assert got["600519"]["market_cap"] > 0


def test_单条坏数据不连累整批():
    got = parse_tencent_quotes(BATCH + '\nv_sz000001="";')
    assert "000001" not in got and len(got) == 2


class _Resp:
    def __init__(self, text):
        self.text, self.encoding = text, None

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, calls, text=""):
        self.calls, self.text = calls, text

    def get(self, url, timeout):
        self.calls.append(url)
        return _Resp(self.text)


def test_按批切分请求():
    calls: list[str] = []
    fetch_quotes([f"sh6{i:05d}" for i in range(65)], session=_Session(calls), chunk=30)
    assert len(calls) == 3
    assert calls[0].count(",") == 29 and calls[2].count(",") == 4


def test_返回每批原始文本供落快照():
    got, raws = fetch_quotes(["sh600519", "sz000858"], session=_Session([], BATCH))
    assert set(got) == {"600519", "000858"} and raws == [BATCH]


def test_单只接口不变():
    assert quotes.fetch_quote.__name__ == "fetch_quote"
