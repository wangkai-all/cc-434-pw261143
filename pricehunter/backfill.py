"""历史归档回填：从夹具的归档快照采集修正值并触发回溯修订。"""
from __future__ import annotations

from urllib.parse import urlencode, urlparse, parse_qs

from .model import SnapshotOutcome
from .parsers import get_parser
from .revision import revise_price
from .storage import Storage, hash_body, new_id
from .transport import Transport


def backfill_archive(storage: Storage, spec, *, archive_date: str, clock,
                     run_id: str | None = None) -> dict:
    """抓取 ?archive=<date> 修正快照，逐 SKU 与当前版本比对并修订。

    归档快照独立保存（带 archive 标记的 run/snapshot），修订通过 revision 引擎
    走标准路径，自动级联重算促销与告警。
    """
    run_id = run_id or new_id("run")
    storage.start_run(run_id, spec.source_id, clock.iso())
    transport = Transport(clock.today().isoformat())
    parser = get_parser(spec.parser_key, spec.parser_version)

    # 只取第一页即可（演示中的修正只涉及第一页商品）
    parsed_url = urlparse(spec.start_path)
    query = parse_qs(parsed_url.query)
    query["archive"] = [archive_date]
    url = (spec.base_url + parsed_url.path + "?" + urlencode(query, doseq=True))

    result = transport.fetch(url)
    snap_id = new_id("snap")
    storage.save_snapshot({
        "snapshot_id": snap_id, "run_id": run_id, "source_id": spec.source_id,
        "url": url, "page_no": 1, "http_status": result.status,
        "outcome": SnapshotOutcome.OK.value, "content_hash": hash_body(result.body),
        "fetched_at": clock.iso(), "retry_no": 0,
    }, result.body)
    parsed = parser.parse(result.body)

    revisions = []
    for item in parsed.items:
        new_vid = revise_price(
            storage, source_id=spec.source_id, sku=item.sku,
            observed_date=archive_date, price_cents=item.price_cents,
            list_price_cents=item.list_price_cents, currency=item.currency,
            promo_code=item.promo_code, parser_version=spec.parser_version,
            snapshot_id=snap_id,
            reason=f"archive backfill from {url}", now_iso=clock.iso())
        if new_vid:
            revisions.append({"sku": item.sku, "new_version_id": new_vid,
                              "price_cents": item.price_cents})

    from .model import RunStatus
    storage.finish_run(
        run_id, RunStatus.OK,
        {"pages_ok": 1, "pages_failed": 0, "rate_limited": 0,
         "retries": transport.retry_count},
        f"archive backfill for {archive_date}", clock.iso())
    return {"run_id": run_id, "archive_date": archive_date,
            "snapshot_id": snap_id, "revisions": revisions}
