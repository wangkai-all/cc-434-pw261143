import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import date
from pathlib import Path

from pricehunter.dashboard_server import Dashboard
from pricehunter.demo import run_demo
from pricehunter.fixtures.server import serve as serve_fixtures
from pricehunter.model import RunStatus, SnapshotOutcome
from pricehunter.pipeline import Collector
from pricehunter.replay import replay_source
from pricehunter.sources import default_sources
from pricehunter.storage import Storage
from pricehunter.timeutil import Clock


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = serve_fixtures("127.0.0.1", 0)
        cls.port = cls.fixture.server_address[1]
        cls.thread = threading.Thread(target=cls.fixture.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.fixture.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.tmp.name) / "t.db",
                               Path(self.tmp.name) / "snaps")
        self.clock = Clock()

    def tearDown(self):
        self.storage.close()
        self.tmp.cleanup()

    def test_full_demo_timeline(self):
        result = run_demo(self.storage, base_url=self.base_url,
                          clock=self.clock, verbose=False)

        # 1) 429：bargain 在 11-14 经历两次限流后重试成功，且 429 响应也留快照
        bargain_runs_14 = [r for r in result["runs"]
                           if r.source_id == "bargain" and r.retries > 0]
        self.assertTrue(bargain_runs_14, "应出现 429 重试")
        limited_snaps = self.storage.conn.execute(
            "SELECT COUNT(*) c FROM snapshots WHERE source_id='bargain' "
            "AND outcome='rate_limited'").fetchone()["c"]
        self.assertGreaterEqual(limited_snaps, 2)
        self.assertGreaterEqual(bargain_runs_14[0].retries, 2)

        # 2) 软维护（200）与硬维护（503）都被识别为 maintenance，而不是空结果
        maint = self.storage.conn.execute(
            "SELECT COUNT(*) c FROM snapshots WHERE source_id='ceg' "
            "AND outcome='maintenance'").fetchone()["c"]
        self.assertEqual(maint, 2)
        ceg_runs = {(r.status, r.note) for r in result["runs"] if r.source_id == "ceg"}
        self.assertTrue(any(s == RunStatus.MAINTENANCE for s, _ in ceg_runs))
        # 维护日没有任何价格写入
        blocked = self.storage.conn.execute(
            "SELECT COUNT(*) c FROM price_versions WHERE source_id='ceg' "
            "AND observed_date IN ('2026-11-15','2026-11-16')").fetchone()["c"]
        self.assertEqual(blocked, 0)
        # 维护产生来源级告警
        self.assertTrue(self.storage.active_alerts("ceg"))

        # 3) 结构变化：11-16 acme v1 解析失败并留存失败样本
        failures = self.storage.list_parse_attempts(source_id="acme", ok=1 - 1)
        self.assertTrue(failures)
        sample = Path(failures[0]["sample_path"])
        self.assertTrue(sample.exists() and sample.stat().st_size > 0)

        # 4) 回放：v2 重放历史快照，v1 结构快照应报 structure_changed 差异
        replay = result["replay"]
        self.assertGreater(replay["failed"], 0)
        diffs = self.storage.replay_diffs(replay["replay_id"])
        self.assertTrue(any(d["diff_type"] == "structure_changed" for d in diffs))
        # 回放只读：不产生新价格版本
        versions_before = self.storage.conn.execute(
            "SELECT COUNT(*) c FROM price_versions").fetchone()["c"]
        replay_source(self.storage, "acme", "acme-parser-v2", self.clock.iso())
        versions_after = self.storage.conn.execute(
            "SELECT COUNT(*) c FROM price_versions").fetchone()["c"]
        self.assertEqual(versions_before, versions_after)

        # 5) 升级解析器后重跑：本次执行全部成功（历史 v1 失败仍保留在审计记录中）
        last_acme = [r for r in result["runs"] if r.source_id == "acme"][-1]
        self.assertEqual(last_acme.status, RunStatus.OK)
        self.assertEqual(last_acme.pages_failed, 0)
        self.assertGreater(last_acme.pages_ok, 0)
        self.assertEqual(self.storage.get_source("acme")["parser_version"],
                         "acme-parser-v2")

        # 6) 回溯修订：499 -> 1250，旧版本 superseded，派生结果级联重算
        revs = self.storage.list_revisions("acme")
        rev = next(r for r in revs if r["observed_date"] == "2026-11-14"
                   and r["sku"] == "SKU-1001")
        current = self.storage.current_version("SKU-1001", "2026-11-14")
        self.assertEqual(current["price_cents"], 1250)
        self.assertEqual(current["version_id"], rev["new_version_id"])
        chain = self.storage.price_chain("SKU-1001")
        self.assertIn(1, [v["revision_no"] for v in chain])

        # 7) 原始快照全部落盘且可读
        for snap in self.storage.list_snapshots("acme"):
            body = self.storage.read_snapshot_body(snap["snapshot_id"])
            self.assertGreater(len(body), 0)

        # 8) 异常告警链路：bargain SKU-2003 尖峰被捕获
        bargain_alerts = self.storage.active_alerts("bargain")
        self.assertTrue(any(a["kind"] == "price_spike"
                            and a["sku"] == "SKU-2003" for a in bargain_alerts))

    def test_single_source_rerun_does_not_touch_others(self):
        run_demo(self.storage, base_url=self.base_url,
                 clock=self.clock, verbose=False)
        before = {s["source_id"]:
                  self.storage.conn.execute(
                      "SELECT COUNT(*) c FROM runs WHERE source_id=?",
                      (s["source_id"],)).fetchone()["c"]
                  for s in self.storage.list_sources()}
        versions_before = self.storage.conn.execute(
            "SELECT COUNT(*) c FROM price_versions WHERE source_id<>'acme'").fetchone()["c"]

        dashboard = Dashboard(self.storage, clock=self.clock, base_url=self.base_url)
        dashboard.set_sim_date("2026-11-18")
        report = dashboard.rerun("acme")
        self.assertEqual(report["source_id"], "acme")

        # 其他来源既没有新 run，也没有新价格版本
        for sid in ("bargain", "ceg"):
            after = self.storage.conn.execute(
                "SELECT COUNT(*) c FROM runs WHERE source_id=?", (sid,)).fetchone()["c"]
            self.assertEqual(after, before[sid])
        versions_after = self.storage.conn.execute(
            "SELECT COUNT(*) c FROM price_versions WHERE source_id<>'acme'").fetchone()["c"]
        self.assertEqual(versions_before, versions_after)

    def test_dashboard_rebuild_keeps_upgraded_parser_version(self):
        run_demo(self.storage, base_url=self.base_url,
                 clock=self.clock, verbose=False)
        self.assertEqual(self.storage.get_source("acme")["parser_version"],
                         "acme-parser-v2")
        # 仪表盘重新实例化（等价于重启）不得把解析器版本回退到默认 v1
        Dashboard(self.storage, clock=self.clock, base_url=self.base_url)
        self.assertEqual(self.storage.get_source("acme")["parser_version"],
                         "acme-parser-v2")

    def test_health_and_status_endpoints(self):
        run_demo(self.storage, base_url=self.base_url,
                 clock=self.clock, verbose=False)
        dashboard = Dashboard(self.storage, clock=self.clock, base_url=self.base_url)
        health = dashboard.health_payload()
        ids = {h["source_id"] for h in health}
        self.assertEqual(ids, {"acme", "bargain", "ceg"})
        ceg = next(h for h in health if h["source_id"] == "ceg")
        self.assertGreater(ceg["snaps_maint"], 0)
        self.assertGreater(ceg["parse_failures"] + ceg["snaps_maint"], 0)


if __name__ == "__main__":
    unittest.main()
