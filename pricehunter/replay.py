"""快照回放：用（可能是新版的）解析器重放历史快照，产出字段级差异。

回放全程只读：不修改 price_versions / 促销 / 告警，只写 replays 与 replay_diffs，
用于在升级解析器前评估结构变化带来的影响。
"""
from __future__ import annotations

from .model import SnapshotOutcome, VersionState
from .parsers import StructureChanged, get_parser
from .storage import Storage, new_id


def _coerce(value) -> str | None:
    if value is None:
        return None
    return str(value)


def replay_source(storage: Storage, source_id: str, parser_version: str,
                  now_iso: str) -> dict:
    src = storage.get_source(source_id)
    if src is None:
        raise KeyError(source_id)
    parser_key = src["parser_key"]
    parser = get_parser(parser_key, parser_version)
    replay_id = new_id("replay")
    snapshots = [s for s in storage.list_snapshots(source_id)
                 if s["outcome"] == SnapshotOutcome.OK.value]
    storage.create_replay({
        "replay_id": replay_id, "source_id": source_id, "parser_key": parser_key,
        "parser_version": parser_version, "started_at": now_iso,
        "snapshots_total": len(snapshots), "snapshots_failed": 0,
        "note": "read-only replay",
    })

    failed = 0
    diff_count = 0
    failure_samples: list[dict] = []
    for snap in snapshots:
        body = storage.read_snapshot_body(snap["snapshot_id"])
        try:
            parsed = parser.parse(body)
        except StructureChanged as exc:
            failed += 1
            storage.add_replay_diff({
                "diff_id": new_id("diff"), "replay_id": replay_id,
                "snapshot_id": snap["snapshot_id"], "sku": "*", "field": "__parse__",
                "old_value": None, "new_value": str(exc),
                "diff_type": "structure_changed",
            })
            diff_count += 1
            failure_samples.append({"snapshot_id": snap["snapshot_id"],
                                    "error": str(exc), "url": snap["url"]})
            continue

        for item in parsed.items:
            # 基准：该快照解析时刻入库、当前仍为 current 的同观测日版本
            current = storage.conn.execute(
                """SELECT * FROM price_versions
                   WHERE sku=? AND source_id=? AND state=?
                   ORDER BY revision_no DESC LIMIT 1""",
                (item.sku, source_id, VersionState.CURRENT.value),
            ).fetchone()
            if current is None:
                storage.add_replay_diff({
                    "diff_id": new_id("diff"), "replay_id": replay_id,
                    "snapshot_id": snap["snapshot_id"], "sku": item.sku,
                    "field": "__existence__", "old_value": None,
                    "new_value": "parsed_but_not_stored",
                    "diff_type": "missing_in_store",
                })
                diff_count += 1
                continue
            checks = {
                "price_cents": (current["price_cents"], item.price_cents),
                "list_price_cents": (current["list_price_cents"],
                                     item.list_price_cents),
                "currency": (current["currency"], item.currency),
                "promo_code": (current["promo_code"], item.promo_code),
            }
            for field, (old, new) in checks.items():
                if _coerce(old) != _coerce(new):
                    storage.add_replay_diff({
                        "diff_id": new_id("diff"), "replay_id": replay_id,
                        "snapshot_id": snap["snapshot_id"], "sku": item.sku,
                        "field": field, "old_value": _coerce(old),
                        "new_value": _coerce(new),
                        "diff_type": "value_changed",
                    })
                    diff_count += 1

    storage.finish_replay(replay_id, now_iso, failed)
    return {
        "replay_id": replay_id, "source_id": source_id,
        "parser_version": parser_version, "snapshots": len(snapshots),
        "failed": failed, "diffs": diff_count, "failure_samples": failure_samples,
    }
