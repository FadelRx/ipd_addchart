"""โหลด/บันทึก settings.json (deep-merge ทับ default_settings.json)"""
import copy
import json
import os
import threading
from pathlib import Path

import sys

FROZEN = getattr(sys, "frozen", False)

# ที่อยู่ไฟล์มี 2 ชุด และต้องแยกให้ขาดเมื่อแพ็กเป็น .exe ไฟล์เดียว
#   RESOURCE_DIR = ที่อยู่ของไฟล์ "อ่านอย่างเดียว" ที่ถูกฝังมาในตัว exe (static, default_settings)
#                  ตอนรันจะถูกแตกไว้ในโฟลเดอร์ชั่วคราวและ "ถูกลบทิ้งทุกครั้งที่ปิดโปรแกรม"
#   BASE_DIR     = โฟลเดอร์ที่ไฟล์ .exe วางอยู่ ใช้เก็บของที่ต้องคงอยู่ (data\, config\settings.json)
#
# ถ้าใช้ที่อยู่เดียวกันทั้งคู่ log และประวัติการรันจะหายทุกครั้งที่ปิดโปรแกรม
if FROZEN:
    BASE_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = BASE_DIR

CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
# ค่าตั้งต้นมากับตัวโปรแกรม ส่วนค่าที่ผู้ใช้ตั้งเองอยู่ข้าง .exe (ไม่ถูกทับเวลาอัปเดตโปรแกรม)
DEFAULT_PATH = RESOURCE_DIR / "config" / "default_settings.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"

# path ของค่าลับใน settings — ใช้ redact ก่อนส่งออกหน้าเว็บ
SECRET_PATHS = [("hosxp_db", "password"), ("hosxp_app", "password")]
# ค่าที่ส่งแทนรหัสผ่านจริง — ถ้าส่งกลับมาเหมือนเดิมแปลว่า "ไม่เปลี่ยน", ถ้าเป็นค่าว่างแปลว่า "ล้างรหัสผ่าน"
SECRET_MASK = "********"

_lock = threading.RLock()


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # ไฟล์เสีย/เขียนค้าง — ถอยไปใช้ค่า default แทนที่จะพังทั้งแอป
        return {}


def load_settings() -> dict:
    with _lock:
        return _deep_merge(_read_json(DEFAULT_PATH), _read_json(SETTINGS_PATH))


def strip_defaults(settings: dict, defaults: dict = None) -> dict:
    """เก็บเฉพาะค่าที่ "ต่างจากค่าตั้งต้น" เท่านั้น

    ทำไมสำคัญ (บทเรียนจริง 2026-08-05):
        เดิม settings.json เก็บสำเนา config ทั้งก้อน พอโปรแกรมอัปเดตกฎ/ค่าใหม่ใน
        default_settings.json สำเนาเก่าจะทับค่าใหม่ทิ้งทั้งหมดอย่างเงียบ ๆ
        ผลคือแก้โค้ดแล้วเหมือนไม่มีอะไรเกิดขึ้น หาสาเหตุยากมาก
        เก็บเฉพาะส่วนต่าง (เช่น ที่อยู่ฐานข้อมูล ชื่อผู้ใช้) แล้วค่าที่เหลือจะไหลตามตัวโปรแกรมเสมอ
    """
    if defaults is None:
        defaults = _read_json(DEFAULT_PATH)
    out = {}
    for k, v in (settings or {}).items():
        base = (defaults or {}).get(k)
        if isinstance(v, dict) and isinstance(base, dict):
            sub = strip_defaults(v, base)
            if sub:
                out[k] = sub
        elif v != base:
            out[k] = copy.deepcopy(v)
    return out


def save_settings(settings: dict) -> None:
    """เขียนแบบ atomic (tmp แล้ว replace) กันไฟล์เสียเมื่อดับกลางคัน"""
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(strip_defaults(settings), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SETTINGS_PATH)


def redact(settings: dict) -> dict:
    out = copy.deepcopy(settings)
    for section, key in SECRET_PATHS:
        if isinstance(out.get(section), dict) and out[section].get(key):
            out[section][key] = SECRET_MASK
    return out


def restore_secrets(new_settings: dict, old_settings: dict) -> dict:
    """ค่าที่ยังเป็น SECRET_MASK = ผู้ใช้ไม่ได้แก้ ให้คืนค่าเดิม
    ค่าว่าง = ผู้ใช้ตั้งใจล้างรหัสผ่าน (ต้องล้างได้จริง เผื่อต้องการตัดการเชื่อมต่อ)"""
    out = copy.deepcopy(new_settings)
    for section, key in SECRET_PATHS:
        if not isinstance(out.get(section), dict):
            continue
        if out[section].get(key) == SECRET_MASK:
            out[section][key] = (old_settings.get(section) or {}).get(key, "")
    return out
