import pytest
import requests

from ir_agent.sources.retry import RetryExhausted, with_retry


class TestRetry:
    def test_returns_immediately_on_success(self):
        calls = []
        assert with_retry(lambda: (calls.append(1), "ok")[1], attempts=3) == "ok"
        assert len(calls) == 1

    def test_retries_transient_timeout_then_succeeds(self):
        calls = []
        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise requests.Timeout("read timed out")
            return "ok"
        assert with_retry(flaky, attempts=3, base_delay=0) == "ok"
        assert len(calls) == 3

    def test_exhausting_attempts_raises_with_the_last_error(self):
        def always():
            raise requests.Timeout("read timed out")
        with pytest.raises(RetryExhausted, match="3 次"):
            with_retry(always, attempts=3, base_delay=0)

    def test_non_transient_error_is_not_retried(self):
        """代码不存在之类的错误重试多少次都一样，快速失败更有用。"""
        calls = []
        def bad():
            calls.append(1)
            raise ValueError("未收录该证券代码")
        with pytest.raises(ValueError):
            with_retry(bad, attempts=3, base_delay=0)
        assert len(calls) == 1

    def test_http_5xx_is_transient_but_4xx_is_not(self):
        def mk(status):
            r = requests.Response(); r.status_code = status
            return requests.HTTPError(response=r)
        from ir_agent.sources.retry import is_transient
        assert is_transient(mk(503)) is True
        assert is_transient(mk(404)) is False
