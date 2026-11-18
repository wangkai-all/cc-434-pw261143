"""历史价格修订：插入新版本、关闭旧版本有效区间、级联重算派生结果。"""
from __future__ import annotations

from .rules import recompute_for_sku
from .storage import Storage, new_id


def upsert_price(storage: Storage, *, source_id: str, sku: str, name: str,
                 observed_date: str, price_cents: int, list_price_cents: int | None,
                 currency: str, promo_code: str | None, parser_version: str,
                 snapshot_id: str, now_iso: str) -> tuple[str, bool]:
    """写入当日观测。

    同 (sku, observed_date) 已存在当前版本：
    - 值完全一致：幂等返回旧版本；
    - 值不同：旧版本置 superseded（valid 区间闭合），写入 revision_no+1 的新版本，
      并记录 revision 审计行。
    返回 (version_id, created)。
    """
    storage.ensure_product(sku, name, now_iso)
    existing = storage.current_version(sku, observed_date)
    if existing is not None:
        same = (
            existing["price_cents"] == price_cents
            and (existing["list_price_cents"] or None) == (list_price_cents or None)
            and existing["currency"] == currency
            and (existing["promo_code"] or None) == (promo_code or None)
        )
        if same:
            return existing["version_id"], False
        new_id_ = new_id("pv")
        storage.supersede_version(existing["version_id"], new_id_)
        storage.insert_price_version({
            "version_id": new_id_, "sku": sku, "source_id": source_id,
            "observed_date": observed_date, "price_cents": price_cents,
            "list_price_cents": list_price_cents, "currency": currency,
            "promo_code": promo_code, "parser_version": parser_version,
            "snapshot_id": snapshot_id, "recorded_at": now_iso,
            "revision_no": existing["revision_no"] + 1,
        })
        storage.add_revision_record({
            "revision_id": new_id("rev"), "source_id": source_id, "sku": sku,
            "observed_date": observed_date,
            "old_version_id": existing["version_id"], "new_version_id": new_id_,
            "reason": "reingest: value changed", "created_at": now_iso,
        })
        return new_id_, True

    vid = new_id("pv")
    storage.insert_price_version({
        "version_id": vid, "sku": sku, "source_id": source_id,
        "observed_date": observed_date, "price_cents": price_cents,
        "list_price_cents": list_price_cents, "currency": currency,
        "promo_code": promo_code, "parser_version": parser_version,
        "snapshot_id": snapshot_id, "recorded_at": now_iso, "revision_no": 0,
    })
    return vid, True


def revise_price(storage: Storage, *, source_id: str, sku: str, observed_date: str,
                 price_cents: int, list_price_cents: int | None, currency: str,
                 promo_code: str | None, parser_version: str, snapshot_id: str,
                 reason: str, now_iso: str) -> str | None:
    """对历史观测日做回溯修订。

    成功修订时：旧版本 superseded -> 新版本 current，写 revision 审计，
    并同步重算该 (sku, 日期) 的促销命中与全序列异常告警。
    若修正值与当前版本一致则不产生新版本，返回 None。
    """
    existing = storage.current_version(sku, observed_date)
    if existing is None:
        raise ValueError(
            f"无法修订不存在的观测: source={source_id} sku={sku} date={observed_date}")

    same = (
        existing["price_cents"] == price_cents
        and (existing["list_price_cents"] or None) == (list_price_cents or None)
        and existing["currency"] == currency
        and (existing["promo_code"] or None) == (promo_code or None)
    )
    if same:
        return None

    product = storage.conn.execute(
        "SELECT name FROM products WHERE sku=?", (sku,)).fetchone()
    name = product["name"] if product else sku
    new_version = new_id("pv")
    storage.supersede_version(existing["version_id"], new_version)
    storage.insert_price_version({
        "version_id": new_version, "sku": sku, "source_id": source_id,
        "observed_date": observed_date, "price_cents": price_cents,
        "list_price_cents": list_price_cents, "currency": currency,
        "promo_code": promo_code, "parser_version": parser_version,
        "snapshot_id": snapshot_id, "recorded_at": now_iso,
        "revision_no": existing["revision_no"] + 1,
    })
    storage.add_revision_record({
        "revision_id": new_id("rev"), "source_id": source_id, "sku": sku,
        "observed_date": observed_date,
        "old_version_id": existing["version_id"], "new_version_id": new_version,
        "reason": reason, "created_at": now_iso,
    })

    src = storage.get_source(source_id)
    recompute_for_sku(
        storage, source_id, sku, observed_date,
        threshold=src["promo_threshold"], z_limit=src["anomaly_z"], now_iso=now_iso,
    )
    return new_version
