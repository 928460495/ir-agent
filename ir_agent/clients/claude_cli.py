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
import subprocess
from typing import Callable

DEFAULT_TIMEOUT = 300
Runner = Callable[[list[str], str, int], str]


class ClaudeCliError(Exception):
    """claude -p 调用失败。"""


class NotAuthenticated(ClaudeCliError):
    """登录已过期 —— 解法明确，单列一类便于调用方给出准确提示。"""


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
):
    """返回一个 `str -> str` 的 client，可直接交给 analyst.write_body。

    prompt 走 stdin 而非 argv —— 研报 prompt 有数千字符，
    走命令行参数会撞上系统的参数长度上限。
    """
    run = runner or _subprocess_runner
    cmd = ["claude", "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]

    def client(prompt: str) -> str:
        text, cost = parse_result(run(list(cmd), prompt, timeout))
        client.total_cost_usd += cost
        client.calls += 1
        return text

    client.total_cost_usd = 0.0
    client.calls = 0
    return client
