"""HTTP 抓取层：类型化异常、429/503 语义、退避重试。"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .model import SnapshotOutcome


class FetchError(Exception):
    """抓取失败基类，携带 outcome 语义。"""

    outcome = SnapshotOutcome.ERROR

    def __init__(self, message: str, *, http_status: int | None = None,
                 retry_after: int | None = None, body: bytes | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.retry_after = retry_after
        self.body = body or b""


class RateLimited(FetchError):
    outcome = SnapshotOutcome.RATE_LIMITED


class Maintenance(FetchError):
    outcome = SnapshotOutcome.MAINTENANCE


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes
    content_type: str
    headers: dict[str, str]
    rate_limited_attempts: list["RateLimited"] = None  # 重试成功前遇到的 429


# 维护标记：显式声明，避免把维护页误判为空结果
_MAINTENANCE_MARKERS = (
    b'data-page="maintenance"',
    b'name="page-state" content="maintenance"',
)


class Transport:
    def __init__(self, sim_date: str, *, timeout: float = 10.0,
                 sleep=time.sleep, max_retries: int = 4,
                 base_backoff_seconds: float = 0.2) -> None:
        self.sim_date = sim_date
        self.timeout = timeout
        self._sleep = sleep
        self.max_retries = max_retries
        self.base_backoff = base_backoff_seconds
        self.retry_count = 0

    def _once(self, url: str) -> FetchResult:
        req = urllib.request.Request(url, headers={
            "X-Sim-Date": self.sim_date,
            "User-Agent": "pricehunter/1.0",
            "Accept": "text/html, application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                headers = {k: v for k, v in resp.headers.items()}
                return FetchResult(url, resp.status, body,
                                   resp.headers.get_content_type(), headers)
        except urllib.error.HTTPError as exc:
            body = exc.read() or b""
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            retry_after = int(retry_after) if retry_after and retry_after.isdigit() else None
            if exc.code == 429:
                raise RateLimited(f"429 rate limited: {url}", http_status=429,
                                  retry_after=retry_after, body=body) from exc
            if exc.code == 503:
                # 硬维护：有 Retry-After，且响应体可能带维护标记
                raise Maintenance(f"503 maintenance: {url}", http_status=503,
                                  retry_after=retry_after, body=body) from exc
            raise FetchError(f"HTTP {exc.code}: {url}", http_status=exc.code,
                             body=body) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"network error: {url}: {exc}") from exc

    @staticmethod
    def detect_soft_maintenance(body: bytes) -> bool:
        return any(marker in body for marker in _MAINTENANCE_MARKERS)

    def fetch(self, url: str) -> FetchResult:
        """带退避重试的抓取。429 按 Retry-After / 指数退避重试；
        503 与软维护立即抛出 Maintenance（重试由上层按来源策略决定）。

        重试成功时，途中遇到的 429 异常挂在结果的 rate_limited_attempts 上，
        由上层落盘为限流快照，保证限流历史可追溯。
        """
        attempt = 0
        seen_429: list[RateLimited] = []
        while True:
            try:
                result = self._once(url)
                if self.detect_soft_maintenance(result.body):
                    raise Maintenance(
                        f"soft maintenance page (HTTP 200): {url}",
                        http_status=200, body=result.body,
                    )
                result.rate_limited_attempts = seen_429
                return result
            except RateLimited as exc:
                if attempt >= self.max_retries:
                    raise
                seen_429.append(exc)
                # 夹具给的 Retry-After=1；演示环境用压缩退避避免长等
                delay = self.base_backoff * (2 ** attempt)
                self.retry_count += 1
                attempt += 1
                self._sleep(delay)
            except (Maintenance, FetchError):
                raise
