"""仪表盘 HTTP 服务：只读 JSON API + 单页前端 + 单来源重跑动作。"""
from __future__ import annotations

import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .backfill import backfill_archive
from .fixtures.server import _reset_counters
from .pipeline import Collector
from .replay import replay_source
from .sources import default_sources
from .timeutil import Clock
from .webapp import INDEX_HTML


class Dashboard:
    def __init__(self, storage, *, clock: Clock, base_url: str) -> None:
        self.storage = storage
        self.clock = clock
        self.base_url = base_url
        self.specs = {s.source_id: s for s in default_sources(base_url)}
        for spec in self.specs.values():
            existing = storage.get_source(spec.source_id)
            if existing is None:
                storage.upsert_source({
                    "source_id": spec.source_id, "name": spec.name,
                    "base_url": spec.base_url, "parser_key": spec.parser_key,
                    "parser_version": spec.parser_version,
                    "promo_threshold": spec.promo_threshold,
                    "anomaly_z": spec.anomaly_z,
                })
            else:
                # 已注册来源：保留用户已升级的解析器版本，只刷新名称/地址等静态信息
                storage.conn.execute(
                    "UPDATE sources SET name=?, base_url=? WHERE source_id=?",
                    (spec.name, spec.base_url, spec.source_id))
                storage.conn.commit()

    # ---------- 聚合数据 ----------
    def health_payload(self) -> list[dict]:
        rows = self.storage.health()
        out = []
        for r in rows:
            d = dict(r)
            snaps = max(d["snaps"], 1)
            d["success_rate"] = round(d["snaps_ok"] / snaps, 4)
            out.append(d)
        return out

    def failures_payload(self) -> list[dict]:
        rows = self.storage.list_parse_attempts(ok=0, limit=100)
        out = []
        for r in rows:
            out.append({
                "attempt_id": r["attempt_id"], "source_id": r["source_id"],
                "parser_version": r["parser_version"], "error_kind": r["error_kind"],
                "error_message": r["error_message"], "url": r["url"],
                "snapshot_id": r["snapshot_id"], "parsed_at": r["parsed_at"],
                "sample_path": r["sample_path"],
            })
        return out

    def replay_payload(self) -> list[dict]:
        out = []
        for r in self.storage.list_replays():
            d = dict(r)
            d["diffs"] = [dict(x) for x in self.storage.replay_diffs(r["replay_id"])]
            out.append(d)
        return out

    def chain_payload(self) -> dict:
        chains = {}
        for sku in self.storage.list_skus():
            versions = []
            for v in self.storage.price_chain(sku):
                versions.append({
                    "version_id": v["version_id"], "source_id": v["source_id"],
                    "observed_date": v["observed_date"],
                    "price_cents": v["price_cents"],
                    "list_price_cents": v["list_price_cents"],
                    "currency": v["currency"], "promo_code": v["promo_code"],
                    "parser_version": v["parser_version"],
                    "state": v["state"], "revision_no": v["revision_no"],
                    "recorded_at": v["recorded_at"],
                    "snapshot_id": v["snapshot_id"],
                })
            chains[sku] = versions
        return {
            "chains": chains,
            "promos": [dict(r) for r in self.storage.active_promos()],
            "alerts": [dict(r) for r in self.storage.active_alerts()],
            "revisions": [dict(r) for r in self.storage.list_revisions()],
        }

    # ---------- 动作 ----------
    def rerun(self, source_id: str) -> dict:
        """单来源重跑：独立 run、独立 Transport，不触碰其他来源的数据。"""
        # 与 sources 表中的 parser_version 对齐（允许用户先升级解析器）
        row = self.storage.get_source(source_id)
        spec = replace(self.specs[source_id], parser_version=row["parser_version"])
        report = Collector(self.storage, clock=self.clock).run_source(spec)
        return {
            "run_id": report.run_id, "source_id": report.source_id,
            "status": report.status.value, "pages_ok": report.pages_ok,
            "pages_failed": report.pages_failed, "retries": report.retries,
            "items_ingested": report.items_ingested, "note": report.note,
            "failures": report.failures, "sim_date": self.clock.today().isoformat(),
        }

    def upgrade_parser(self, source_id: str, parser_version: str) -> dict:
        from .parsers import PARSER_REGISTRY
        row = self.storage.get_source(source_id)
        available = PARSER_REGISTRY[row["parser_key"]]
        if parser_version not in available:
            raise ValueError(f"未知解析器版本 {parser_version}，可选: {list(available)}")
        self.storage.conn.execute(
            "UPDATE sources SET parser_version=? WHERE source_id=?",
            (parser_version, source_id))
        self.storage.conn.commit()
        return {"source_id": source_id, "parser_version": parser_version}

    def run_replay(self, source_id: str, parser_version: str | None) -> dict:
        version = parser_version or self.storage.get_source(source_id)["parser_version"]
        return replay_source(self.storage, source_id, version, self.clock.iso())

    def run_backfill(self, source_id: str, archive_date: str) -> dict:
        row = self.storage.get_source(source_id)
        spec = replace(self.specs[source_id], parser_version=row["parser_version"])
        return backfill_archive(self.storage, spec, archive_date=archive_date,
                                clock=self.clock)

    def set_sim_date(self, day: str) -> dict:
        from datetime import date
        y, m, d = (int(x) for x in day.split("-"))
        self.clock.set_date(date(y, m, d))
        _reset_counters()  # 新的一天，夹具限流计数归零
        return {"sim_date": self.clock.today().isoformat()}

    def snapshot_payload(self, snapshot_id: str) -> dict:
        row = self.storage.get_snapshot(snapshot_id)
        if row is None:
            raise KeyError(snapshot_id)
        body = self.storage.read_snapshot_body(snapshot_id)
        preview = body[:200_000].decode("utf-8", errors="replace")
        return {"meta": dict(row), "body": preview}


def make_handler(dashboard: Dashboard):
    storage = dashboard.storage

    class Handler(BaseHTTPRequestHandler):
        server_version = "PriceHunterDashboard/1.0"

        def log_message(self, fmt, *args):
            pass

        def _json(self, payload, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            try:
                if path == "/":
                    data = INDEX_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if path == "/api/health":
                    return self._json(dashboard.health_payload())
                if path == "/api/failures":
                    return self._json(dashboard.failures_payload())
                if path == "/api/replays":
                    return self._json(dashboard.replay_payload())
                if path == "/api/chains":
                    return self._json(dashboard.chain_payload())
                if path == "/api/sources":
                    return self._json([dict(r) for r in storage.list_sources()])
                if path == "/api/runs":
                    source_id = query.get("source", [None])[0]
                    return self._json([dict(r) for r in storage.list_runs(source_id)])
                if path == "/api/snapshot":
                    return self._json(
                        dashboard.snapshot_payload(query["id"][0]))
                if path == "/api/clock":
                    return self._json({"sim_date": dashboard.clock.today().isoformat()})
                self._json({"error": "not found"}, 404)
            except KeyError as exc:
                self._json({"error": f"not found: {exc}"}, 404)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                if path == "/api/clock":
                    return self._json(dashboard.set_sim_date(payload["sim_date"]))
                if path == "/api/rerun":
                    return self._json(dashboard.rerun(payload["source_id"]))
                if path == "/api/parser":
                    return self._json(dashboard.upgrade_parser(
                        payload["source_id"], payload["parser_version"]))
                if path == "/api/replay":
                    return self._json(dashboard.run_replay(
                        payload["source_id"], payload.get("parser_version")))
                if path == "/api/backfill":
                    return self._json(dashboard.run_backfill(
                        payload["source_id"], payload["archive_date"]))
                self._json({"error": "not found"}, 404)
            except KeyError as exc:
                self._json({"error": f"missing/not-found: {exc}"}, 404)
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    return Handler


def serve_dashboard(storage, clock: Clock, base_url: str,
                    host: str = "127.0.0.1", port: int = 8090) -> ThreadingHTTPServer:
    dashboard = Dashboard(storage, clock=clock, base_url=base_url)
    return ThreadingHTTPServer((host, port), make_handler(dashboard))
