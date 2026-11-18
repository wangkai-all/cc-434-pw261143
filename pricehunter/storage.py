"""SQLite 双时态存储。

设计要点：
- snapshots 保存每次抓取的原始响应（原始快照不可变）。
- parse_attempts 记录用哪个 parser_version 解析了哪份快照、结果如何。
- price_versions 是商品价格的版本链（修订链），同一观测日可被多次修订，
  通过 superseded_by 串联；valid_from/valid_to 是业务有效区间（观测日期），
  recorded_at 是系统记录时间（双时态）。
- promo_hits / alerts 的每次重算结果作为新行保存，旧行置 superseded，
  保证历史判定可追溯。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .model import DerivedState, RunStatus, VersionState

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    source_id        TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    base_url         TEXT NOT NULL,
    parser_key       TEXT NOT NULL,
    parser_version   TEXT NOT NULL,
    promo_threshold  REAL NOT NULL DEFAULT 0.15,
    anomaly_z        REAL NOT NULL DEFAULT 2.5,
    active           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL REFERENCES sources(source_id),
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    status           TEXT NOT NULL,
    pages_ok         INTEGER NOT NULL DEFAULT 0,
    pages_failed     INTEGER NOT NULL DEFAULT 0,
    rate_limited     INTEGER NOT NULL DEFAULT 0,
    retries          INTEGER NOT NULL DEFAULT 0,
    note             TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id      TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    source_id        TEXT NOT NULL,
    url              TEXT NOT NULL,
    page_no          INTEGER NOT NULL,
    http_status      INTEGER,
    outcome          TEXT NOT NULL,
    content_hash     TEXT,
    content_path     TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    retry_no         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS parse_attempts (
    attempt_id       TEXT PRIMARY KEY,
    snapshot_id      TEXT NOT NULL REFERENCES snapshots(snapshot_id),
    source_id        TEXT NOT NULL,
    parser_key       TEXT NOT NULL,
    parser_version   TEXT NOT NULL,
    ok               INTEGER NOT NULL,
    item_count       INTEGER NOT NULL DEFAULT 0,
    error_kind       TEXT,
    error_message    TEXT,
    sample_path      TEXT,
    parsed_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    sku              TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    first_seen       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_versions (
    version_id       TEXT PRIMARY KEY,
    sku              TEXT NOT NULL REFERENCES products(sku),
    source_id        TEXT NOT NULL,
    observed_date    TEXT NOT NULL,           -- 业务时间（有效区间维度）
    price_cents      INTEGER NOT NULL,
    list_price_cents INTEGER,
    currency         TEXT NOT NULL,
    promo_code       TEXT,
    parser_version   TEXT NOT NULL,
    snapshot_id      TEXT,                    -- 关联原始快照；修订/测试场景可为空
    recorded_at      TEXT NOT NULL,           -- 系统时间
    state            TEXT NOT NULL,
    superseded_by    TEXT,
    revision_no      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(sku, observed_date, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_pv_sku_date ON price_versions(sku, observed_date);
CREATE INDEX IF NOT EXISTS idx_pv_source ON price_versions(source_id, observed_date);

CREATE TABLE IF NOT EXISTS promo_hits (
    hit_id           TEXT PRIMARY KEY,
    sku              TEXT NOT NULL,
    source_id        TEXT NOT NULL,
    observed_date    TEXT NOT NULL,
    version_id       TEXT NOT NULL REFERENCES price_versions(version_id),
    discount_rate    REAL NOT NULL,
    promo_code       TEXT,
    state            TEXT NOT NULL,
    superseded_by    TEXT,
    computed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_promo_sku ON promo_hits(sku, observed_date);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id         TEXT PRIMARY KEY,
    sku              TEXT NOT NULL,
    source_id        TEXT NOT NULL,
    observed_date    TEXT NOT NULL,
    kind             TEXT NOT NULL,
    severity         TEXT NOT NULL,
    message          TEXT NOT NULL,
    version_id       TEXT REFERENCES price_versions(version_id),
    state            TEXT NOT NULL,
    superseded_by    TEXT,
    computed_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_sku ON alerts(sku, observed_date);

CREATE TABLE IF NOT EXISTS revisions (
    revision_id      TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL,
    sku              TEXT NOT NULL,
    observed_date    TEXT NOT NULL,
    old_version_id   TEXT NOT NULL,
    new_version_id   TEXT NOT NULL,
    reason           TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replays (
    replay_id        TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL,
    parser_key       TEXT NOT NULL,
    parser_version   TEXT NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    snapshots_total  INTEGER NOT NULL DEFAULT 0,
    snapshots_failed INTEGER NOT NULL DEFAULT 0,
    note             TEXT
);

CREATE TABLE IF NOT EXISTS replay_diffs (
    diff_id          TEXT PRIMARY KEY,
    replay_id        TEXT NOT NULL REFERENCES replays(replay_id),
    snapshot_id      TEXT NOT NULL,
    sku              TEXT NOT NULL,
    field            TEXT NOT NULL,
    old_value        TEXT,
    new_value        TEXT,
    diff_type        TEXT NOT NULL
);
"""


class LockingConnection:
    """对 sqlite3.Connection 的薄包装：所有 SQL 入口持同一把锁，
    让 ThreadingHTTPServer 多线程访问安全（可重入，事务内多语句不死锁）。"""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name):
        # row_factory 等属性透传
        return getattr(self._conn, name)

    def __setattr__(self, name, value) -> None:
        if name in ("_conn", "_lock"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


class Storage:
    def __init__(self, db_path: str | Path, snapshot_dir: str | Path) -> None:
        self.db_path = str(db_path)
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：仪表盘 ThreadingHTTPServer 跨线程复用连接；
        # LockingConnection 用可重入锁串行化所有 SQL，保证多线程下 SQLite 安全。
        self._lock = threading.RLock()
        raw = sqlite3.connect(self.db_path, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self.conn = LockingConnection(raw, self._lock)
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------- sources ----------
    def upsert_source(self, src: dict) -> None:
        self.conn.execute(
            """INSERT INTO sources(source_id, name, base_url, parser_key, parser_version,
                                   promo_threshold, anomaly_z, active)
               VALUES (:source_id, :name, :base_url, :parser_key, :parser_version,
                       :promo_threshold, :anomaly_z, 1)
               ON CONFLICT(source_id) DO UPDATE SET
                 name=excluded.name, base_url=excluded.base_url,
                 parser_key=excluded.parser_key, parser_version=excluded.parser_version,
                 promo_threshold=excluded.promo_threshold, anomaly_z=excluded.anomaly_z""",
            src,
        )
        self.conn.commit()

    def get_source(self, source_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM sources WHERE source_id=?", (source_id,)
        ).fetchone()

    def list_sources(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()

    # ---------- runs / snapshots ----------
    def start_run(self, run_id: str, source_id: str, started_at: str) -> None:
        self.conn.execute(
            "INSERT INTO runs(run_id, source_id, started_at, status) VALUES (?,?,?,?)",
            (run_id, source_id, started_at, RunStatus.RUNNING.value),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, status: RunStatus, counters: dict, note: str | None,
                   finished_at: str) -> None:
        self.conn.execute(
            """UPDATE runs SET status=?, finished_at=?, pages_ok=?, pages_failed=?,
                               rate_limited=?, retries=?, note=?
               WHERE run_id=?""",
            (status.value, finished_at, counters.get("pages_ok", 0),
             counters.get("pages_failed", 0), counters.get("rate_limited", 0),
             counters.get("retries", 0), note, run_id),
        )
        self.conn.commit()

    def list_runs(self, source_id: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        if source_id:
            return self.conn.execute(
                "SELECT * FROM runs WHERE source_id=? ORDER BY started_at DESC LIMIT ?",
                (source_id, limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def save_snapshot(self, snap: dict, body: bytes) -> str:
        """原始快照落盘，路径入库；快照内容永不更新。"""
        path = self.snapshot_dir / f"{snap['snapshot_id']}.bin"
        path.write_bytes(body)
        self.conn.execute(
            """INSERT INTO snapshots(snapshot_id, run_id, source_id, url, page_no,
                                     http_status, outcome, content_hash, content_path,
                                     fetched_at, retry_no)
               VALUES (:snapshot_id, :run_id, :source_id, :url, :page_no, :http_status,
                       :outcome, :content_hash, :content_path, :fetched_at, :retry_no)""",
            {**snap, "content_path": str(path)},
        )
        self.conn.commit()
        return str(path)

    def list_snapshots(self, source_id: str, outcome: str | None = None) -> list[sqlite3.Row]:
        if outcome:
            return self.conn.execute(
                "SELECT * FROM snapshots WHERE source_id=? AND outcome=? ORDER BY fetched_at",
                (source_id, outcome),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM snapshots WHERE source_id=? ORDER BY fetched_at", (source_id,)
        ).fetchall()

    def get_snapshot(self, snapshot_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()

    def read_snapshot_body(self, snapshot_id: str) -> bytes:
        row = self.get_snapshot(snapshot_id)
        if row is None:
            raise KeyError(snapshot_id)
        return Path(row["content_path"]).read_bytes()

    # ---------- parse attempts ----------
    def record_parse_attempt(self, att: dict) -> None:
        self.conn.execute(
            """INSERT INTO parse_attempts(attempt_id, snapshot_id, source_id, parser_key,
                                          parser_version, ok, item_count, error_kind,
                                          error_message, sample_path, parsed_at)
               VALUES (:attempt_id, :snapshot_id, :source_id, :parser_key, :parser_version,
                       :ok, :item_count, :error_kind, :error_message, :sample_path, :parsed_at)""",
            att,
        )
        self.conn.commit()

    def list_parse_attempts(self, source_id: str | None = None, ok: int | None = None,
                            limit: int = 100) -> list[sqlite3.Row]:
        sql = "SELECT pa.*, s.url, s.fetched_at FROM parse_attempts pa JOIN snapshots s USING(snapshot_id) WHERE 1=1"
        args: list = []
        if source_id:
            sql += " AND pa.source_id=?"
            args.append(source_id)
        if ok is not None:
            sql += " AND pa.ok=?"
            args.append(ok)
        sql += " ORDER BY pa.parsed_at DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(sql, args).fetchall()

    def save_failure_sample(self, attempt_id: str, body: bytes, ext: str = "html") -> str:
        path = self.snapshot_dir / "failures" / f"{attempt_id}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return str(path)

    # ---------- products / price versions ----------
    def ensure_product(self, sku: str, name: str, now_iso: str) -> None:
        self.conn.execute(
            """INSERT INTO products(sku, name, first_seen) VALUES (?,?,?)
               ON CONFLICT(sku) DO UPDATE SET name=excluded.name""",
            (sku, name, now_iso),
        )

    def current_version(self, sku: str, observed_date: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT * FROM price_versions
               WHERE sku=? AND observed_date=? AND state=?
               ORDER BY revision_no DESC LIMIT 1""",
            (sku, observed_date, VersionState.CURRENT.value),
        ).fetchone()

    def insert_price_version(self, v: dict) -> None:
        self.conn.execute(
            """INSERT INTO price_versions(version_id, sku, source_id, observed_date,
                                          price_cents, list_price_cents, currency, promo_code,
                                          parser_version, snapshot_id, recorded_at, state,
                                          superseded_by, revision_no)
               VALUES (:version_id, :sku, :source_id, :observed_date, :price_cents,
                       :list_price_cents, :currency, :promo_code, :parser_version,
                       :snapshot_id, :recorded_at, :state, :superseded_by, :revision_no)""",
            {**v, "state": v.get("state", VersionState.CURRENT.value),
             "superseded_by": v.get("superseded_by")},
        )
        self.conn.commit()

    def supersede_version(self, version_id: str, new_version_id: str) -> None:
        self.conn.execute(
            "UPDATE price_versions SET state=?, superseded_by=? WHERE version_id=?",
            (VersionState.SUPERSEDED.value, new_version_id, version_id),
        )
        self.conn.commit()

    def add_revision_record(self, r: dict) -> None:
        self.conn.execute(
            """INSERT INTO revisions(revision_id, source_id, sku, observed_date,
                                     old_version_id, new_version_id, reason, created_at)
               VALUES (:revision_id, :source_id, :sku, :observed_date, :old_version_id,
                       :new_version_id, :reason, :created_at)""",
            r,
        )
        self.conn.commit()

    def price_chain(self, sku: str) -> list[sqlite3.Row]:
        """某商品完整价格链路：当前与历史版本按业务日期、修订号排列。"""
        return self.conn.execute(
            """SELECT * FROM price_versions WHERE sku=?
               ORDER BY observed_date, revision_no""",
            (sku,),
        ).fetchall()

    def latest_prices_by_source(self, source_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """WITH latest AS (
                 SELECT sku, MAX(observed_date) AS d FROM price_versions
                 WHERE source_id=? AND state=? GROUP BY sku
               )
               SELECT pv.* FROM price_versions pv JOIN latest l
                 ON pv.sku=l.sku AND pv.observed_date=l.d
               WHERE pv.state=?""",
            (source_id, VersionState.CURRENT.value, VersionState.CURRENT.value),
        ).fetchall()

    def list_skus(self) -> list[str]:
        return [r["sku"] for r in self.conn.execute(
            "SELECT DISTINCT sku FROM price_versions ORDER BY sku").fetchall()]

    # ---------- 促销 / 告警（重算时先作废旧行再写新行） ----------
    def supersede_derived_for(self, source_id: str, sku: str, observed_date: str) -> None:
        self.conn.execute(
            """UPDATE promo_hits SET state=?, superseded_by=?
               WHERE source_id=? AND sku=? AND observed_date=? AND state=?""",
            (DerivedState.SUPERSEDED.value, None, source_id, sku, observed_date,
             DerivedState.ACTIVE.value),
        )
        self.conn.execute(
            """UPDATE alerts SET state=?, superseded_by=?
               WHERE source_id=? AND sku=? AND observed_date=? AND state=?""",
            (DerivedState.SUPERSEDED.value, None, source_id, sku, observed_date,
             DerivedState.ACTIVE.value),
        )

    def add_promo_hit(self, h: dict) -> None:
        self.conn.execute(
            """INSERT INTO promo_hits(hit_id, sku, source_id, observed_date, version_id,
                                      discount_rate, promo_code, state, superseded_by, computed_at)
               VALUES (:hit_id, :sku, :source_id, :observed_date, :version_id, :discount_rate,
                       :promo_code, :state, :superseded_by, :computed_at)""",
            {**h, "state": DerivedState.ACTIVE.value, "superseded_by": None},
        )

    def add_alert(self, a: dict) -> None:
        self.conn.execute(
            """INSERT INTO alerts(alert_id, sku, source_id, observed_date, kind, severity,
                                  message, version_id, state, superseded_by, computed_at)
               VALUES (:alert_id, :sku, :source_id, :observed_date, :kind, :severity,
                       :message, :version_id, :state, :superseded_by, :computed_at)""",
            {**a, "state": DerivedState.ACTIVE.value, "superseded_by": None},
        )

    def active_promos(self, source_id: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM promo_hits WHERE state=?"
        args: list = [DerivedState.ACTIVE.value]
        if source_id:
            sql += " AND source_id=?"
            args.append(source_id)
        return self.conn.execute(sql + " ORDER BY observed_date DESC", args).fetchall()

    def active_alerts(self, source_id: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM alerts WHERE state=?"
        args: list = [DerivedState.ACTIVE.value]
        if source_id:
            sql += " AND source_id=?"
            args.append(source_id)
        return self.conn.execute(sql + " ORDER BY observed_date DESC, severity", args).fetchall()

    # ---------- replay ----------
    def create_replay(self, replay: dict) -> None:
        self.conn.execute(
            """INSERT INTO replays(replay_id, source_id, parser_key, parser_version,
                                   started_at, snapshots_total, snapshots_failed, note)
               VALUES (:replay_id, :source_id, :parser_key, :parser_version, :started_at,
                       :snapshots_total, :snapshots_failed, :note)""",
            replay,
        )
        self.conn.commit()

    def finish_replay(self, replay_id: str, finished_at: str, failed: int) -> None:
        self.conn.execute(
            "UPDATE replays SET finished_at=?, snapshots_failed=? WHERE replay_id=?",
            (finished_at, failed, replay_id),
        )
        self.conn.commit()

    def add_replay_diff(self, d: dict) -> None:
        self.conn.execute(
            """INSERT INTO replay_diffs(diff_id, replay_id, snapshot_id, sku, field,
                                        old_value, new_value, diff_type)
               VALUES (:diff_id, :replay_id, :snapshot_id, :sku, :field, :old_value,
                       :new_value, :diff_type)""",
            d,
        )

    def list_replays(self, source_id: str | None = None) -> list[sqlite3.Row]:
        if source_id:
            return self.conn.execute(
                "SELECT * FROM replays WHERE source_id=? ORDER BY started_at DESC",
                (source_id,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM replays ORDER BY started_at DESC").fetchall()

    def replay_diffs(self, replay_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM replay_diffs WHERE replay_id=? ORDER BY snapshot_id, sku, field",
            (replay_id,),
        ).fetchall()

    def list_revisions(self, source_id: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM revisions"
        args: list = []
        if source_id:
            sql += " WHERE source_id=?"
            args.append(source_id)
        sql += " ORDER BY created_at DESC"
        return self.conn.execute(sql, args).fetchall()

    # ---------- 健康度聚合 ----------
    def health(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT s.source_id, s.name, s.parser_version,
                   (SELECT COUNT(*) FROM runs r WHERE r.source_id=s.source_id) AS runs,
                   (SELECT COUNT(*) FROM runs r WHERE r.source_id=s.source_id
                      AND r.status='ok') AS runs_ok,
                   (SELECT COUNT(*) FROM snapshots sn WHERE sn.source_id=s.source_id) AS snaps,
                   (SELECT COUNT(*) FROM snapshots sn WHERE sn.source_id=s.source_id
                      AND sn.outcome='ok') AS snaps_ok,
                   (SELECT COUNT(*) FROM snapshots sn WHERE sn.source_id=s.source_id
                      AND sn.outcome='rate_limited') AS snaps_429,
                   (SELECT COUNT(*) FROM snapshots sn WHERE sn.source_id=s.source_id
                      AND sn.outcome='maintenance') AS snaps_maint,
                   (SELECT COUNT(*) FROM parse_attempts pa WHERE pa.source_id=s.source_id
                      AND pa.ok=0) AS parse_failures,
                   (SELECT MAX(fetched_at) FROM snapshots sn WHERE sn.source_id=s.source_id)
                      AS last_fetch
            FROM sources s ORDER BY s.source_id
            """
        ).fetchall()

    def vacuum_checkpoint(self) -> None:
        self.conn.commit()


def new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def hash_body(body: bytes) -> str:
    import hashlib

    return hashlib.sha256(body).hexdigest()[:16]
