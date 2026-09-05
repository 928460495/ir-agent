"""瞬时故障重试。

只重试**瞬时**错误（超时、连接中断、5xx）。4xx 和业务错误重试多少次都一样，
快速失败更有用 —— 盲目重试会把一个明确的错误拖成一次漫长的挂起。
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

import requests

T = TypeVar("T")

_TRANSIENT = (requests.Timeout, requests.ConnectionError,
              requests.exceptions.ChunkedEncodingError)


class RetryExhausted(Exception):
    """重试次数用尽仍未成功。"""


def is_transient(e: BaseException) -> bool:
    if isinstance(e, requests.HTTPError):
        resp = getattr(e, "response", None)
        return resp is not None and 500 <= resp.status_code < 600
    return isinstance(e, _TRANSIENT)


def with_retry(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 0.8,
) -> T:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:                      # noqa: BLE001
            if not is_transient(e):
                raise
            last = e
            if i < attempts - 1:
                time.sleep(base_delay * (2 ** i))   # 指数退避
    raise RetryExhausted(
        f"重试 {attempts} 次后仍失败: {last.__class__.__name__}: {last}"
    ) from last
