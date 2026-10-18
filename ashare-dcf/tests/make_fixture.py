# -*- coding: utf-8 -*-
"""生成离线建表夹具。

ashare-dcf 的端到端测试都要一个现成工作簿，而建表需要 calibrate 的
返回值——后者要联网取行情和财报。把它落成 pickle，之后改 build/verify
的测试就能离线跑，不必每次都去撞东财的频控。

用法: python3 tests/make_fixture.py 600183
"""
import os, pickle, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ashare_calibrate as C
import ashare_data as D

code = D.validate_code(sys.argv[1] if len(sys.argv) > 1 else "600183")
cal = C.calibrate(code, 5)
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
os.makedirs(out, exist_ok=True)
path = os.path.join(out, f"cal_{code}.pkl")
with open(path, "wb") as f:
    pickle.dump(cal, f)
it = cal.get("interim") or {}
print(path, "interim:", it.get("period"), "share_hist:", it.get("share_hist"))
