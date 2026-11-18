import tempfile
import unittest
from datetime import date
from pathlib import Path

from pricehunter.model import DerivedState, VersionState
from pricehunter.revision import revise_price, upsert_price
from pricehunter.rules import evaluate_anomalies, evaluate_promo
from pricehunter.storage import Storage
from pricehunter.timeutil import Clock

NOW = "2026-11-18T09:00:00+00:00"


class StorageRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "t.db",
                               Path(self.tmp.name) / "snaps")
        self.storage.upsert_source({
            "source_id": "s1", "name": "S1", "base_url": "http://x",
            "parser_key": "k", "parser_version": "v1",
            "promo_threshold": 0.10, "anomaly_z": 2.0, "active": 1})
        self.clock = Clock()

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def _ingest(self, sku, day, price, list_price=None):
        return upsert_price(
            self.storage, source_id="s1", sku=sku, name=sku,
            observed_date=day, price_cents=price, list_price_cents=list_price,
            currency="USD", promo_code=None, parser_version="v1",
            snapshot_id="snap-x", now_iso=NOW)

    def test_upsert_is_idempotent_and_then_revises(self):
        vid1, created1 = self._ingest("SKU-1", "2026-11-13", 1000)
        vid2, created2 = self._ingest("SKU-1", "2026-11-13", 1000)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(vid1, vid2)

        vid3, created3 = self._ingest("SKU-1", "2026-11-13", 900)
        self.assertTrue(created3)
        old = self.storage.conn.execute(
            "SELECT state FROM price_versions WHERE version_id=?", (vid1,)).fetchone()
        self.assertEqual(old["state"], VersionState.SUPERSEDED.value)
        chain = self.storage.price_chain("SKU-1")
        self.assertEqual([v["revision_no"] for v in chain], [0, 1])
        self.assertTrue(all(v["observed_date"] == "2026-11-13" for v in chain))

    def test_promo_threshold(self):
        hit = evaluate_promo(
            {"sku": "s", "source_id": "s1", "observed_date": "d",
             "version_id": "v", "price_cents": 899, "list_price_cents": 999,
             "promo_code": None, "recorded_at": NOW}, 0.10)
        self.assertIsNotNone(hit)  # 10.01% >= 10%
        none = evaluate_promo(
            {"sku": "s", "source_id": "s1", "observed_date": "d",
             "version_id": "v", "price_cents": 950, "list_price_cents": 999,
             "promo_code": None, "recorded_at": NOW}, 0.10)
        self.assertIsNone(none)  # 4.9% < 10%

    def test_anomaly_needs_three_points_and_detects_spike(self):
        history = [
            {"sku": "s", "source_id": "s1", "observed_date": f"d{i}",
             "version_id": f"v{i}", "price_cents": p, "recorded_at": NOW}
            for i, p in enumerate([1000, 1010, 990, 1005])
        ]
        # 当前点不参与基线统计：正常值不报警
        current_normal = dict(history[-1], observed_date="d4", version_id="v4")
        self.assertEqual(evaluate_anomalies(history, current_normal, 2.0), [])
        # 尖峰点与历史基线相比 z 超阈值
        spike = dict(current_normal, price_cents=2600)
        alerts = evaluate_anomalies(history, spike, 2.0)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "price_spike")
        # 历史不足 3 点不判定
        self.assertEqual(evaluate_anomalies(history[:2], spike, 2.0), [])

    def test_revision_cascades_promo_and_alerts(self):
        # 构造一条稳定历史 + 一个错误低价（当日恰好误触促销阈值）
        for day, price in [
            ("2026-11-13", 1299), ("2026-11-14", 499),
            ("2026-11-15", 1299), ("2026-11-16", 1299),
        ]:
            self._ingest("SKU-1001", day, price,
                         list_price=1299 if day == "2026-11-14" else None)
            from pricehunter.rules import recompute_for_sku
            recompute_for_sku(self.storage, "s1", "SKU-1001", day,
                              threshold=0.10, z_limit=2.0, now_iso=NOW)

        promos_before = self.storage.active_promos("s1")
        alerts_before = self.storage.active_alerts("s1")
        self.assertTrue(any(p["observed_date"] == "2026-11-14" for p in promos_before))

        # 回溯修订 11-14：499 -> 1250（与划线价 1299 相比折扣不足 10%）
        new_vid = revise_price(
            self.storage, source_id="s1", sku="SKU-1001",
            observed_date="2026-11-14", price_cents=1250, list_price_cents=None,
            currency="USD", promo_code=None, parser_version="v2",
            snapshot_id="snap-archive", reason="archive correction", now_iso=NOW)
        self.assertIsNotNone(new_vid)

        # 旧促销/告警全部作废，新集合按修订后数据重算
        superseded_promos = self.storage.conn.execute(
            "SELECT COUNT(*) c FROM promo_hits WHERE state=?",
            (DerivedState.SUPERSEDED.value,)).fetchone()["c"]
        self.assertGreaterEqual(superseded_promos, 1)
        promos_after = self.storage.active_promos("s1")
        self.assertFalse(any(p["observed_date"] == "2026-11-14" for p in promos_after))

        # 修订记录可审计，价格链闭合
        revisions = self.storage.list_revisions("s1")
        self.assertEqual(revisions[0]["reason"], "archive correction")
        current = self.storage.current_version("SKU-1001", "2026-11-14")
        self.assertEqual(current["price_cents"], 1250)
        self.assertEqual(current["state"], VersionState.CURRENT.value)


if __name__ == "__main__":
    unittest.main()
