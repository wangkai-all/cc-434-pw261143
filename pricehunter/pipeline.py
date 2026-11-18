"""采集主流程：分页、重试、维护页识别、快照、解析落库与规则重算。"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

from .model import (
    ErrorKind,
    RunStatus,
    SnapshotOutcome,
)
from .parsers import StructureChanged, get_parser
from .revision import upsert_price
from .rules import maintenance_alert, recompute_for_sku
from .storage import Storage, hash_body, new_id
from .transport import FetchError, Maintenance, RateLimited, Transport

MAX_PAGES = 20


@dataclass
class RunReport:
    run_id: str
    source_id: str
    status: RunStatus
    pages_ok: int = 0
    pages_failed: int = 0
    rate_limited: int = 0
    retries: int = 0
    items_ingested: int = 0
    note: str | None = None
    failures: list[dict] = field(default_factory=list)


class Collector:
    def __init__(self, storage: Storage, *, clock) -> None:
        self.storage = storage
        self.clock = clock

    # ---------- 内部工具 ----------
    def _persist_snapshot(self, *, run_id: str, source_id: str, url: str, page_no: int,
                          outcome: SnapshotOutcome, status: int | None, body: bytes,
                          retry_no: int) -> str:
        snap_id = new_id("snap")
        self.storage.save_snapshot({
            "snapshot_id": snap_id, "run_id": run_id, "source_id": source_id,
            "url": url, "page_no": page_no, "http_status": status,
            "outcome": outcome.value, "content_hash": hash_body(body),
            "fetched_at": self.clock.iso(), "retry_no": retry_no,
        }, body)
        return snap_id

    def _record_failure(self, *, snapshot_id: str, source_id: str, parser_key: str,
                        parser_version: str, body: bytes, error: StructureChanged) -> str:
        attempt_id = new_id("pa")
        sample_path = self.storage.save_failure_sample(attempt_id, body)
        self.storage.record_parse_attempt({
            "attempt_id": attempt_id, "snapshot_id": snapshot_id,
            "source_id": source_id, "parser_key": parser_key,
            "parser_version": parser_version, "ok": 0, "item_count": 0,
            "error_kind": error.kind.value, "error_message": str(error),
            "sample_path": sample_path, "parsed_at": self.clock.iso(),
        })
        return attempt_id

    # ---------- 主入口 ----------
    def run_source(self, spec, *, run_id: str | None = None) -> RunReport:
        """采集单个来源。不同来源使用独立 run 与独立 Transport，互不影响。"""
        run_id = run_id or new_id("run")
        self.storage.start_run(run_id, spec.source_id, self.clock.iso())
        report = RunReport(run_id=run_id, source_id=spec.source_id,
                           status=RunStatus.OK)
        transport = Transport(self.clock.today().isoformat())
        parser = get_parser(spec.parser_key, spec.parser_version)
        observed_date = self.clock.today().isoformat()
        affected: set[str] = set()

        url = urljoin(spec.base_url, spec.start_path)
        page_no = 0
        try:
            while url and page_no < MAX_PAGES:
                page_no += 1
                try:
                    result = transport.fetch(url)
                except Maintenance as exc:
                    # 维护（503 或 200 软维护）：保存快照，明确标记，绝不当空结果
                    body = exc.body or b""
                    self._persist_snapshot(
                        run_id=run_id, source_id=spec.source_id, url=url, page_no=page_no,
                        outcome=SnapshotOutcome.MAINTENANCE, status=exc.http_status,
                        body=body, retry_no=0)
                    maintenance_alert(
                        self.storage, spec.source_id, observed_date,
                        f"维护页拦截（HTTP {exc.http_status}），采集中止: {url}",
                        self.clock.iso())
                    report.status = RunStatus.MAINTENANCE
                    report.note = f"maintenance at page {page_no}"
                    return self._finish(run_id, report, transport)
                except RateLimited as exc:
                    # 重试耗尽：保存限流快照并置失败，而不是静默当空页
                    self._persist_snapshot(
                        run_id=run_id, source_id=spec.source_id, url=url, page_no=page_no,
                        outcome=SnapshotOutcome.RATE_LIMITED, status=429,
                        body=exc.body, retry_no=transport.retry_count)
                    report.rate_limited += 1
                    report.pages_failed += 1
                    report.failures.append({"page": page_no, "kind": "rate_limited",
                                            "message": str(exc)})
                    report.status = RunStatus.FAILED
                    report.note = f"rate limit exhausted at page {page_no}"
                    return self._finish(run_id, report, transport)
                except FetchError as exc:
                    self._persist_snapshot(
                        run_id=run_id, source_id=spec.source_id, url=url, page_no=page_no,
                        outcome=SnapshotOutcome.ERROR, status=exc.http_status,
                        body=exc.body, retry_no=0)
                    report.pages_failed += 1
                    report.failures.append({"page": page_no, "kind": "fetch_error",
                                            "message": str(exc)})
                    report.status = RunStatus.FAILED
                    report.note = f"fetch error at page {page_no}"
                    return self._finish(run_id, report, transport)

                # 200 正常响应：先存原始快照（不可变证据）
                # 重试成功前遇到的 429 也单独落盘，保留限流历史
                for idx, rl_exc in enumerate(result.rate_limited_attempts or (), start=1):
                    self._persist_snapshot(
                        run_id=run_id, source_id=spec.source_id, url=url,
                        page_no=page_no, outcome=SnapshotOutcome.RATE_LIMITED,
                        status=429, body=rl_exc.body, retry_no=idx)
                    report.rate_limited += 1
                snap_id = self._persist_snapshot(
                    run_id=run_id, source_id=spec.source_id, url=url, page_no=page_no,
                    outcome=SnapshotOutcome.OK, status=result.status,
                    body=result.body, retry_no=transport.retry_count)

                try:
                    parsed = parser.parse(result.body)
                except StructureChanged as exc:
                    self._record_failure(
                        snapshot_id=snap_id, source_id=spec.source_id,
                        parser_key=spec.parser_key, parser_version=spec.parser_version,
                        body=result.body, error=exc)
                    report.pages_failed += 1
                    report.failures.append({"page": page_no,
                                            "kind": ErrorKind.STRUCTURE_CHANGED.value,
                                            "message": str(exc),
                                            "snapshot_id": snap_id})
                    report.status = RunStatus.PARTIAL
                    report.note = f"structure changed at page {page_no}"
                    return self._finish(run_id, report, transport)

                self.storage.record_parse_attempt({
                    "attempt_id": new_id("pa"), "snapshot_id": snap_id,
                    "source_id": spec.source_id, "parser_key": spec.parser_key,
                    "parser_version": spec.parser_version, "ok": 1,
                    "item_count": len(parsed.items), "error_kind": None,
                    "error_message": None, "sample_path": None,
                    "parsed_at": self.clock.iso(),
                })
                report.pages_ok += 1

                if not parsed.items:
                    # 合法空页：分页终止。维护页在 fetch 阶段已被拦截，不会走到这里。
                    break

                for item in parsed.items:
                    vid, _created = upsert_price(
                        self.storage, source_id=spec.source_id, sku=item.sku,
                        name=item.name, observed_date=observed_date,
                        price_cents=item.price_cents,
                        list_price_cents=item.list_price_cents,
                        currency=item.currency, promo_code=item.promo_code,
                        parser_version=spec.parser_version, snapshot_id=snap_id,
                        now_iso=self.clock.iso())
                    report.items_ingested += 1
                    affected.add(item.sku)

                if parsed.next_url is None:
                    break
                url = urljoin(spec.base_url, parsed.next_url)

            # 规则重算（仅影响本来源、本批次 SKU）
            for sku in sorted(affected):
                recompute_for_sku(
                    self.storage, spec.source_id, sku, observed_date,
                    threshold=spec.promo_threshold, z_limit=spec.anomaly_z,
                    now_iso=self.clock.iso())

            if report.status == RunStatus.OK and transport.retry_count > 0:
                report.status = RunStatus.PARTIAL  # 有过 429 但重试成功
            return self._finish(run_id, report, transport)
        except Exception as exc:  # 防御：任何意外都要闭合 run
            report.status = RunStatus.FAILED
            report.note = f"unexpected: {exc!r}"
            report.pages_failed += 1
            return self._finish(run_id, report, transport)

    def _finish(self, run_id: str, report: RunReport, transport: Transport) -> RunReport:
        report.retries = transport.retry_count
        report.rate_limited = max(report.rate_limited, 0)
        self.storage.finish_run(
            run_id, report.status,
            {"pages_ok": report.pages_ok, "pages_failed": report.pages_failed,
             "rate_limited": report.rate_limited, "retries": report.retries},
            report.note, self.clock.iso())
        return report
