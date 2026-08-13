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
    IFNULL(b.bedno, '') AS bedno,
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
-- เลขเตียงปัจจุบัน: แถวใน iptadm ที่ยังไม่ย้ายออก (บางตึกไม่ได้กำหนดเตียง จะได้ค่าว่าง)
LEFT JOIN iptadm b ON b.an = i.an AND (b.outdate IS NULL OR b.outdate = '0000-00-00')
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
# ต้องอ่านจาก medplan_ipd = "แผนการใช้ยาของผู้ป่วยใน" ซึ่งเป็นตัวเดียวกับที่ Add Chart F5 ยกมา
#   orderstatus = 'C' (Continue) คือยาต่อเนื่องที่ถูกยกมาทุกวัน
#   orderstatus = 'S' (Stat) คือยาสั่งครั้งเดียว ไม่ถูกยกมา
#   offdate     = วันที่สั่งหยุดยา ถ้ายังไม่ถึงวันนั้น (หรือว่าง) แปลว่ายังใช้อยู่
#
# เคยใช้ opitemrece แล้วเลขเพี้ยนทั้งขึ้นและลง (แก้ 10 ส.ค. 2569):
#   opitemrece คือรายการ "จ่ายยา/คิดเงิน" ไม่ใช่แผนการใช้ยา คนไข้ที่มีหลายใบสั่งในวันเดียว
#   จะถูกนับซ้ำ (มี 3 แสดง 7) ส่วนคนที่ชาร์ตล่าสุดมีแค่บางรายการก็นับขาด (มี 3 แสดง 1)
#
# พิสูจน์แล้วกับ AN 690017875: medplan_ipd orderstatus='C' ได้ 5 ตัว
# (MetFORMIN, GLIPIZIDE, Lasix, Aldactone, Carvedilol) ตรงกับที่เห็นบนหน้าจอ HosXP เป๊ะ
# ส่วนแถว 'S' (NSS, LEVOPHED) ไม่ถูกยกมาจริงตามที่เห็นบนหน้าจอ
SQL_DRUG_COUNTS = """
SELECT
    m.an,
    COUNT(*) AS drug_count,
    DATE_FORMAT(MAX(m.orderdate), '%%Y-%%m-%%d') AS rx_date
FROM medplan_ipd m
WHERE m.an IN %(ans)s
  AND m.orderstatus = 'C'
  AND (m.offdate IS NULL OR m.offdate = '0000-00-00' OR DATE(m.offdate) > %(today)s)
GROUP BY m.an
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
    # หมายเหตุ: ไม่ต้องใช้ช่วงวันย้อนหลังแล้ว เพราะ medplan_ipd เก็บ "แผนที่ยังใช้อยู่" ของ AN นั้นตรง ๆ
    # (ต่างจาก opitemrece เดิมที่ต้องไล่หาวันล่าสุดที่มีรายการยา)
    out = {}
    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            for i in range(0, len(ans), AN_CHUNK):
                chunk = tuple(ans[i : i + AN_CHUNK])
                cur.execute(SQL_DRUG_COUNTS, {"ans": chunk, "today": today})
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


def _bed_sort_key(bedno) -> str:
    """คีย์สำหรับเรียงเลขเตียงให้ถูกตามสายตาคน

    เลขเตียงของโรงพยาบาลปนตัวอักษรกับตัวเลข (BH01, 6613, NICU12, MICU01, PD28)
    ถ้าเรียงแบบข้อความล้วน จะได้ NICU12 มาก่อน NICU2 ซึ่งผิด
    จึงเติมศูนย์หน้าตัวเลขให้ยาวเท่ากันก่อนเรียง และดันเตียงที่ว่างไปท้ายสุด
    """
    import re as _re

    s = str(bedno or "").strip()
    if not s:
        return "￿"  # ไม่มีเลขเตียง = ไปท้ายกลุ่ม
    return "".join(
        part.rjust(8, "0") if part.isdigit() else part.upper()
        for part in _re.split(r"(\d+)", s)
    )


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
                "bedno": str(r.get("bedno") or "").strip(),
                "bed_sort": _bed_sort_key(r.get("bedno")),
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
