r"""ทดสอบว่าสั่งงาน HosXP โดย "ไม่ใช้เมาส์/คีย์บอร์ดจริง" ได้ไหม — ต้องรันด้วยสิทธิ์แอดมิน

ทำไมต้องแอดมิน: HosXP รันด้วยสิทธิ์สูง Windows จึงบล็อกการส่ง message แบบ "เขียน"
(WM_SETTEXT / WM_CHAR / BM_CLICK) จากโปรเซสสิทธิ์ต่ำ ทดสอบแบบไม่แอดมินจะได้ผลลวงว่า "ไม่ได้"

ปลอดภัย: ไม่กด Enter ไม่กดบันทึก ไม่แตะปุ่มที่เปลี่ยนข้อมูล และล้างช่องคืนให้เสมอ
ผลลัพธ์: data\scans\no_mouse_test.txt
"""
import ctypes
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.config import DATA_DIR, load_settings  # noqa: E402
from app.robot import winapi  # noqa: E402
from app.robot.session import HosxpSession  # noqa: E402
from app.winutil import is_elevated  # noqa: E402

lines = []


def say(m=""):
    print(m)
    lines.append(str(m))


u = ctypes.WinDLL("user32", use_last_error=True)
u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_wchar_p]
u.SendMessageW.restype = ctypes.c_ssize_t
u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
WM_SETTEXT, EM_SETSEL, EM_REPLACESEL, WM_CHAR, BM_CLICK = 0x000C, 0x00B1, 0x00C2, 0x0102, 0x00F5
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202



kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetConsoleWindow.restype = wintypes.HWND
u.SetForegroundWindow.argtypes = [wintypes.HWND]
u.GetForegroundWindow.restype = wintypes.HWND


def steal_focus() -> str:
    """ดึงโฟกัสออกจาก HosXP มาที่หน้าต่างคอนโซลนี้ — จำลองว่าผู้ใช้ไปทำงานอื่นอยู่"""
    con = kernel32.GetConsoleWindow()
    if con:
        u.SetForegroundWindow(con)
        time.sleep(0.6)
    return winapi.foreground_info()[1] or "?"


def main():
    say(f"เวลา {datetime.now():%Y-%m-%d %H:%M:%S}")
    say(f"สิทธิ์แอดมิน = {is_elevated()}")
    if not is_elevated():
        say("!! ต้องรันด้วยสิทธิ์แอดมิน ไม่งั้นผลจะลวง — ปิดแล้วดับเบิลคลิก test_no_mouse.bat")
        return 1

    from pywinauto.application import Application
    from pywinauto.findwindows import find_elements

    cfg = load_settings()["robot"]
    els = find_elements(class_name=cfg["ipd_form"]["class_name"], top_level_only=False)
    if not els:
        say("!! ไม่พบหน้า IPD Medication Profile — เปิดหน้านี้ใน HosXP ค้างไว้ก่อน")
        return 1
    ipd_h = int(els[0].handle)
    app = Application(backend="win32").connect(handle=ipd_h)
    win = app.window(handle=ipd_h)

    s = HosxpSession.__new__(HosxpSession)
    s.robot_cfg, s.timing, s.poisoned = cfg, cfg["timing"], False
    s._log = lambda *a: None

    at_select = s.quick_has_control(ipd_h, cfg["ipd_form"]["select_group"])
    say(f"หน้าจอตอนนี้: อยู่หน้าเลือกคนไข้ = {at_select}")
    say("")

    # ---------- 1. กดปุ่มโดยไม่ใช้เมาส์ ----------
    say("=== ทดสอบ: กดปุ่มโดยไม่ใช้เมาส์จริง (ใช้ปุ่ม 'เลือกใหม่' ซึ่งไม่เปลี่ยนข้อมูลอะไร) ===")
    btn_spec = cfg["ipd_form"].get("select_new_button") or {}
    btn = s.find_one(win, btn_spec, "ปุ่มเลือกใหม่", 10) if btn_spec else None
    if btn is None:
        say("   (ข้าม: ไม่เห็นปุ่ม 'เลือกใหม่' บนจอตอนนี้ — เปิดคนไข้ขึ้นมาสักคนแล้วรันใหม่)")
    else:
        bh = int(btn.handle)
        say(f"   ปุ่ม: คลาส {winapi.class_name_of(bh)!r} ข้อความ {winapi.get_text(bh, 500)!r}")
        before_state = s.quick_has_control(ipd_h, cfg["ipd_form"]["select_group"])
        fg = steal_focus()   # << จุดสำคัญ: ดึงโฟกัสไปที่อื่นก่อนสั่ง
        say(f"   ดึงโฟกัสไปที่หน้าต่างอื่นแล้ว (ตอนนี้หน้าสุดคือ [{fg}]) แล้วค่อยสั่งกดปุ่ม")
        u.SendMessageW(bh, BM_CLICK, 0, None)
        time.sleep(1.5)
        after = s.quick_has_control(ipd_h, cfg["ipd_form"]["select_group"])
        say(f"   BM_CLICK: หน้าเลือกคนไข้ ก่อน={before_state} หลัง={after} -> "
            f"{'ปุ่มทำงาน ✅' if (after and not before_state) else 'ไม่มีผล ❌'}")
        if not (after and not before_state):
            u.PostMessageW(bh, WM_LBUTTONDOWN, 1, 0)
            time.sleep(0.08)
            u.PostMessageW(bh, WM_LBUTTONUP, 0, 0)
            time.sleep(1.5)
            after2 = s.quick_has_control(ipd_h, cfg["ipd_form"]["select_group"])
            say(f"   WM_LBUTTONDOWN/UP: หลัง={after2} -> "
                f"{'ปุ่มทำงาน ✅' if (after2 and not before_state) else 'ไม่มีผล ❌'}")
    # ---------- 2. ใส่ข้อความโดยไม่ใช้คีย์บอร์ด ----------
    say("=== ทดสอบ: ใส่ข้อความลงช่องคีย์ HN โดยไม่ใช้คีย์บอร์ดจริง ===")
    # ต้องเช็คสถานะใหม่ตรงนี้ ไม่ใช้ค่าที่อ่านไว้ตั้งแต่ต้นฟังก์ชัน
    # เพราะการทดสอบปุ่ม 'เลือกใหม่' ข้างบนเพิ่งพาหน้าจอกลับมาหน้าเลือกคนไข้
    at_select = s.quick_has_control(ipd_h, cfg["ipd_form"]["select_group"])
    say(f"   (เช็คสถานะใหม่: อยู่หน้าเลือกคนไข้ = {at_select})")
    if not at_select:
        say("   (ข้าม: ต้องอยู่หน้าเลือกคนไข้ กดปุ่ม 'เลือกใหม่' ใน HosXP ก่อนแล้วรันใหม่)")
    else:
        grp = s.find_one(win, cfg["ipd_form"]["select_group"], "กรอบเลือกคนไข้", 20)
        edits = grp.descendants(class_name=cfg["ipd_form"].get("patient_edit_class", "TdxEdit"))
        e = edits[int(cfg["ipd_form"].get("patient_edit_index", 0))]
        h = int(e.handle)
        before = winapi.get_text(h, 800)
        say(f"   ช่องคีย์: คลาส {winapi.class_name_of(h)!r} ค่าเดิม {before!r}")
        TEST = "1234567"

        fg = steal_focus()   # << จุดสำคัญ: ดึงโฟกัสไปที่อื่นก่อนสั่ง
        say(f"   ดึงโฟกัสไปที่หน้าต่างอื่นแล้ว (ตอนนี้หน้าสุดคือ [{fg}]) แล้วค่อยสั่งพิมพ์")

        def try_method(name, fn):
            u.SendMessageW(h, WM_SETTEXT, 0, "")
            time.sleep(0.25)
            try:
                fn()
            except Exception as ex:
                say(f"   {name}: ผิดพลาด {ex}")
                return False
            time.sleep(0.5)
            got = (winapi.get_text(h, 800) or "").strip()
            hit = got == TEST
            say(f"   {name}: อ่านกลับได้ {got!r} -> {'ติด ✅' if hit else 'ไม่ติด ❌'}")
            return hit

        r1 = try_method("WM_SETTEXT   ", lambda: u.SendMessageW(h, WM_SETTEXT, 0, TEST))
        r2 = try_method("EM_REPLACESEL", lambda: (u.SendMessageW(h, EM_SETSEL, 0, None),
                                                  u.SendMessageW(h, EM_REPLACESEL, 1, TEST)))

        def chars():
            for ch in TEST:
                u.PostMessageW(h, WM_CHAR, ord(ch), 0)
                time.sleep(0.03)

        r3 = try_method("WM_CHAR ทีละตัว", chars)
        u.SendMessageW(h, WM_SETTEXT, 0, before or "")
        time.sleep(0.3)
        say(f"   ล้างช่องคืนแล้ว: {winapi.get_text(h, 800)!r}")
        say(f"   >> ใส่ข้อความโดยไม่ใช้คีย์บอร์ด: {'ทำได้' if (r1 or r2 or r3) else 'ทำไม่ได้'}")
    say("")

    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception as e:
        import traceback

        say(f"!! ผิดพลาด: {e}")
        say(traceback.format_exc(limit=3))
    try:
        out = DATA_DIR / "scans" / "no_mouse_test.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nบันทึกผลไว้ที่ {out}")
    except Exception:
        pass
    input("\nกด Enter เพื่อปิด")
    sys.exit(code)
