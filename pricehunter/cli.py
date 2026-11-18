"""命令行入口。

用法：
  python -m pricehunter.cli init-db --data-dir ./data
  python -m pricehunter.cli serve-fixtures [--port 8088]
  python -m pricehunter.cli run-demo [--fixture-url http://127.0.0.1:8088]
  python -m pricehunter.cli collect --source acme
  python -m pricehunter.cli replay --source acme --parser-version acme-parser-v2
  python -m pricehunter.cli backfill --source acme --archive-date 2026-11-14
  python -m pricehunter.cli dashboard [--port 8090]
  python -m pricehunter.cli status
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

from .dashboard_server import serve_dashboard
from .demo import run_demo
from .fixtures.server import serve as serve_fixtures
from .pipeline import Collector
from .replay import replay_source
from .sources import default_sources
from .storage import Storage
from .timeutil import Clock

DATA_DIR = os.environ.get("PRICEHUNTER_DATA", "./data")
FIXTURE_URL = os.environ.get("PRICEHUNTER_FIXTURE_URL", "http://127.0.0.1:8088")


def open_storage(data_dir: str) -> Storage:
    return Storage(os.path.join(data_dir, "pricehunter.db"),
                   os.path.join(data_dir, "snapshots"))


def cmd_init_db(args) -> None:
    st = open_storage(args.data_dir)
    for spec in default_sources(FIXTURE_URL):
        from .sources import as_dict
        st.upsert_source(as_dict(spec))
    print("initialized:", args.data_dir)
    st.close()


def cmd_serve_fixtures(args) -> None:
    server = serve_fixtures(args.host, args.port)
    print(f"fixture server listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def cmd_run_demo(args) -> None:
    st = open_storage(args.data_dir)
    clock = Clock()
    run_demo(st, base_url=args.fixture_url, clock=clock)
    st.close()


def cmd_collect(args) -> None:
    st = open_storage(args.data_dir)
    clock = Clock()
    specs = {s.source_id: s for s in default_sources(args.fixture_url)}
    if args.source not in specs:
        sys.exit(f"unknown source: {args.source}")
    row = st.get_source(args.source)
    spec = replace(specs[args.source], parser_version=row["parser_version"])
    report = Collector(st, clock=clock).run_source(spec)
    print(json.dumps({
        "run_id": report.run_id, "source": report.source_id,
        "status": report.status.value, "pages_ok": report.pages_ok,
        "pages_failed": report.pages_failed, "retries": report.retries,
        "items": report.items_ingested, "note": report.note,
        "failures": report.failures,
    }, ensure_ascii=False, indent=2))
    st.close()


def cmd_replay(args) -> None:
    st = open_storage(args.data_dir)
    clock = Clock()
    result = replay_source(st, args.source, args.parser_version, clock.iso())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    st.close()


def cmd_backfill(args) -> None:
    from .backfill import backfill_archive
    st = open_storage(args.data_dir)
    clock = Clock()
    specs = {s.source_id: s for s in default_sources(args.fixture_url)}
    row = st.get_source(args.source)
    spec = replace(specs[args.source], parser_version=row["parser_version"])
    result = backfill_archive(st, spec, archive_date=args.archive_date, clock=clock)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    st.close()


def cmd_dashboard(args) -> None:
    st = open_storage(args.data_dir)
    clock = Clock()
    server = serve_dashboard(st, clock, args.fixture_url, args.host, args.port)
    print(f"dashboard on http://{args.host}:{args.port} (fixture={args.fixture_url})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def cmd_status(args) -> None:
    st = open_storage(args.data_dir)
    out = {"health": [dict(r) for r in st.health()],
           "active_alerts": [dict(r) for r in st.active_alerts()],
           "active_promos": [dict(r) for r in st.active_promos()],
           "revisions": [dict(r) for r in st.list_revisions()],
           "current_prices": {}}
    for s in st.list_sources():
        out["current_prices"][s["source_id"]] = [
            dict(r) for r in st.latest_prices_by_source(s["source_id"])]
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    st.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="商品价格采集工具（本地夹具版）")
    p.add_argument("--data-dir", default=DATA_DIR)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init-db"); sp.set_defaults(func=cmd_init_db)

    sp = sub.add_parser("serve-fixtures")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8088)
    sp.set_defaults(func=cmd_serve_fixtures)

    sp = sub.add_parser("run-demo")
    sp.add_argument("--fixture-url", default=FIXTURE_URL)
    sp.set_defaults(func=cmd_run_demo)

    sp = sub.add_parser("collect")
    sp.add_argument("--source", required=True)
    sp.add_argument("--fixture-url", default=FIXTURE_URL)
    sp.set_defaults(func=cmd_collect)

    sp = sub.add_parser("replay")
    sp.add_argument("--source", required=True)
    sp.add_argument("--parser-version", required=True)
    sp.set_defaults(func=cmd_replay)

    sp = sub.add_parser("backfill")
    sp.add_argument("--source", required=True)
    sp.add_argument("--archive-date", required=True)
    sp.add_argument("--fixture-url", default=FIXTURE_URL)
    sp.set_defaults(func=cmd_backfill)

    sp = sub.add_parser("dashboard")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8090)
    sp.add_argument("--fixture-url", default=FIXTURE_URL)
    sp.set_defaults(func=cmd_dashboard)

    sp = sub.add_parser("status"); sp.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
