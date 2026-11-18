"""本地夹具 HTTP 服务器：用 X-Sim-Date 头或 ?sim_date= 选择场景日期。

路由：
  GET /acme/products?page=N[&archive=YYYY-MM-DD]
  GET /bargain/api/items?cursor=...
  GET /ceg/catalog/page/N
  GET /healthz
  POST /control/reset            重置限流计数（跨来源独立计数）
所有响应头带回显的 X-Sim-Date，便于调试与快照溯源。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import catalog

_lock = threading.Lock()
# 每个 (source, sim_date) 的请求计数，用于确定性 429
_rate_counters: dict[tuple[str, str], int] = {}


def _sim_date(handler: BaseHTTPRequestHandler, query: dict) -> str:
    return (handler.headers.get("X-Sim-Date")
            or query.get("sim_date", [catalog.SCENARIO_DAYS[0]])[0])


def _reset_counters() -> None:
    with _lock:
        _rate_counters.clear()


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "PriceHunterFixture/1.0"

    def log_message(self, fmt, *args):  # 静音
        pass

    # ---- 工具 ----
    def _send(self, status: int, body: bytes, content_type: str,
              sim_date: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Sim-Date", sim_date)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, sim_date: str, status: int = 200,
              extra: dict | None = None) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", sim_date, extra)

    def _html(self, html: str, sim_date: str, status: int = 200) -> None:
        self._send(status, html.encode("utf-8"),
                   "text/html; charset=utf-8", sim_date)

    def _maybe_429(self, source: str, sim_date: str) -> bool:
        if source == "bargain" and sim_date == catalog.BARGAIN["rate_limit_day"]:
            key = (source, sim_date)
            with _lock:
                n = _rate_counters.get(key, 0) + 1
                _rate_counters[key] = n
            if n <= catalog.BARGAIN["rate_limit_hits"]:
                self._json(
                    {"error": "rate_limited", "retry_after_seconds": 1},
                    sim_date, status=429,
                    extra={"Retry-After": "1"},
                )
                return True
        return False

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        sim_date = _sim_date(self, query)
        path = parsed.path

        if path == "/healthz":
            self._json({"status": "ok"}, sim_date)
            return

        if path.startswith("/acme/products"):
            page_no = int(query.get("page", ["1"])[0])
            archive = query.get("archive", [None])[0]
            self._html(catalog.acme_html(sim_date, page_no, archive), sim_date)
            return

        if path.startswith("/bargain/api/items"):
            if self._maybe_429("bargain", sim_date):
                return
            cursor = query.get("cursor", [None])[0]
            self._json(catalog.bargain_json(sim_date, cursor), sim_date)
            return

        if path.startswith("/ceg/catalog/"):
            if sim_date == catalog.CEG["hard_maintenance_day"]:
                # 硬维护：503 + Retry-After
                self._send(
                    503,
                    catalog.soft_maintenance_html("CegMart").encode("utf-8"),
                    "text/html; charset=utf-8", sim_date,
                    extra={"Retry-After": "60"},
                )
                return
            if sim_date == catalog.CEG["soft_maintenance_day"]:
                # 软维护：HTTP 200，但页面带显式维护标记
                self._html(catalog.soft_maintenance_html("CegMart"), sim_date)
                return
            parts = path.rstrip("/").split("/")
            page_no = int(parts[-1])
            self._html(catalog.ceg_html(sim_date, page_no), sim_date)
            return

        self._json({"error": "not_found", "path": path}, sim_date, status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/control/reset":
            _reset_counters()
            self._json({"reset": True}, self.headers.get("X-Sim-Date", ""))
            return
        self._json({"error": "not_found"}, self.headers.get("X-Sim-Date", ""), 404)


def serve(host: str = "127.0.0.1", port: int = 8088) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), FixtureHandler)
    return httpd


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="启动价格采集夹具服务器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    server = serve(args.host, args.port)
    print(f"fixture server on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
