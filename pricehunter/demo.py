"""端到端演示编排：按场景时间线逐日采集三个来源，驱动全部故障与修订路径。"""
from __future__ import annotations

import urllib.request
from dataclasses import replace
from datetime import date

from .backfill import backfill_archive
from .pipeline import Collector
from .replay import replay_source
from .sources import default_sources
from .timeutil import Clock


def _reset_fixture(base_url: str) -> None:
    """重置夹具限流计数，保证演示可重复运行（计数器按来源+日期独立）。"""
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/control/reset",
                                     method="POST")
        urllib.request.urlopen(req, timeout=5).read()
    except OSError:
        pass  # 夹具不可用时让后续采集流程报真实网络错误


def run_demo(storage, *, base_url: str, clock: Clock, verbose: bool = True) -> dict:
    specs = {s.source_id: s for s in default_sources(base_url)}
    for spec in specs.values():
        storage.upsert_source({
            "source_id": spec.source_id, "name": spec.name,
            "base_url": spec.base_url, "parser_key": spec.parser_key,
            "parser_version": spec.parser_version,
            "promo_threshold": spec.promo_threshold, "anomaly_z": spec.anomaly_z,
        })

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    timeline = [
        (2026, 11, 13), (2026, 11, 14), (2026, 11, 15),
        (2026, 11, 16), (2026, 11, 17), (2026, 11, 18),
    ]
    collector = Collector(storage, clock=clock)
    results = []
    _reset_fixture(base_url)

    for y, m, d in timeline:
        clock.set_date(date(y, m, d))
        log(f"\n=== 模拟日 {clock.today()} ===")
        # 每个来源独立 run，任何一个来源失败都不影响其他来源
        for source_id in ("acme", "bargain", "ceg"):
            report = collector.run_source(specs[source_id])
            results.append(report)
            log(f"  [{source_id:8s}] status={report.status.value:20s} "
                f"ok={report.pages_ok} fail={report.pages_failed} "
                f"429_retries={report.retries} items={report.items_ingested} "
                f"note={report.note or ''}")

    # D6：先回放验证 v2 解析器对全部历史快照的差异（只读）
    log("\n=== 回放：acme-parser-v2 重放 acme 全部快照（只读） ===")
    replay = replay_source(storage, "acme", "acme-parser-v2", clock.iso())
    log(f"  snapshots={replay['snapshots']} parse_failed={replay['failed']} "
        f"diffs={replay['diffs']}")

    # 升级 acme 解析器并单来源重跑（不影响 bargain / ceg）
    log("\n=== 升级 acme -> acme-parser-v2 并重跑 ===")
    storage.conn.execute(
        "UPDATE sources SET parser_version=? WHERE source_id=?",
        ("acme-parser-v2", "acme"))
    storage.conn.commit()
    specs["acme"] = replace(specs["acme"], parser_version="acme-parser-v2")
    report = collector.run_source(specs["acme"])
    results.append(report)
    log(f"  [acme    ] status={report.status.value} ok={report.pages_ok} "
        f"fail={report.pages_failed} items={report.items_ingested}")

    # 归档回溯：修正 11-14 SKU-1001 的错误价 499 -> 1250，级联重算促销/告警
    log("\n=== 回溯修订：acme 11-14 归档快照（499 -> 1250） ===")
    backfill = backfill_archive(
        storage, specs["acme"], archive_date="2026-11-14", clock=clock)
    for rev in backfill["revisions"]:
        log(f"  revised {rev['sku']} -> {rev['price_cents']} "
            f"(version {rev['new_version_id'][:18]}…)")

    return {
        "runs": results,
        "replay": replay,
        "backfill": backfill,
        "final_parser": {sid: storage.get_source(sid)["parser_version"]
                         for sid in specs},
    }
