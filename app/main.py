"""FastAPI — หน้าเว็บ + API ทั้งหมดของ IPDaddChart"""
import getpass
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .excel_io import parse_patients
from .hosxp_db import attach_drug_counts, fetch_admitted
from .patients import is_done, needs_review, norm_id
from .runner import manager
from .winutil import ELEVATION_HELP, is_elevated

# ไฟล์หน้าเว็บถูกฝังมาในตัว exe จึงต้องอ่านจาก RESOURCE_DIR ไม่ใช่ที่อยู่ของโค้ด
STATIC_DIR = config.RESOURCE_DIR / "app" / "static" if config.FROZEN else Path(__file__).resolve().parent / "static"
SINGLE_INSTANCE_PORT = 47770  # ใช้เป็นล็อกกันเปิดโปรแกรมซ้อนกันหลายโปรเซส
_lock_socket = None


def _acquire_single_instance() -> bool:
    """คืน True ถ้าเราเป็นโปรเซสเดียว — ใช้ตัดสินว่ามีสิทธิ์ล้างงานค้างสถานะ running หรือไม่"""
    global _lock_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.listen(1)
        _lock_socket = s
        return True
    except OSError:
        s.close()
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if _acquire_single_instance():
        # มีเราโปรเซสเดียว งานที่ค้าง running จึงเป็นซากของรอบก่อนแน่นอน
        db.sweep_stale_runs()
    yield
    # ปิดโปรแกรม: สั่งหยุดแล้วรอให้คนไข้คนปัจจุบันจบ กันถูกฆ่าคาระหว่างกด F9
    manager.shutdown(timeout=45)


app = FastAPI(title="IPDaddChart", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _dedup_hours() -> int:
    return int(config.load_settings().get("robot", {}).get("dedup_window_hours", 6))


def _patient_key() -> str:
    return config.load_settings().get("robot", {}).get("ipd_form", {}).get("patient_key", "hn")


# ---------- หน้าเว็บ ----------


@app.get("/")
def page_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/logs")
def page_logs():
    return FileResponse(STATIC_DIR / "logs.html")


@app.get("/settings")
def page_settings():
    return FileResponse(STATIC_DIR / "settings.html")


# ---------- รายชื่อผู้ป่วย ----------


def _annotate_done(patients: list) -> list:
    hours = _dedup_hours()
    index = db.done_index(db.today_str(), hours)
    for p in patients:
        p["done_today"] = is_done(p, index)
        p["needs_review"] = needs_review(p, index)
        p["done_info"] = ""
        if p["done_today"] or p["needs_review"]:
            info = db.completion_info(norm_id(p.get("an")), norm_id(p.get("hn")), hours)
            if info:
                p["done_info"] = f"{info.get('created_at', '')} @ {info.get('host', '')}"
    return patients


@app.get("/api/patients/db")
def api_patients_db():
    cfg = config.load_settings().get("hosxp_db", {})
    if not cfg.get("user"):
        raise HTTPException(400, "ยังไม่ได้ตั้งค่าบัญชีฐานข้อมูล HOSxP ในหน้า Settings")
    try:
        rows, stats = fetch_admitted(cfg, with_stats=True)
    except Exception as e:
        raise HTTPException(502, f"ต่อฐานข้อมูล HOSxP ไม่ได้: {e}")
    drug_error = attach_drug_counts(cfg, rows)
    return {
        "source": "db",
        "patient_key": _patient_key(),
        "patients": _annotate_done(rows),
        "filter_stats": stats,
        "drug_count_error": drug_error,
    }


@app.post("/api/patients/excel")
def api_patients_excel(file: UploadFile = File(...)):
    # เป็น def ธรรมดา (ไม่ใช่ async) เพื่อให้ FastAPI ย้ายไปรันใน threadpool
    # ไม่งั้นการอ่าน Excel / ต่อ MariaDB จะบล็อก event loop จน /api/stop สั่งหยุดโรบอทไม่ได้
    data = file.file.read()
    try:
        parsed = parse_patients(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"อ่านไฟล์ Excel ไม่ได้: {e}")

    rows = parsed["rows"]
    # เติม AN จากฐานข้อมูลให้แถวที่มีแต่ HN — AN ใช้เป็นคีย์กันคีย์ซ้ำและใช้ตรวจว่าโหลดถูกคน
    missing_an = [r for r in rows if not r["an"] and r["hn"]]
    enriched = 0
    ambiguous = 0
    db_lookup_error = ""
    if missing_an:
        try:
            admitted = fetch_admitted(config.load_settings().get("hosxp_db", {}))
            by_hn = {}
            dup_hn = set()
            for a in admitted:
                key = (a["hn"] or "").lstrip("0")
                if not key:
                    continue  # hn ว่าง/เป็นศูนย์ล้วน — ห้ามใช้เป็นกุญแจ เดี๋ยวจับคู่มั่ว
                if key in by_hn:
                    dup_hn.add(key)
                by_hn[key] = a
            for r in missing_an:
                key = (r["hn"] or "").lstrip("0")
                if not key:
                    continue
                if key in dup_hn:
                    # HN เดียวมีการ admit ค้างอยู่มากกว่าหนึ่ง — เดา AN ไม่ได้ ต้องให้คนเลือกเอง
                    ambiguous += 1
                    r["note"] = "HN นี้มีการ admit ค้างมากกว่า 1 ราย — ใส่ AN เองในไฟล์"
                    continue
                hit = by_hn.get(key)
                if hit:
                    r["an"] = hit["an"]
                    r["hn"] = hit["hn"]  # ใช้ HN รูปแบบเดียวกับในฐานข้อมูล เพื่อให้ตรวจหน้าจอ/log ตรงกัน
                    r["ptname"] = r["ptname"] or hit["ptname"]
                    r["ward_name"] = r["ward_name"] or hit["ward_name"]
                    r["regdate"] = r["regdate"] or hit.get("regdate", "")
                    r["los_days"] = hit.get("los_days", "")
                    enriched += 1
        except Exception as e:
            db_lookup_error = str(e)

    key_field = _patient_key()
    drug_error = attach_drug_counts(config.load_settings().get("hosxp_db", {}), rows)
    return {
        "source": "excel",
        "patient_key": key_field,
        "patients": _annotate_done(rows),
        "enriched_from_db": enriched,
        "ambiguous_hn": ambiguous,
        "missing_key_count": sum(1 for r in rows if not r.get(key_field)),
        "db_lookup_error": db_lookup_error,
        "drug_count_error": drug_error,
    }


# ---------- รัน / หยุด / สถานะ ----------


@app.post("/api/run")
def api_run(payload: dict = Body(...)):
    patients = payload.get("patients") or []
    dry_run = bool(payload.get("dry_run", True))
    source = str(payload.get("source", ""))
    if not patients:
        raise HTTPException(400, "ไม่มีรายชื่อผู้ป่วยที่เลือก")
    try:
        operator = getpass.getuser()
    except Exception:
        operator = ""
    try:
        run_id = manager.start(patients, dry_run, source, operator)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"run_id": run_id}


@app.post("/api/patients/clear_review")
def api_clear_review(payload: dict = Body(...)):
    """ปลดสถานะ 'ต้องตรวจเอง' หลังเภสัชกรเข้าไปดูใน HosXP แล้วว่ายายังไม่เข้า
    ลบเฉพาะสถานะ 'ไม่รู้ผล' เท่านั้น — คนที่บันทึกสำเร็จจริงยังถูกกันคีย์ซ้ำไว้เหมือนเดิม"""
    an = norm_id(payload.get("an"))
    hn = norm_id(payload.get("hn"))
    if not an and not hn:
        raise HTTPException(400, "ต้องระบุ AN หรือ HN")
    if manager.is_running():
        raise HTTPException(409, "มีงานกำลังรันอยู่ — หยุดงานก่อนแล้วค่อยปลดสถานะ")
    removed = db.clear_review(an, hn, db.today_str(), _dedup_hours())
    return {"ok": True, "removed": removed}


@app.get("/api/patients/status")
def api_patients_status():
    """สถานะ 'ทำแล้ว / ต้องตรวจเอง' ของวันนี้ทั้งหมด — ให้หน้าเว็บอัปเดตตารางได้ทันทีระหว่าง/หลังรัน
    (ก่อนหน้านี้ต้องสลับ ward ไปกลับถึงจะเห็นสถานะเปลี่ยน เพราะตารางไม่เคยถูกรีเฟรช)"""
    idx = db.done_index(db.today_str(), _dedup_hours())
    return {
        "done_an": sorted(idx.get("an", ())),
        "done_hn": sorted(idx.get("hn", ())),
        "review_an": sorted(idx.get("unknown_an", ())),
        "review_hn": sorted(idx.get("unknown_hn", ())),
    }


@app.post("/api/patients/confirm_review")
def api_confirm_review(payload: dict = Body(...)):
    """ยืนยันว่า 'ยาเข้าไปแล้ว' หลังเภสัชกรเข้าไปตรวจใน HosXP
    เปลี่ยนสถานะจาก 'ไม่รู้ผล' เป็น 'ทำแล้ว' เพื่อกันคีย์ซ้ำ (ตรงข้ามกับ clear_review)"""
    an = norm_id(payload.get("an"))
    hn = norm_id(payload.get("hn"))
    if not an and not hn:
        raise HTTPException(400, "ต้องระบุ AN หรือ HN")
    if manager.is_running():
        raise HTTPException(409, "มีงานกำลังรันอยู่ — หยุดงานก่อนแล้วค่อยยืนยัน")
    changed = db.confirm_review(an, hn, db.today_str(), _dedup_hours())
    return {"ok": True, "changed": changed}


@app.post("/api/stop")
def api_stop():
    manager.stop()
    return {"ok": True}


@app.post("/api/unblock")
def api_unblock():
    """ปลดล็อกหลังงานหยุดด้วยขั้นตอนค้าง — ให้เลือกคนไข้แล้วกดรันใหม่ได้โดยไม่ต้องปิดโปรแกรม
    ปลดให้เฉพาะตอนไม่มีคำสั่งค้างเหลืออยู่แล้วเท่านั้น (ดูเหตุผลใน JobManager.unblock)"""
    try:
        manager.unblock()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "blocked": manager.blocked}


@app.get("/api/status")
def api_status():
    st = manager.status()
    run_id = st.get("run_id")
    if run_id:
        st["run"] = db.get_run(run_id)
    st["elevated"] = is_elevated()
    st["elevation_help"] = "" if st["elevated"] else ELEVATION_HELP
    st["blocked"] = manager.blocked
    return st


@app.get("/api/runs")
def api_runs(limit: int = 50, offset: int = 0):
    return {"runs": db.get_runs(limit=limit, offset=offset)}


@app.get("/api/runs/{run_id}/logs")
def api_run_logs(run_id: int, after: int = 0):
    return {"logs": db.get_logs(run_id, after_id=after)}


# ---------- settings ----------


@app.get("/api/settings")
def api_get_settings():
    return config.redact(config.load_settings())


@app.post("/api/settings")
def api_save_settings(payload: dict = Body(...)):
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(400, "รูปแบบการตั้งค่าไม่ถูกต้อง")
    old = config.load_settings()
    merged = config.restore_secrets(payload, old)
    try:
        config.save_settings(merged)
    except OSError as e:
        raise HTTPException(500, f"เขียนไฟล์ settings.json ไม่ได้: {e}")
    return {"ok": True}
