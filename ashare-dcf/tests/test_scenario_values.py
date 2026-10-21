# -*- coding: utf-8 -*-
"""三档情景验算时一并带回每股价值。

verify_all_scenarios 原本只返回失败项，悲观 / 乐观两档的每股价值算完即丢 ——
ir-agent 的相对估值页要把 DCF 三档画进足球场图，需要这三个数。
"""
import copy, os, pickle, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_build as B
import ashare_verify as V

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "cal_600183.pkl")


@pytest.fixture(scope="module")
def wb(tmp_path_factory):
    if not os.path.exists(FIX):
        pytest.skip("缺离线夹具：python3 tests/make_fixture.py 600183")
    with open(FIX, "rb") as f:
        cal = pickle.load(f)
    g = cal["judg"]["growth"]
    cal["judg"]["growth_scen"] = {"bull": [x + 0.05 for x in g], "base": list(g),
                                  "bear": [x - 0.05 for x in g]}
    p = tmp_path_factory.mktemp("sc") / "w.xlsx"
    B.build(copy.deepcopy(cal), str(p))
    return str(p)


def test_三档每股价值一并带回(wb):
    vals = {}
    assert V.verify_all_scenarios(wb, quiet=True, values=vals) == []
    assert set(vals) == {"Bull", "Base", "Bear"}
    assert vals["Base"] == pytest.approx(V.verify(wb, quiet=True)[1]["ps"])
    assert vals["Bear"] < vals["Base"] < vals["Bull"]


def test_不传values时行为不变(wb):
    assert V.verify_all_scenarios(wb, quiet=True) == []
