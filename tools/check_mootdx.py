#!/usr/bin/env python3
"""复检 mootdx / 通达信协议源是否可用。

2026-09-05（周六）实测结论: **不可用**，且即使可用也非腾讯的等价替代。
  · 38 台内置服务器全部无法返回行情（get_security_count 正常，说明协议通、
    是数据侧被拒），周末因素无法完全排除；
  · get_security_quotes 不含市值/PE/PB，市值须再发一次 get_finance_info
    取总股本自行相乘 —— 多一次请求、多一个失败点。

交易日跑一次本脚本即可重新判断。退出码 0 表示可用。

    python tools/check_mootdx.py
"""
from __future__ import annotations

import sys

NEEDED = ("price", "last_close", "open", "high", "low", "vol", "amount")


def main() -> int:
    try:
        from mootdx.consts import CONFIG
        from tdxpy.hq import TdxHq_API
    except ImportError:
        print("未安装 mootdx。  pip install mootdx")
        return 2

    servers = CONFIG.get("SERVER", {}).get("HQ") or []
    print(f"内置服务器 {len(servers)} 台，逐台测试行情返回…\n")

    reachable = usable = 0
    sample = None
    for name, ip, port in servers:
        api = TdxHq_API()
        try:
            api.connect(ip, int(port), time_out=3)
            if api.get_security_count(1):
                reachable += 1
            q = api.get_security_quotes([(1, "600519")])
            if q and q[0].get("price"):
                usable += 1
                sample = sample or (name, ip, q[0])
                print(f"  ✓ {name:18} {ip:16} price={q[0]['price']}")
            api.disconnect()
        except Exception:
            pass

    print(f"\n协议可达 {reachable}/{len(servers)} · 能返回行情 {usable}/{len(servers)}")

    if not usable:
        print("\n结论: 仍不可用。继续使用腾讯(Tier-1)/新浪(Tier-2)。")
        return 1

    name, ip, q = sample
    missing = [f for f in NEEDED if f not in q]
    print(f"\n样本字段齐全: {not missing}" + (f"，缺 {missing}" if missing else ""))
    print("市值: 协议不提供，须另调 get_finance_info 取 zongguben 相乘。")
    print("\n结论: 可用。接入前请先决定市值的取法。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
