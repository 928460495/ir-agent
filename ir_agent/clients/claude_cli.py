"""通过 `claude -p` 调用模型 —— **无需 API key**。

用现有的 Claude Code 登录，是官方文档指定的「从其它语言驱动同一 agent loop」
的方式: 以子进程运行 CLI，加 `-p` 与 `--output-format json`。

计费: 2026-06-15 起 `claude -p` 的用量**不计入订阅的交互式额度池**，而是消耗
每档订阅附带的月度 Agent SDK credit（Pro $20 / Max 5x $100 / Max 20x $200，
按标准 API 费率）。响应里的 `total_cost_usd` 因此是真实开销，值得累计上报 ——
一篇研报大约 $0.05，但驳回重写与后续的多空辩论会成倍增加。

注意: 官方限制第三方产品对外提供 claude.ai 登录/额度。**自用没问题**，
若要做成产品分发给他人，须改用 API key。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import Callable

# 撰写一篇九节研报的提示词已有两万余字（可引用事实清单 + 宏观/行业/
# 产业链/战略/可比/一致预期六段摘要 + 年报摘录），实测 300s 不够用 ——
# 长电那一轮就死在这里，而它不是逻辑问题，纯粹是没给够时间。
DEFAULT_TIMEOUT = 900
Runner = Callable[[list[str], str, int], str]


class ClaudeCliError(Exception):
    """claude -p 调用失败。"""


class NotAuthenticated(ClaudeCliError):
    """登录已过期 —— 解法明确，单列一类便于调用方给出准确提示。"""


class ClaudeCliTransient(ClaudeCliError):
    """传输层故障 —— 什么都没说明，值得重试。

    与「模型写出了不合规的东西」必须分开。实测事故（600183 第四轮）:
    撰写正文时连接在模型吐字中途断开，full.py 的
    `except (AnalystRefused, ClaudeCliError)` 把它和模型被驳回一视同仁
    地吞掉，整个撰写+辩论阶段作废——而此前最贵的采集层已经花完了钱。

    `AnalystRefused` 带着信息（模型确实写了，而它不合规，且 write_body
    内部已重试三次），连接中断不带任何信息。
    """


# 判为「可重试」的错误文本白名单。
#
# **白名单而非黑名单是有意的: 兜底值是「不重试」。** 认不出来的错误按
# 确定性失败处理，代价是如实报告一次失败；反过来默认重试，会把确定性
# 的失败（提示词过长、模型拒答）伪装成偶发故障，而且每次重试都在花钱。
# 这与 research._mark 那次事故同一条教训——兜底值该选最弱的那个断言。
#
# 超时不在白名单里: 默认 900s，重试三次等于 45 分钟无声等待，而超时的
# 常见原因（提示词太长）重试也不会变。
_TRANSIENT = re.compile(
    r"connection closed|connection reset|connection error|connection aborted"
    r"|socket hang up|network error|stream (?:error|closed)|unexpected eof"
    # 实测到的两种措辞都带「响应被截断」的意思，且都以
    # 「The response above may be incomplete.」收尾:
    #   · Connection closed mid-response（第四轮，撰写正文）
    #   · Response stalled mid-stream（第五轮，多空辩论）
    # 第一次只按第一种措辞建白名单，第二种就漏了——**一个样本建的白名单
    # 只覆盖一个样本**。这里按「截断」这个共同语义收口，而不是再贴一条
    # 字面量。
    r"|mid-stream|mid-response|stalled|may be incomplete"
    r"|\b50[0234]\b|bad gateway|service unavailable|gateway timeout"
    r"|overloaded|please try again",
    re.I)


def parse_result(raw: str) -> tuple[str, float]:
    """解析 --output-format json 的输出，返回 (正文, 花费美元)。"""
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ClaudeCliError(f"claude 未返回 JSON，原始输出：{raw[:300]}") from e

    text = str(d.get("result") or "")
    if d.get("is_error"):
        if "authenticate" in text.lower() or "oauth" in text.lower():
            raise NotAuthenticated(
                f"Claude Code 登录已过期：{text}\n"
                "请在你自己的终端运行 `claude`，按提示 /login 重新登录后重试。"
            )
        if _TRANSIENT.search(text):
            raise ClaudeCliTransient(f"claude 返回错误：{text[:300]}")
        raise ClaudeCliError(f"claude 返回错误：{text[:300]}")

    if not text.strip():
        raise ClaudeCliError("claude 返回空结果。")
    return text, float(d.get("total_cost_usd") or 0.0)


def _subprocess_runner(cmd: list[str], prompt: str, timeout: int) -> str:
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True,
                           text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise ClaudeCliError(
            "找不到 claude 命令。请确认 Claude Code 已安装且在 PATH 中。") from e
    except subprocess.TimeoutExpired as e:
        raise ClaudeCliError(f"claude 调用超时（{timeout}s）。") from e
    return p.stdout or p.stderr


def make_client(
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    runner: Runner | None = None,
    tools: list[str] | None = None,
    retries: int = 2,
    backoff: float = 5.0,
    sleeper: Callable[[float], None] = time.sleep,
):
    """返回一个 `str -> str` 的 client，可直接交给 analyst.write_body。

    prompt 走 stdin 而非 argv —— 研报 prompt 有数千字符，
    走命令行参数会撞上系统的参数长度上限。

    `tools` 默认 `None`——不开任何工具，此时命令行为与不传该参数前
    完全一致。这是有意的默认值：绝大多数调用（撰写研报正文、摘要、
    打分）是纯推理，开着工具只会让模型分心去决定要不要用、并拖慢
    响应；只有明确要联网搜索（例如 sources/web.py 的开放网络采集）
    时才该显式传 `tools=["WebSearch", "WebFetch"]` 之类的列表打开。

    `--allowedTools` 在 claude CLI 里是变长参数：如果把工具名和后面
    的提示词分别作为独立 argv 项传给它，提示词会被当成额外的工具名
    吃掉（实测报错 `Input must be provided either through stdin or
    as a prompt argument`）。所以这里把工具名用逗号拼成*一个*字符串，
    作为 `--allowedTools` 后唯一的一个 argv 元素——同时本来 prompt
    也不经过 argv（见上），双重保险不会撞上这个坑。
    """
    run = runner or _subprocess_runner
    cmd = ["claude", "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]

    def client(prompt: str) -> str:
        for attempt in range(retries + 1):
            try:
                text, cost = parse_result(run(list(cmd), prompt, timeout))
            except ClaudeCliTransient as e:
                if attempt == retries:
                    raise
                client.transient_retries += 1
                wait = backoff * (attempt + 1)
                # 打到 stderr —— 实跑的日志里必须留下「这一轮重连过」，
                # 否则事后分不清一份产物是一次写成的还是重试换来的。
                print(f"传输故障，{wait:.0f}s 后重试"
                      f"（第 {attempt + 1}/{retries} 次）：{str(e)[:120]}",
                      file=sys.stderr)
                sleeper(wait)
                continue
            client.total_cost_usd += cost
            client.calls += 1
            return text
        raise AssertionError("不可达：用尽重试的那次已经 raise 了")

    client.total_cost_usd = 0.0
    client.calls = 0
    # 这一轮因传输故障重连了几次。**目前只有上面那行 stderr 和测试在读
    # 它**——实跑日志里有「传输故障，5s 后重试」就够回答「这份正文是不是
    # 重试换来的」。留这个计数器是为了让调用方能把它拼进产物摘要（研报
    # 末尾的「模型开销」那一行旁边），还没接上；在接上之前不要在别处声称
    # 产物会报告重试次数。
    #
    # 顺带记一个已知的低报: 失败那次的开销，CLI 只在它自己那份错误响应
    # 里报，而 parse_result 在那条路径上是 raise 而不是 return，所以
    # total_cost_usd 不含重试掉的那次。
    client.transient_retries = 0
    return client
