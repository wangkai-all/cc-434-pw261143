"""可注入的时钟，保证演示与测试的时间确定性。"""
from __future__ import annotations

from datetime import date, datetime, timezone


class Clock:
    """模拟时钟：所有持久化时间戳与观测日期都从这里取。"""

    def __init__(self, fixed: datetime | None = None) -> None:
        self._now: datetime = fixed or datetime.now(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def iso(self) -> str:
        return self._now.isoformat(timespec="seconds")

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        self._now = value

    def set_date(self, day: date, hour: int = 9) -> None:
        self._now = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)

    def advance_seconds(self, seconds: int) -> None:
        from datetime import timedelta

        self._now += timedelta(seconds=seconds)
