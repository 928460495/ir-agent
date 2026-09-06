"""通过 claude -p 调用模型 —— 无需 API key。

用现有的 Claude Code 登录（CLAUDE_CODE_OAUTH_TOKEN），是官方文档指定的
「从其它语言驱动同一 agent loop」的方式: 以子进程运行 CLI，-p 加
--output-format json。

计费说明: 2026-06-15 起 claude -p 的用量不计入订阅的交互式额度池，
而是消耗每档订阅附带的月度 Agent SDK credit（按标准 API 费率）。
响应里的 total_cost_usd 因此值得记录 —— 它是真实开销。
"""
import json

import pytest

from ir_agent.clients.claude_cli import (
    ClaudeCliError,
    NotAuthenticated,
    make_client,
    parse_result,
)


def payload(**kw):
    base = {"type": "result", "subtype": "success", "is_error": False,
            "result": "毛利率是营业收入减营业成本后占营业收入的比例。",
            "total_cost_usd": 0.0123, "session_id": "abc"}
    base.update(kw)
    return json.dumps(base)


class TestParseResult:
    def test_returns_the_text(self):
        assert "毛利率" in parse_result(payload())[0]

    def test_returns_the_cost(self):
        assert parse_result(payload())[1] == 0.0123

    def test_expired_oauth_raises_a_specific_error(self):
        """认证过期是最常见的失败，且解法明确 —— 不该混在通用错误里。"""
        p = payload(is_error=True,
                    result="Failed to authenticate: OAuth session expired "
                           "and could not be refreshed")
        with pytest.raises(NotAuthenticated, match="claude"):
            parse_result(p)

    def test_other_errors_raise_the_generic_error(self):
        with pytest.raises(ClaudeCliError, match="rate"):
            parse_result(payload(is_error=True, result="rate limit exceeded"))

    def test_non_json_output_raises_with_the_raw_text(self):
        with pytest.raises(ClaudeCliError, match="command not found"):
            parse_result("zsh: command not found: claude")

    def test_empty_result_is_an_error(self):
        with pytest.raises(ClaudeCliError):
            parse_result(payload(result=""))


class TestMakeClient:
    def _runner(self, out, record=None):
        def run(cmd, prompt, timeout):
            if record is not None:
                record.update(cmd=cmd, prompt=prompt, timeout=timeout)
            return out
        return run

    def test_client_returns_only_the_text(self):
        c = make_client(runner=self._runner(payload()))
        assert "毛利率" in c("问题")

    def test_prompt_is_passed_on_stdin_not_argv(self):
        """研报 prompt 有数千字符，走 argv 会撞上系统参数长度上限。"""
        rec = {}
        make_client(runner=self._runner(payload(), rec))("很长的 prompt")
        assert rec["prompt"] == "很长的 prompt"
        assert "很长的 prompt" not in " ".join(rec["cmd"])

    def test_json_output_format_is_requested(self):
        rec = {}
        make_client(runner=self._runner(payload(), rec))("q")
        assert "--output-format" in rec["cmd"] and "json" in rec["cmd"]

    def test_model_can_be_pinned(self):
        rec = {}
        make_client(model="claude-opus-5", runner=self._runner(payload(), rec))("q")
        assert "--model" in rec["cmd"]
        assert "claude-opus-5" in rec["cmd"]

    def test_costs_accumulate_across_calls(self):
        c = make_client(runner=self._runner(payload()))
        c("a"); c("b")
        assert c.total_cost_usd == pytest.approx(0.0246)

    def test_call_count_is_tracked(self):
        c = make_client(runner=self._runner(payload()))
        c("a"); c("b"); c("c")
        assert c.calls == 3
