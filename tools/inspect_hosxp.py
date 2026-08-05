"""สแกนหน้าจอ HosXP ที่เปิดอยู่ แล้ว dump รายชื่อ control ทั้งหมดลงไฟล์ เพื่อใช้ map ปุ่ม/ช่องที่ยังไม่รู้ชื่อ

วิธีใช้ (เปิด HosXP และหน้า IPD Medication Profile ค้างไว้ก่อน):
    venv\\Scripts\\python.exe tools\\inspect_hosxp.py
ผลลัพธ์อยู่ที่ data\\hosxp_controls.txt — หาไฟล์คำว่า TIPDRxForm แล้วดูปุ่มข้างใน
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from pywinauto.application import Application  # noqa: E402

from app.config import DATA_DIR, load_settings  # noqa: E402

OUT_PATH = DATA_DIR / "hosxp_controls.txt"


def main() -> None:
    settings = load_settings()
    main_spec = settings.get("robot", {}).get("main_window", {})
    kwargs = {}
    if main_spec.get("class_name"):
        kwargs["class_name"] = main_spec["class_name"]
    if main_spec.get("title_re"):
        kwargs["title_re"] = main_spec["title_re"]

    print("กำลังหาโปรแกรม HosXP ...")
    try:
        app = Application(backend="win32").connect(**kwargs, timeout=5)
    except Exception as e:
        print(f"ไม่พบหน้าต่างหลักของ HosXP — เปิดโปรแกรมและ login ก่อน ({e})")
        sys.exit(1)

    win = app.window(**kwargs)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("กำลังสแกน control ทั้งหมด (อาจใช้เวลาสักครู่) ...")
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        original = sys.stdout
        sys.stdout = f
        try:
            win.print_control_identifiers(depth=None)
        finally:
            sys.stdout = original

    # เก็บสำเนาแยกตามเวลา เพื่อสแกนหลายรอบ (ก่อน/หลังโหลดคนไข้) แล้วเอามาเทียบกันได้
    from datetime import datetime

    scans = DATA_DIR / "scans"
    scans.mkdir(parents=True, exist_ok=True)
    stamped = scans / f"hosxp_controls_{datetime.now():%H%M%S}.txt"
    stamped.write_text(OUT_PATH.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

    print(f"เสร็จแล้ว — {OUT_PATH}")
    print(f"สำเนา   — {stamped}")
    print("ค้นหาคำว่า TIPDRxForm เพื่อดูโครงสร้างหน้า IPD Medication Profile")


if __name__ == "__main__":
    main()
