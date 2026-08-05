"""สำรวจหน้า IPD Medication Profile ที่เปิดอยู่ — อ่านอย่างเดียว ไม่คลิก ไม่พิมพ์ ไม่แตะชาร์ตคนไข้

ใช้ 2 รอบเพื่อเทียบกัน:
  รอบที่ 1  ตอนยังไม่มีคนไข้ (หน้าเลือกคนไข้)
  รอบที่ 2  ตอนโหลดคนไข้แล้ว/อยู่หน้า Add Chart  <- รอบนี้สำคัญสุด เพราะปุ่มเพิ่งถูกสร้าง

ผลลัพธ์: data\\ipd_probe.txt (ล่าสุด) และสำเนาใน data\\scans\\ipd_probe_<เวลา>.txt
"""
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from pywinauto.application import Application  # noqa: E402

from app.config import DATA_DIR, load_settings  # noqa: E402
from app.robot.session import safe_text, with_timeout  # noqa: E402
from app.winutil import is_elevated  # noqa: E402

_lines = []


def say(msg=""):
    print(msg)
    _lines.append(str(msg))


def describe(ctrl) -> str:
    try:
        cls = ctrl.friendly_class_name()
    except Exception:
        cls = "?"
    try:
        real = ctrl.class_name()
    except Exception:
        real = "?"
    try:
        r = ctrl.rectangle()
        rect = f"L{r.left} T{r.top} R{r.right} B{r.bottom}"
    except Exception:
        rect = "?"
    txt = ""
    try:
        txt = safe_text(ctrl.handle)
    except Exception:
        pass
    return f"{real:<26} {cls:<14} {rect:<34} {txt!r}"


def main():
    say(f"เวลา {datetime.now():%Y-%m-%d %H:%M:%S} | รันด้วยสิทธิ์แอดมิน = {is_elevated()}")
    s = load_settings()
    rb = s.get("robot", {})
    mw = rb.get("main_window", {})
    kw = {}
    if mw.get("class_name"):
        kw["class_name"] = mw["class_name"]
    if mw.get("title_re"):
        kw["title_re"] = mw["title_re"]

    try:
        app = Application(backend="win32").connect(**kw, timeout=5)
    except Exception as e:
        say(f"[ผิดพลาด] ไม่พบหน้าต่างหลักของ HosXP — เปิดและ login ก่อน ({e})")
        return 1
    main_win = app.window(**kw)

    ipd_spec = rb.get("ipd_form", {})
    forms = with_timeout(
        lambda: main_win.descendants(class_name=ipd_spec.get("class_name", "TIPDRxForm")),
        30,
        "หาฟอร์ม IPD",
    ) or []
    if not forms:
        say("[ผิดพลาด] ไม่พบหน้า IPD Medication Profile — เปิดเมนูนี้ค้างไว้ก่อน")
        return 1
    ipd = forms[0]
    say(f"พบฟอร์ม IPD จำนวน {len(forms)} บาน — สำรวจบานแรก\n")

    items = with_timeout(lambda: ipd.descendants(), 60, "ไล่ control ทั้งหมด") or []
    say(f"=== control ทั้งหมดในฟอร์ม: {len(items)} รายการ ===\n")

    # 1) ปุ่มทั้งหมด (สิ่งที่ต้องใช้กด)
    say("=== ปุ่ม (TcxButton / Button) ทั้งหมด ===")
    n = 0
    for c in items:
        try:
            cn = c.class_name()
        except Exception:
            continue
        if "Button" in cn:
            say("  " + describe(c))
            n += 1
    say(f"  (รวม {n} ปุ่ม)\n")

    # 2) กรอบต่าง ๆ
    say("=== กรอบ (TcxGroupBox) และแท็บ (TcxTabSheet) ===")
    for c in items:
        try:
            cn = c.class_name()
        except Exception:
            continue
        if cn in ("TcxGroupBox", "TcxTabSheet"):
            say("  " + describe(c))
    say()

    # 3) ช่องกรอกที่มีข้อความ (ตอนโหลดคนไข้แล้วจะเห็น HN/AN/ชื่อ/ward)
    say("=== ช่องกรอก/ป้ายที่มีข้อความ (ใช้ดูว่า HN/AN โผล่ที่ไหน) ===")
    n = 0
    for c in items:
        try:
            txt = safe_text(c.handle)
        except Exception:
            continue
        if txt and txt.strip():
            say("  " + describe(c))
            n += 1
            if n >= 80:
                say("  ... (ตัดที่ 80 รายการ)")
                break
    say()

    # 4) เทียบกับ selector ที่ตั้งไว้ในไฟล์ตั้งค่า
    say("=== ตรวจ selector ที่ตั้งไว้ในระบบ ว่าหาเจอไหมในสภาพหน้าจอตอนนี้ ===")
    from app.robot.session import HosxpSession

    sess = HosxpSession.__new__(HosxpSession)
    sess.settings, sess.robot_cfg, sess.app_cfg = s, rb, s.get("hosxp_app", {})
    sess.timing, sess.app, sess.main = rb.get("timing", {}), app, main_win
    sess.log = lambda *a: None

    checks = [
        ("กรอบเลือกคนไข้", ipd_spec.get("select_group", {}), ipd),
        ("กรอบข้อมูลผู้ป่วย", ipd_spec.get("info_group", {}), ipd),
        ("แท็บสั่งยา", ipd_spec.get("drug_tab", {}), ipd),
    ]
    for b in rb.get("steps", {}).get("action_buttons", []):
        checks.append((f"ปุ่ม {b.get('label')}", b, ipd))
    for name, spec, scope in checks:
        try:
            found = sess.find_all(scope, spec, name, timeout=20)
        except Exception as e:
            say(f"  {name:<24} -> ผิดพลาด: {e}")
            continue
        say(f"  {name:<24} -> พบ {len(found)} รายการ" + (f"  [{describe(found[0])}]" if found else ""))

    out_dir = DATA_DIR / "scans"
    out_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(_lines)
    (DATA_DIR / "ipd_probe.txt").write_text(text, encoding="utf-8")
    stamped = out_dir / f"ipd_probe_{datetime.now():%H%M%S}.txt"
    stamped.write_text(text, encoding="utf-8")
    print(f"\nบันทึกไว้ที่ {DATA_DIR / 'ipd_probe.txt'}")
    print(f"และสำเนา {stamped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
