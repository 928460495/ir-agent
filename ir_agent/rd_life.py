"""研发资本的摊销年限：申万行业 → 年（spec 2026-11-24-rd-capitalization §4.5）。

依据 Damodaran 的经验表（软件 / 互联网 3 年、半导体与硬件 5 年、制药 / 生物与通信设备、汽车 10 年；
美股按 SIC 映射见 `us-dcf/valuation_lib.rd_life`）。申万二级优先、一级兜底，查不到默认 5 年。
"""

from __future__ import annotations

DEFAULT = 5

_L2 = {
    "软件开发": 3, "IT服务Ⅱ": 3, "互联网电商": 3, "游戏Ⅱ": 3, "数字媒体": 3,
    "半导体": 5, "元件": 5, "光学光电子": 5, "消费电子": 5, "计算机设备": 5, "电子化学品Ⅱ": 5,
    "通信设备": 10,
    "化学制药": 10, "生物制品": 10, "中药Ⅱ": 10,
    "医疗器械": 5, "医疗服务": 5, "医药商业": 5,
}
_L1 = {"计算机": 3, "传媒": 3, "电子": 5, "通信": 5, "医药生物": 10, "汽车": 10, "国防军工": 10}


def life_for_sw(path: str) -> int:
    """「一级-二级-三级」形式的申万路径 → 摊销年限。"""
    parts = [p.strip() for p in (path or "").split("-") if p.strip()]
    if len(parts) >= 2 and parts[1] in _L2:
        return _L2[parts[1]]
    if parts and parts[0] in _L1:
        return _L1[parts[0]]
    return DEFAULT
