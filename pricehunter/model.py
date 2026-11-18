"""领域模型与枚举。"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    OK = "ok"                      # 全部页面正常
    PARTIAL = "partial"            # 有重试成功 / 单页解析失败等降级情况
    MAINTENANCE = "maintenance_blocked"  # 命中维护页，采集中止（不是空结果）
    FAILED = "failed"


class SnapshotOutcome(str, enum.Enum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"  # 收到 429
    MAINTENANCE = "maintenance"    # 503 或 200 软维护页
    ERROR = "error"                # 其他抓取错误
    EMPTY = "empty"                # 合法的真空页（分页结束标志）


class ErrorKind(str, enum.Enum):
    STRUCTURE_CHANGED = "structure_changed"  # 页面结构变化，当前解析器无法识别
    PARSE_ERROR = "parse_error"
    FIELD_MISSING = "field_missing"


class VersionState(str, enum.Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


class DerivedState(str, enum.Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"  # 历史修订后被重算替换


@dataclass
class ParsedItem:
    """解析器输出的单条商品记录。"""

    sku: str
    name: str
    price_cents: int
    currency: str = "USD"
    list_price_cents: int | None = None
    promo_code: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ParseResult:
    items: list[ParsedItem]
    next_url: str | None
