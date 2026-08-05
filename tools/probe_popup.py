"""สำรวจหน้าต่าง/dialog ที่กำลังเปิดค้างอยู่ใน HosXP — อ่านอย่างเดียว ไม่คลิก ไม่กดปุ่มใด ๆ

ใช้ตอนมี popup เด้งค้างอยู่บนจอ (เช่น Drug Interaction, Note view, คำเตือนต่าง ๆ)
เพื่อดูว่าโรบอทอ่านอะไรจากหน้าต่างนั้นได้บ้าง — โดยเฉพาะค่าที่ต้องใช้ตัดสินใจ เช่น Level

วิธีใช้: ให้ popup ค้างอยู่บนจอ แล้วดับเบิลคลิก probe_popup.bat
ผลลัพธ์: data\\popup_probe.txt (+ สำเนาใน data\\scans\\)
"""
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from pywinauto.application import Application  # noqa: E402
from pywinauto.findwindows import find_elements  # noqa: E402

from app.config import DATA_DIR, load_settings  # noqa: E402
from app.robot import winapi  # noqa: E402
from app.winutil import is_elevated  # noqa: E402

_lines = []


def say(msg=""):
    print(msg)
    _lines.append(str(msg))


def dump_window(h, prefix="  "):
    cls = winapi.class_name_of(h)
    title = winapi.get_text(h)
    rect = winapi.window_rect(h)
    say(f"{prefix}หน้าต่าง class={cls!r} title={title!r} rect={rect} visible={winapi.is_visible(h)}")
    kids = winapi.child_handles(h, limit=400)
    say(f"{prefix}  จำนวน control ลูก = {len(kids)}")
    readable = 0
    for k in kids:
        kcls = winapi.class_name_of(k)
        ktxt = winapi.get_text(k)
        krect = winapi.window_rect(k)
        if ktxt:
            readable += 1
        say(f"{prefix}    [{kcls:<28}] {ktxt!r}  {krect}")
    say(f"{prefix}  -> อ่านข้อความได้ {readable} จาก {len(kids)} control")
    return readable, len(kids)


def main():
    say(f"เวลา {datetime.now():%Y-%m-%d %H:%M:%S} | สิทธิ์แอดมิน = {is_elevated()}")
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
        main_win = app.window(**kw)
        main_handle = main_win.handle
        pid = app.process
    except Exception as e:
        say(f"[ผิดพลาด] ไม่พบหน้าต่างหลักของ HosXP ({e})")
        return 1
    say(f"HosXP pid={pid} หน้าต่างหลัก handle={main_handle}\n")

    say("=== หน้าต่างทั้งหมดของ HosXP ที่ไม่ใช่หน้าต่างหลัก ===")
    seen = set()
    found_any = False
    try:
        els = find_elements(process=pid, top_level_only=True)
    except Exception as e:
        els = []
        say(f"(หา top-level ไม่ได้: {e})")
    for e in els:
        try:
            h = int(e.handle)
        except Exception:
            continue
        if h == main_handle or h in seen or not winapi.is_visible(h):
            continue
        seen.add(h)
        found_any = True
        say("")
        dump_window(h)

    # เผื่อ dialog เป็นหน้าต่างลูกของหน้าต่างหลัก
    say("\n=== หน้าต่างลูกของหน้าต่างหลักที่หน้าตาเหมือน dialog ===")
    try:
        for c in main_win.children():
            h = c.handle
            cls = winapi.class_name_of(h)
            if not winapi.is_visible(h):
                continue
            if cls in ("#32770",) or "Form" in cls:
                if h in seen:
                    continue
                seen.add(h)
                found_any = True
                say("")
                dump_window(h)
    except Exception as e:
        say(f"(ไล่ลูกของหน้าต่างหลักไม่ได้: {e})")

    if not found_any:
        say("\n*** ไม่พบหน้าต่าง/dialog ที่เปิดค้างอยู่ ***")
        say("ให้ popup ค้างอยู่บนจอก่อนแล้วรันใหม่")

    say("\n=== หน้าต่างที่โฟกัสอยู่ตอนนี้ ===")
    h, cls, title, root, fpid = winapi.foreground_info()
    say(f"handle={h} class={cls!r} title={title!r} root={root} pid={fpid} (HosXP pid={pid})")

    out_dir = DATA_DIR / "scans"
    out_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(_lines)
    (DATA_DIR / "popup_probe.txt").write_text(text, encoding="utf-8")
    stamped = out_dir / f"popup_probe_{datetime.now():%H%M%S}.txt"
    stamped.write_text(text, encoding="utf-8")
    print(f"\nบันทึกไว้ที่ {DATA_DIR / 'popup_probe.txt'}")
    print(f"และสำเนา {stamped}")
    return 0


if __name__ == "__main__":
    time.sleep(0.5)
    sys.exit(main())
