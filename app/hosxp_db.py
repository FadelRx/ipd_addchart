"""ดึงรายชื่อผู้ป่วยที่ยัง admit อยู่จากฐานข้อมูล HOSxP (MariaDB) — อ่านอย่างเดียว SELECT เท่านั้น"""
import pymysql
from pymysql.cursors import DictCursor

# หมายเหตุ:
# - dchdate ของ HOSxP อาจเป็น NULL หรือ zero-date '0000-00-00' ทั้งคู่แปลว่ายังไม่จำหน่าย
# - แต่ "ยังไม่จำหน่าย" ไม่ได้แปลว่ายังนอนอยู่จริง — ในทางปฏิบัติมีแถวค้างจำนวนมากที่ไม่เคย
#   ถูกปิด (ผู้ป่วยกลับบ้านไปแล้วแต่ไม่ได้คีย์จำหน่าย) แถวพวกนี้จะโผล่มาพร้อมวันนอนหลักพันวัน
#   ไม่มี ward และไม่มีชื่อ ถ้าปล่อยผ่านโรบอทจะไปคีย์ยาให้คนที่ไม่ได้นอนอยู่แล้ว
# - has_newer_admit: ถ้า HN เดียวกันมีการ admit ที่เริ่มทีหลัง แถวนี้ต้องเป็นซากที่ไม่ได้ปิดแน่นอน
#   เพราะคนคนหนึ่งนอนโรงพยาบาลพร้อมกันสองครั้งไม่ได้
SQL_ADMITTED = """
SELECT
    i.an,
    i.hn,
    i.ward,
    IFNULL(w.name, '') AS ward_name,
    CONCAT(IFNULL(p.pname,''), IFNULL(p.fname,''), ' ', IFNULL(p.lname,'')) AS ptname,
    IFNULL(p.fname, '') AS fname,
    IFNULL(p.lname, '') AS lname,
    DATE_FORMAT(i.regdate, '%%Y-%%m-%%d') AS regdate,
    DATEDIFF(%(today)s, i.regdate) AS los_days,
    EXISTS(
        SELECT 1 FROM ipt i2
        WHERE i2.hn = i.hn AND i2.an <> i.an AND i2.regdate > i.regdate
    ) AS has_newer_admit
FROM ipt i
LEFT JOIN ward w ON w.ward = i.ward
LEFT JOIN patient p ON p.hn = i.hn
WHERE (i.dchdate IS NULL OR i.dchdate = '0000-00-00')
ORDER BY ward_name, i.ward, i.an
"""

# ค่าเริ่มต้นของตัวกรอง "admit อยู่จริง" — ปรับได้ที่ settings.json → hosxp_db.filters
DEFAULT_FILTERS = {
    "max_los_days": 365,          # นอนเกินเท่านี้ถือว่าเป็นแถวค้างที่ไม่ได้ปิด
    "require_ward": True,         # ไม่มี ward = ไม่ได้นอนอยู่จริง
    "require_name": True,         # ไม่มีชื่อ-สกุล = ข้อมูลไม่ครบ ไม่ควรให้โรบอทคีย์
    "drop_superseded_admits": True,  # มี admit ใหม่กว่าแล้ว = แถวนี้เป็นซาก
}

# เหตุผลที่ถูกตัดออก เรียงตามลำดับที่ตรวจ — ใช้แสดงให้ผู้ใช้เห็นว่าตัดอะไรไปบ้าง
DROP_REASON_LABELS = {
    "no_ward": "ไม่มี ward",
    "no_name": "ไม่มีชื่อ-สกุล",
    "los_over": "วันนอนเกินกำหนด",
    "superseded": "มี admit ใหม่กว่าแล้ว",
    "bad_regdate": "วันที่ admit ไม่ถูกต้อง",
}


# นับ "รายการยา continue" ของแต่ละ AN — คือสิ่งที่ปุ่ม Add Chart F5 จะยกมาให้
# ถ้านับได้ 0 แปลว่ากดไปก็จะเจอ "No Item" (ไม่มีอะไรถูกบันทึก)
#
# สองจุดที่ต้องระวัง ไม่งั้นเลขจะหลอก:
# 1. opitemrece เก็บยาปนกับเวชภัณฑ์/บริการ (Echocardiogram, EKG monitor, ค่าบริการห้อง ICU)
#    ของพวกนี้ icode ขึ้นต้นด้วย 3 และไม่มีใน drugitems — ต้อง JOIN drugitems เพื่อเอาเฉพาะยา
# 2. ห้ามใช้ MAX(rxdate) เฉย ๆ เป็นวันของชาร์ต เพราะคนไข้จำนวนมากมีแถว "ค่าบริการรายวัน"
#    ของวันนี้แล้วแต่ยังไม่มีชาร์ตยาของวันนี้ (นั่นแหละคือเหตุผลที่ต้องรันโปรแกรมนี้)
#    ถ้านับจากวันล่าสุดจะได้ 0 ทั้งที่จริง ๆ มียา continue ค้างอยู่จากเมื่อวาน
#    จึงต้องหา "วันล่าสุดที่มีรายการยา" แล้วนับยาของวันนั้น
SQL_DRUG_COUNTS = """
SELECT
    o.an,
    DATE_FORMAT(o.rxdate, '%%Y-%%m-%%d') AS rx_date,
    COUNT(*) AS drug_count
FROM opitemrece o
JOIN drugitems d ON d.icode = o.icode
JOIN (
    SELECT o2.an, MAX(o2.rxdate) AS mx
    FROM opitemrece o2
    JOIN drugitems d2 ON d2.icode = o2.icode
    WHERE o2.an IN %(ans)s AND o2.rxdate BETWEEN %(since)s AND %(today)s
    GROUP BY o2.an
) t ON t.an = o.an AND t.mx = o.rxdate
WHERE o.an IN %(ans)s AND o.rxdate BETWEEN %(since)s AND %(today)s
GROUP BY o.an, o.rxdate
"""

AN_CHUNK = 500  # กัน SQL ยาวเกินไปเมื่อโรงพยาบาลใหญ่มีคนไข้ในหลักพัน


def _connect(cfg: dict):
    return pymysql.connect(
        host=cfg.get("host", ""),
        port=int(cfg.get("port", 3306)),
        user=cfg.get("user", ""),
        password=cfg.get("password", ""),
        database=cfg.get("database", "hos"),
        charset=cfg.get("charset", "tis620"),
        connect_timeout=int(cfg.get("connect_timeout", 5)),
        read_timeout=int(cfg.get("read_timeout", 60)),
        cursorclass=DictCursor,
    )


def _today_str() -> str:
    from datetime import datetime

    # คำนวณวันที่ฝั่ง Python — เครื่องที่ตั้ง locale ไทยอาจทำให้ CURDATE() ของ DB เพี้ยนเป็น พ.ศ.
    return datetime.now().strftime("%Y-%m-%d")


def fetch_drug_counts(cfg: dict, ans: list, today: str = "") -> dict:
    """คืน {an: {drug_count, item_count, rx_date}} — AN ที่ไม่มีรายการเลยจะไม่มีคีย์ในผลลัพธ์"""
    ans = [str(a).strip() for a in ans if str(a or "").strip()]
    if not ans:
        return {}
    today = today or _today_str()
    days = int(cfg.get("drug_lookback_days", 30) or 30)

    from datetime import datetime, timedelta

    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")

    out = {}
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            for i in range(0, len(ans), AN_CHUNK):
                chunk = tuple(ans[i : i + AN_CHUNK])
                cur.execute(SQL_DRUG_COUNTS, {"ans": chunk, "since": since, "today": today})
                for r in cur.fetchall():
                    out[str(r.get("an") or "").strip()] = {
                        "drug_count": int(r.get("drug_count") or 0),
                        "rx_date": str(r.get("rx_date") or ""),
                    }
    finally:
        conn.close()
    return out


def attach_drug_counts(cfg: dict, rows: list, today: str = "") -> str:
    """เติม drug_count/drug_rx_date ลงในแต่ละแถว — คืนข้อความ error ถ้าดึงไม่ได้

    ตั้งใจไม่ให้ throw: คอลัมน์นี้เป็นข้อมูลประกอบการตัดสินใจ ถ้าดึงไม่ได้ก็ยังต้องคีย์ยาได้ตามปกติ
    """
    targets = [r for r in rows if r.get("an")]
    if not targets:
        return ""
    try:
        counts = fetch_drug_counts(cfg, [r["an"] for r in targets], today)
    except Exception as e:
        return str(e)
    for r in targets:
        hit = counts.get(str(r["an"]).strip())
        # ไม่มีรายการยาเลยในช่วงที่ดู = ไม่มียา continue ให้ยกมา (จะเจอ No Item)
        r["drug_count"] = hit["drug_count"] if hit else 0
        r["drug_rx_date"] = hit["rx_date"] if hit else ""
    return ""


def _filters(cfg: dict) -> dict:
    f = dict(DEFAULT_FILTERS)
    f.update(cfg.get("filters") or {})
    return f


def _drop_reason(r: dict, f: dict) -> str:
    """คืนเหตุผลที่แถวนี้ไม่ใช่คนที่ admit อยู่จริง — คืนค่าว่างถ้าผ่าน"""
    if f.get("require_ward", True) and not str(r.get("ward") or "").strip():
        return "no_ward"
    if f.get("require_name", True) and not (
        str(r.get("fname") or "").strip() or str(r.get("lname") or "").strip()
    ):
        return "no_name"
    regdate = str(r.get("regdate") or "").strip()
    if not regdate or regdate.startswith("0000"):
        return "bad_regdate"
    los = r.get("los_days")
    max_los = int(f.get("max_los_days", 0) or 0)
    if max_los > 0 and los is not None and int(los) > max_los:
        return "los_over"
    if f.get("drop_superseded_admits", True) and int(r.get("has_newer_admit") or 0):
        return "superseded"
    return ""


def fetch_admitted(cfg: dict, today: str = "", with_stats: bool = False):
    """คืนรายชื่อผู้ป่วยที่ admit อยู่จริง (กรองแถวค้างออกแล้ว)

    with_stats=True จะคืน (rows, stats) โดย stats บอกว่าตัดอะไรออกไปกี่คน
    """
    today = today or _today_str()
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_ADMITTED, {"today": today})
            rows = cur.fetchall()
    finally:
        conn.close()

    f = _filters(cfg)
    out = []
    dropped = {}
    for r in rows:
        an = str(r.get("an") or "").strip()
        if not an:
            continue
        reason = _drop_reason(r, f)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        los = r.get("los_days")
        ward = str(r.get("ward") or "").strip()
        ward_name = str(r.get("ward_name") or "").strip()
        out.append(
            {
                "an": an,
                "hn": str(r.get("hn") or "").strip(),
                "ward": ward,
                # ward ที่ไม่มีชื่อในตาราง ward ยังต้องจัดกลุ่มได้ ใช้รหัส ward แทนชื่อ
                "ward_name": ward_name or ward,
                "ptname": " ".join(str(r.get("ptname") or "").split()),
                "regdate": str(r.get("regdate") or "").strip(),
                "los_days": int(los) if los is not None else "",
            }
        )

    if not with_stats:
        return out
    stats = {
        "total_raw": len(rows),
        "kept": len(out),
        "dropped_total": sum(dropped.values()),
        "dropped": {DROP_REASON_LABELS.get(k, k): v for k, v in dropped.items()},
        "max_los_days": int(f.get("max_los_days", 0) or 0),
    }
    return out, stats
