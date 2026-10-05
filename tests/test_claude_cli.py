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
    ClaudeCliTimeout,
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

    def test_no_tools_by_default(self):
        """默认不开任何工具 —— 绝大多数调用是纯推理，开着工具只会让模型
        分心并拖慢，联网只应在明确要搜索时开启。这条测试钉住「不传
        tools 时命令行为完全不变」这个向后兼容承诺。"""
        rec = {}
        make_client(runner=self._runner(payload(), rec))("q")
        assert "--allowedTools" not in rec["cmd"]

    def test_tools_are_comma_joined_not_separate_argv_items(self):
        """实测踩过的坑：`--allowedTools` 是变长参数，若把多个工具名和
        提示词分别作为独立 argv 项传给它（`--allowedTools WebSearch
        WebFetch "提示词"`），提示词会被当成第三个工具名吃掉，claude
        CLI 报 `Input must be provided either through stdin or as a
        prompt argument`。必须把工具名逗号拼成一个字符串传一个 argv
        位置：`--allowedTools "WebSearch,WebFetch"`。"""
        rec = {}
        make_client(
            tools=["WebSearch", "WebFetch"],
            runner=self._runner(payload(), rec),
        )("q")
        cmd = rec["cmd"]
        assert "--allowedTools" in cmd
        i = cmd.index("--allowedTools")
        # 工具名必须是紧跟在 --allowedTools 后面的单个 argv 元素，
        # 而不是被拆成 "WebSearch", "WebFetch" 两个独立元素
        # （拆开就会把后面无意中拼上的内容一起吃掉）。
        assert cmd[i + 1] == "WebSearch,WebFetch"

    def test_prompt_still_goes_via_stdin_when_tools_are_enabled(self):
        """开工具不应该改变 prompt 的传递方式 —— prompt 依旧只通过
        runner 的 prompt 参数走 stdin，不出现在 cmd（argv）里，这样
        才不会撞上「陷阱」里提示词被当成工具名吃掉的问题。"""
        rec = {}
        make_client(
            tools=["WebSearch"],
            runner=self._runner(payload(), rec),
        )("搜索这个关键词")
        assert rec["prompt"] == "搜索这个关键词"
        assert "搜索这个关键词" not in " ".join(rec["cmd"])


# ── 传输故障与内容故障是两种失败，不该同一个待遇 ──────────────────
#
# 真实事故（600183 第四轮实跑）: 撰写正文时连接在模型吐字中途断开
# （`API Error: Connection closed mid-response`），full.py 的
# `except (AnalystRefused, ClaudeCliError)` 把它和「模型写出了不合规的
# 正文」一视同仁地吞掉，整个撰写+辩论阶段作废。而此前最贵的采集层
# （年报 PDF、公司公告、开放网络、因子检索）已经花完了钱——$7.38 一轮，
# 死在一次网络抖动上。
#
# 两者的信息量完全不同:
#   · AnalystRefused —— 模型确实写出了东西，而它不合规；write_body 内部
#     已经重试过三次，再试还是同一个毛病，放弃是对的
#   · 连接中断 —— 什么都没说明，重试一次就可能过
#
# 白名单（而非黑名单）是有意的: 兜底值是「不重试」。认不出来的错误按
# 不可重试处理，最多是如实报告一次失败；反过来默认重试会把确定性的
# 失败伪装成偶发故障，并且每次重试都在花钱。
class TestTransientClassification:
    def test_连接中途断开被认成传输故障(self):
        from ir_agent.clients.claude_cli import ClaudeCliTransient
        with pytest.raises(ClaudeCliTransient):
            parse_result(payload(
                is_error=True,
                result="API Error: Connection closed mid-response. "
                       "The response above may be incomplete."))

    def test_断流的第二种措辞也被认成传输故障(self):
        """第一次按「Connection closed mid-response」一条措辞建白名单，
        第五轮实跑换成「Response stalled mid-stream」就漏了——重试没触发，
        已经写好的正文被丢掉。一个样本建的白名单只覆盖一个样本。"""
        from ir_agent.clients.claude_cli import ClaudeCliTransient
        with pytest.raises(ClaudeCliTransient):
            parse_result(payload(
                is_error=True,
                result="API Error: Response stalled mid-stream. "
                       "The response above may be incomplete."))

    def test_服务端五开头的错误被认成传输故障(self):
        from ir_agent.clients.claude_cli import ClaudeCliTransient
        with pytest.raises(ClaudeCliTransient):
            parse_result(payload(is_error=True,
                                 result="API Error: 503 Service Unavailable"))

    def test_登录过期不是传输故障(self):
        """重试一百次也还是过期 —— 它有自己的解法，不该被重试掩盖。"""
        from ir_agent.clients.claude_cli import ClaudeCliTransient
        with pytest.raises(NotAuthenticated) as e:
            parse_result(payload(is_error=True,
                                 result="Failed to authenticate: OAuth "
                                        "session expired"))
        assert not isinstance(e.value, ClaudeCliTransient)

    def test_认不出来的错误不算传输故障(self):
        """兜底是「不重试」—— 白名单之外一律按确定性失败处理。"""
        from ir_agent.clients.claude_cli import ClaudeCliTransient
        with pytest.raises(ClaudeCliError) as e:
            parse_result(payload(is_error=True,
                                 result="Prompt is too long: 312000 tokens"))
        assert not isinstance(e.value, ClaudeCliTransient)


class TestTransientRetry:
    def _flaky(self, outs, rec):
        """前几次返回故障载荷，之后返回成功载荷。"""
        def run(cmd, prompt, timeout):
            rec.setdefault("calls", 0)
            rec["calls"] += 1
            return outs[min(rec["calls"] - 1, len(outs) - 1)]
        return run

    def _drop(self):
        return payload(is_error=True,
                       result="API Error: Connection closed mid-response.")

    def test_一次连接中断之后重试成功(self):
        rec = {}
        c = make_client(runner=self._flaky([self._drop(), payload()], rec),
                        sleeper=lambda s: None)
        assert "毛利率" in c("问题")
        assert rec["calls"] == 2

    def test_重试成功的那次只算一次调用一份开销(self):
        """失败那次没拿到正文，不该被计成一次成功调用。"""
        rec = {}
        c = make_client(runner=self._flaky([self._drop(), payload()], rec),
                        sleeper=lambda s: None)
        c("问题")
        assert c.calls == 1
        assert c.total_cost_usd == pytest.approx(0.0123)

    def test_重试次数记在计数器上(self):
        """一轮下来重试了几次，要能在产物里如实报告。"""
        rec = {}
        c = make_client(runner=self._flaky([self._drop(), payload()], rec),
                        sleeper=lambda s: None)
        c("问题")
        assert c.transient_retries == 1

    def test_一直断就在用尽重试后抛出(self):
        from ir_agent.clients.claude_cli import ClaudeCliTransient
        rec = {}
        c = make_client(runner=self._flaky([self._drop()], rec),
                        retries=2, sleeper=lambda s: None)
        with pytest.raises(ClaudeCliTransient):
            c("问题")
        assert rec["calls"] == 3, "默认 2 次重试 = 总共 3 次尝试"

    def test_非传输故障一次都不重试(self):
        rec = {}
        c = make_client(
            runner=self._flaky([payload(is_error=True,
                                        result="Prompt is too long")], rec),
            sleeper=lambda s: None)
        with pytest.raises(ClaudeCliError):
            c("问题")
        assert rec["calls"] == 1

    def test_登录过期一次都不重试(self):
        rec = {}
        c = make_client(
            runner=self._flaky([payload(
                is_error=True,
                result="Failed to authenticate: OAuth session expired")], rec),
            sleeper=lambda s: None)
        with pytest.raises(NotAuthenticated):
            c("问题")
        assert rec["calls"] == 1

    def test_退避时间逐次拉长(self):
        """连接刚断就立刻重连，很可能撞上同一个还没恢复的故障。"""
        rec, slept = {}, []
        c = make_client(runner=self._flaky([self._drop()], rec),
                        retries=2, backoff=5.0, sleeper=slept.append)
        with pytest.raises(ClaudeCliError):
            c("问题")
        assert slept == [5.0, 10.0]

    def test_重试默认不真的睡过去由调用方注入(self):
        """这条钉的是可测性: sleeper 必须可注入，否则重试测试要么真睡、
        要么得去 monkeypatch time.sleep。"""
        import inspect
        assert "sleeper" in inspect.signature(make_client).parameters


# ── 超时：按「输入受限」还是「输出受限」区分，不按一条规则 ──────────
#
# **实测（生益科技 600183 全流程，2026-10-06）。** 第一阶段只差最后一步，
# 判断项那次调用 900s 超时，整轮 $11.09 作废 —— brief 与 debate 都已落盘且
# 5 项挑战全部回写，但没有判断项就进不了第二阶段，而流水线没有续跑点。
#
# 超时原先被刻意排除在可重试白名单外，理由是「超时的常见原因（提示词太长）
# 重试也不会变」。这条推理对**撰写**成立（提示词两万余字，长度固定），对
# **判断项**不成立: 它的瓶颈在输出 —— 要为 7~8 个因子 × 5 年 × 3 档生成几十
# 行结构化文本，是全链路输出最长的一次调用，而生成速度是会波动的。
#
# 同一条规则套在两种不同瓶颈的调用上，这是当初的错。因此改为**由调用方
# 声明**: 输出受限的调用显式开启 `retry_timeout`，其余保持原样（重试超时
# 会带来 45 分钟静默等待，这个代价是真的）。
class TestTimeoutPolicy:
    def _timeout_runner(self, rec, then=None):
        def run(cmd, prompt, timeout):
            rec["calls"] = rec.get("calls", 0) + 1
            if then is not None and rec["calls"] > 1:
                return then
            raise ClaudeCliTimeout(f"claude 调用超时（{timeout}s）。")
        return run

    def test_超时是独立一类而不是传输故障(self):
        """按**异常类型**区分，不按错误文本匹配 —— 文本会变，类型不会。"""
        from ir_agent.clients.claude_cli import (ClaudeCliTimeout,
                                                 ClaudeCliTransient)
        assert issubclass(ClaudeCliTimeout, ClaudeCliError)
        assert not issubclass(ClaudeCliTimeout, ClaudeCliTransient)

    def test_默认不重试超时(self):
        """45 分钟静默等待是真代价，默认不付。"""
        rec = {}
        c = make_client(runner=self._timeout_runner(rec), sleeper=lambda s: None)
        with pytest.raises(ClaudeCliTimeout):
            c("q")
        assert rec["calls"] == 1

    def test_调用方可以显式开启超时重试(self):
        rec = {}
        c = make_client(runner=self._timeout_runner(rec, then=payload()),
                        retry_timeout=True, sleeper=lambda s: None)
        assert "毛利率" in c("q")
        assert rec["calls"] == 2

    def test_开启后仍然受重试次数限制(self):
        rec = {}
        c = make_client(runner=self._timeout_runner(rec), retry_timeout=True,
                        retries=1, sleeper=lambda s: None)
        with pytest.raises(ClaudeCliTimeout):
            c("q")
        assert rec["calls"] == 2

    def test_开启超时重试不影响内容类错误(self):
        """提示词过长这类确定性失败仍然一次都不重试。"""
        rec = {}

        def run(cmd, prompt, timeout):
            rec["calls"] = rec.get("calls", 0) + 1
            return payload(is_error=True, result="Prompt is too long")

        c = make_client(runner=run, retry_timeout=True, sleeper=lambda s: None)
        with pytest.raises(ClaudeCliError):
            c("q")
        assert rec["calls"] == 1
