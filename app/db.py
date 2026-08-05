"""SQLite เก็บสถานะ/ประวัติการรัน — runs, logs, completions (กันคีย์ซ้ำ)

completions คือหัวใจด้านความปลอดภัย: 1 แถว = AN นั้นถูกคีย์ยาสำเร็จแล้ว
การเช็คซ้ำใช้ทั้ง "วันเดียวกัน" และ "ภายใน N ชั่วโมงล่าสุด" เพื่อกันกรณีรันคร่อมเที่ยงคืน
"""
import contextlib
import socket
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR

DB_PATH = DATA_DIR / "ipdaddchart.db"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return ""


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextlib.contextmanager
def _tx():
    """เปิด connection → commit/rollback → ปิดเสมอ (sqlite3 'with conn' commit อย่างเดียว ไม่ปิดให้)"""
    conn = get_conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _tx() as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # ตั้งครั้งเดียวพอ ค่าอยู่ในไฟล์ถาวร
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                source TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                operator TEXT,
                host TEXT,
                total INTEGER NOT NULL DEFAULT 0,
                ok_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                skip_count INTEGER NOT NULL DEFAULT 0,
                cancel INTEGER NOT NULL DEFAULT 0,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                an TEXT,
                hn TEXT,
                ptname TEXT,
                step TEXT,
                status TEXT,
                message TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_logs_run ON logs(run_id, id);
            CREATE TABLE IF NOT EXISTS completions (
                day TEXT NOT NULL,
                an TEXT NOT NULL,
                hn TEXT,
                run_id INTEGER,
                host TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (day, an)
            );
            CREATE INDEX IF NOT EXISTS idx_completions_created ON completions(created_at);
            """
        )
        _migrate(conn)


def _migrate(conn) -> None:
    """เพิ่มคอลัมน์ที่มาทีหลังให้ฐานข้อมูลเดิมที่สร้างไว้ก่อนแล้ว"""
    for table, column, ddl in (
        ("runs", "host", "ALTER TABLE runs ADD COLUMN host TEXT"),
        ("completions", "host", "ALTER TABLE completions ADD COLUMN host TEXT"),
        ("completions", "an_norm", "ALTER TABLE completions ADD COLUMN an_norm TEXT"),
        ("completions", "hn_norm", "ALTER TABLE completions ADD COLUMN hn_norm TEXT"),
        ("completions", "status", "ALTER TABLE completions ADD COLUMN status TEXT DEFAULT 'done'"),
    ):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_completions_an ON completions(an_norm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_completions_hn ON completions(hn_norm)")


def sweep_stale_runs() -> None:
    """ปิดงานที่ค้างสถานะ running จากโปรเซสก่อนหน้า — เรียกเฉพาะตอนถือ single-instance lock เท่านั้น
    (ถ้าเรียกมั่ว โปรเซสที่สองจะไปทับสถานะงานที่กำลังรันจริงอยู่)"""
    with _tx() as conn:
        conn.execute(
            "UPDATE runs SET status='error', finished_at=?, "
            "message=COALESCE(message,'') || ' [โปรเซสถูกปิดกลางคัน — ตรวจ HosXP ว่ามีคนไข้ค้างหรือไม่]' "
            "WHERE status='running'",
            (_now(),),
        )


def create_run(source: str, total: int, dry_run: bool, operator: str) -> int:
    with _tx() as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, status, source, dry_run, operator, host, total) "
            "VALUES (?,?,?,?,?,?,?)",
            (_now(), "running", source, 1 if dry_run else 0, operator, hostname(), total),
        )
        return cur.lastrowid


def finish_run(run_id: int, status: str, message: str = "") -> None:
    with _tx() as conn:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, message=? WHERE id=?",
            (status, _now(), message, run_id),
        )


def update_counts(run_id: int, ok: int, fail: int, skip: int) -> None:
    with _tx() as conn:
        conn.execute(
            "UPDATE runs SET ok_count=?, fail_count=?, skip_count=? WHERE id=?",
            (ok, fail, skip, run_id),
        )


def request_cancel(run_id: int) -> None:
    with _tx() as conn:
        conn.execute("UPDATE runs SET cancel=1 WHERE id=?", (run_id,))


def is_cancel_requested(run_id: int) -> bool:
    with _tx() as conn:
        row = conn.execute("SELECT cancel FROM runs WHERE id=?", (run_id,)).fetchone()
        return bool(row and row["cancel"])


def add_log(run_id: int, an: str, hn: str, ptname: str, step: str, status: str, message: str) -> None:
    with _tx() as conn:
        conn.execute(
            "INSERT INTO logs (run_id, an, hn, ptname, step, status, message, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, an, hn, ptname, step, status, message, _now()),
        )


def get_run(run_id: int):
    with _tx() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def get_runs(limit: int = 50, offset: int = 0):
    with _tx() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]


def get_logs(run_id: int, after_id: int = 0, limit: int = 500):
    with _tx() as conn:
        rows = conn.execute(
            "SELECT * FROM logs WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
            (run_id, after_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_done(
    day: str, key: str, an_norm: str, hn_norm: str, hn: str, run_id: int, status: str = "done"
) -> None:
    """status='done' = บันทึกยาสำเร็จแน่นอน, status='unknown' = กดบันทึกไปแล้วแต่ไม่รู้ผล (ต้องให้คนตรวจ)"""
    with _tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO completions "
            "(day, an, hn, an_norm, hn_norm, status, run_id, host, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (day, key, hn, an_norm, hn_norm, status, run_id, hostname(), _now()),
        )


def done_index(day: str = "", window_hours: int = 6) -> dict:
    """ดัชนีคนที่แตะไปแล้ว = ของวันที่ระบุ รวมกับภายใน window_hours ล่าสุด

    window_hours มีไว้กันกรณีเดียว: รันรอบหนึ่งจบ 23:59 แล้วกดรันอีกรอบ 00:05
    (คนละ 'วัน' แต่ห่างกันไม่กี่นาที ไม่ควรคีย์ซ้ำ)
    **ห้ามตั้งยาวเกินจำเป็น** เพราะยาต้องคีย์ใหม่ทุกวัน ถ้าหน้าต่างยาวคร่อมข้ามคืน
    คนที่คีย์ไปเมื่อเย็นวานจะถูกข้ามในรอบเช้าวันนี้ = คนไข้ไม่ได้ยา
    (การรันคร่อมเที่ยงคืนไม่ต้องพึ่งค่านี้ เพราะ run_day ถูกตรึงตั้งแต่เริ่มงานอยู่แล้ว)

    แยกเป็น done = บันทึกสำเร็จ และ unknown = ไม่รู้ผล ต้องให้คนตรวจ"""
    day = day or today_str()
    cutoff = (datetime.now() - timedelta(hours=int(window_hours))).strftime("%Y-%m-%d %H:%M:%S")
    idx = {"an": set(), "hn": set(), "unknown_an": set(), "unknown_hn": set(), "keys": set()}
    with _tx() as conn:
        rows = conn.execute(
            "SELECT an, an_norm, hn_norm, status FROM completions WHERE day=? OR created_at>=?",
            (day, cutoff),
        ).fetchall()
    for r in rows:
        unknown = (r["status"] or "done") == "unknown"
        idx["keys"].add(r["an"])
        if r["an_norm"]:
            idx["unknown_an" if unknown else "an"].add(r["an_norm"])
        if r["hn_norm"]:
            idx["unknown_hn" if unknown else "hn"].add(r["hn_norm"])
    return idx


def clear_review(an_norm: str, hn_norm: str, day: str = "", window_hours: int = 6) -> int:
    """ปลดสถานะ 'ไม่รู้ผลการบันทึก' ของคนไข้รายนี้ หลังคนไปตรวจใน HosXP มาแล้ว
    ลบเฉพาะแถว status='unknown' — แถวที่บันทึกสำเร็จจริง (done) ห้ามแตะ ไม่งั้นจะกลายเป็นคีย์ซ้ำ"""
    day = day or today_str()
    cutoff = (datetime.now() - timedelta(hours=int(window_hours))).strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as conn:
        cur = conn.execute(
            "DELETE FROM completions WHERE status='unknown' "
            "AND ((an_norm<>'' AND an_norm=?) OR (hn_norm<>'' AND hn_norm=?)) "
            "AND (day=? OR created_at>=?)",
            (an_norm or "\x00", hn_norm or "\x00", day, cutoff),
        )
        return cur.rowcount


def confirm_review(an_norm: str, hn_norm: str, day: str = "", window_hours: int = 6) -> int:
    """ยืนยันว่า 'ยาเข้าไปแล้วจริง' หลังคนไปตรวจใน HosXP มาแล้ว

    ตรงข้ามกับ clear_review() — ใช้เมื่อตรวจแล้วพบว่ายาถูกบันทึกเรียบร้อย
    เปลี่ยนสถานะเป็น done เพื่อกันคีย์ซ้ำต่อไป (ถ้าเผลอไปกด 'ให้ลองใหม่' คนไข้จะได้ยาซ้ำ)
    """
    day = day or today_str()
    cutoff = (datetime.now() - timedelta(hours=int(window_hours))).strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as conn:
        cur = conn.execute(
            "UPDATE completions SET status='done' WHERE status='unknown' "
            "AND ((an_norm<>'' AND an_norm=?) OR (hn_norm<>'' AND hn_norm=?)) "
            "AND (day=? OR created_at>=?)",
            (an_norm or "\x00", hn_norm or "\x00", day, cutoff),
        )
        return cur.rowcount


def completion_info(an_norm: str, hn_norm: str, window_hours: int = 6):
    """ข้อมูลว่าคนไข้รายนี้ถูกแตะไปแล้วเมื่อไหร่/เครื่องไหน/ผลเป็นอย่างไร (ใช้แสดงในหน้าเว็บ)"""
    cutoff = (datetime.now() - timedelta(hours=int(window_hours))).strftime("%Y-%m-%d %H:%M:%S")
    with _tx() as conn:
        row = conn.execute(
            "SELECT * FROM completions "
            "WHERE ((an_norm<>'' AND an_norm=?) OR (hn_norm<>'' AND hn_norm=?)) "
            "AND (day=? OR created_at>=?) ORDER BY created_at DESC LIMIT 1",
            (an_norm or "\x00", hn_norm or "\x00", today_str(), cutoff),
        ).fetchone()
        return dict(row) if row else None
