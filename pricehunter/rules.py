"""促销命中与异常告警规则；所有派生结果都带版本、可被重算替换。"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from .model import DerivedState, VersionState
from .storage import Storage, new_id


@dataclass
class RuleResult:
    promo: dict | None = None
    alerts: list[dict] | None = None


def _discount_rate(price_cents: int, list_price_cents: int | None) -> float | None:
    if not list_price_cents or list_price_cents <= 0 or price_cents > list_price_cents:
        return None
    return round((list_price_cents - price_cents) / list_price_cents, 4)


def evaluate_promo(version: dict, threshold: float) -> dict | None:
    """基于当前价格版本计算促销命中；无促销返回 None。"""
    rate = _discount_rate(version["price_cents"], version["list_price_cents"])
    if rate is not None and rate >= threshold:
        return {
            "hit_id": new_id("promo"),
            "sku": version["sku"],
            "source_id": version["source_id"],
            "observed_date": version["observed_date"],
            "version_id": version["version_id"],
            "discount_rate": rate,
            "promo_code": version["promo_code"],
            "computed_at": version["recorded_at"],
        }
    # 显式促销码但价格未达折扣阈值也记录（阈值放宽场景由来源配置决定）
    if version["promo_code"] and rate is not None and rate > 0:
        return {
            "hit_id": new_id("promo"),
            "sku": version["sku"],
            "source_id": version["source_id"],
            "observed_date": version["observed_date"],
            "version_id": version["version_id"],
            "discount_rate": rate,
            "promo_code": version["promo_code"],
            "computed_at": version["recorded_at"],
        }
    return None


def evaluate_anomalies(history: list[dict], current: dict, z_limit: float) -> list[dict]:
    """基于该 SKU 此前的当前版本序列（不含当前点）做 z-score 异常检测。

    至少 3 个历史观测点才判定，避免小样本误报；当前点只用于比对，不参与基线统计。
    """
    alerts: list[dict] = []
    prior = [v["price_cents"] for v in history
             if v["observed_date"] < current["observed_date"]]
    if len(prior) < 3:
        return alerts
    mean = statistics.fmean(prior)
    stdev = statistics.pstdev(prior)
    if stdev == 0:
        # 全历史同价但当前价不同：任何偏离都算异常，方向按价格变动判定
        if current["price_cents"] != prior[-1]:
            kind = "price_spike" if current["price_cents"] > prior[-1] else "price_drop"
            alerts.append(_alert(current, kind, "high",
                                 f"历史价格恒定 {prior[-1]}，当前 {current['price_cents']} 出现偏离"))
        return alerts
    z = (current["price_cents"] - mean) / stdev
    if abs(z) >= z_limit:
        kind = "price_spike" if z > 0 else "price_drop"
        severity = "high" if abs(z) >= z_limit + 1 else "medium"
        alerts.append(_alert(
            current, kind, severity,
            f"z={z:.2f} 超出阈值 ±{z_limit}（均值 {mean:.0f}，σ={stdev:.0f}，"
            f"当前 {current['price_cents']}）"))
    return alerts


def _alert(version: dict, kind: str, severity: str, message: str) -> dict:
    return {
        "alert_id": new_id("alert"),
        "sku": version["sku"],
        "source_id": version["source_id"],
        "observed_date": version["observed_date"],
        "kind": kind,
        "severity": severity,
        "message": message,
        "version_id": version["version_id"],
        "computed_at": version["recorded_at"],
    }


def current_history(storage: Storage, sku: str) -> list[dict]:
    rows = storage.conn.execute(
        """SELECT * FROM price_versions
           WHERE sku=? AND state=?
           ORDER BY observed_date, revision_no""",
        (sku, VersionState.CURRENT.value),
    ).fetchall()
    return [dict(r) for r in rows]


def recompute_for_sku(storage: Storage, source_id: str, sku: str,
                      observed_date: str, *, threshold: float,
                      z_limit: float, now_iso: str) -> RuleResult:
    """重算单个 (sku, 观测日) 的促销 + 该 SKU 全序列异常告警。

    历史修订后调用：旧派生行置 superseded，按最新当前价格版本重新计算。
    """
    storage.supersede_derived_for(source_id, sku, observed_date)

    version = storage.current_version(sku, observed_date)
    if version is None:
        storage.conn.commit()
        return RuleResult()
    v = dict(version)
    v.setdefault("recorded_at", now_iso)

    promo = evaluate_promo(v, threshold)
    if promo:
        storage.add_promo_hit(promo)

    # 异常基于整条当前版本链（修订会改变历史值，必须整链重算）
    history = current_history(storage, sku)
    alerts = evaluate_anomalies(history, v, z_limit)
    for alert in alerts:
        storage.add_alert(alert)
    storage.conn.commit()
    return RuleResult(promo=promo, alerts=alerts)


def maintenance_alert(storage: Storage, source_id: str, observed_date: str,
                      detail: str, now_iso: str) -> dict:
    alert = {
        "alert_id": new_id("alert"),
        "sku": "*",  # 来源级告警，不属于单个 SKU
        "source_id": source_id,
        "observed_date": observed_date,
        "kind": "maintenance",
        "severity": "high",
        "message": detail,
        "version_id": None,
        "computed_at": now_iso,
    }
    storage.add_alert(alert)
    storage.conn.commit()
    return alert


def recompute_source(storage: Storage, source_id: str) -> int:
    """对来源下所有 (sku, 观测日) 全量重算；返回重算的键数量。"""
    src = storage.get_source(source_id)
    keys = storage.conn.execute(
        "SELECT DISTINCT sku, observed_date FROM price_versions WHERE source_id=? "
        "AND state=?",
        (source_id, VersionState.CURRENT.value),
    ).fetchall()
    # 先整体作废来源的活动派生行
    storage.conn.execute(
        "UPDATE promo_hits SET state=? WHERE source_id=? AND state=?",
        (DerivedState.SUPERSEDED.value, source_id, DerivedState.ACTIVE.value),
    )
    storage.conn.execute(
        "UPDATE alerts SET state=? WHERE source_id=? AND state=? AND sku <> '*'",
        (DerivedState.SUPERSEDED.value, source_id, DerivedState.ACTIVE.value),
    )
    count = 0
    for row in keys:
        v = dict(storage.current_version(row["sku"], row["observed_date"]))
        if promo := evaluate_promo(v, src["promo_threshold"]):
            storage.add_promo_hit(promo)
        history = current_history(storage, row["sku"])
        for alert in evaluate_anomalies(history, v, src["anomaly_z"]):
            storage.add_alert(alert)
        count += 1
    storage.conn.commit()
    return count
