"""อ่านข้อความในกล่อง dialog ด้วย UI Automation — สำหรับกล่องที่ถามค่าตรง ๆ ไม่ได้

ทำไมต้องมี (พิสูจน์แล้ว 2026-08-05):
    กล่องของ HosXP เป็น "Task Dialog" ของ Windows ไม่ใช่ MessageBox ธรรมดา
    ข้อความอยู่ในชั้น DirectUIHWND ซึ่งไม่มี control ให้ถาม WM_GETTEXT จึงคืนค่าว่างเสมอ
    (ยืนยันจากไฟล์ data\\scans\\dialog_20260805_113255_601.txt — control 17 ตัว ไม่มีตัวไหนมีข้อความเลย
     แต่ในภาพเขียนว่า "Please confirm save" ชัด ๆ)

    ทดลองสร้างกล่องแบบเดียวกันแล้วอ่านเทียบ:
        WM_GETTEXT       -> ''
        UI Automation    -> 'Rx-Queue : 283\\nSelect new patient ?'  (ใช้เวลา 0.04 วินาที)

หมายเหตุ: ใช้ได้กับกล่องมาตรฐานของ Windows เท่านั้น
หน้าต่างของ Delphi เอง (เช่น TDrugInteractionAlertForm) UIA อ่านไม่ได้ แต่ WM_GETTEXT อ่านได้อยู่แล้ว
จึงเสริมกันพอดี
"""
import threading

_lock = threading.Lock()
_desktop = None
_broken = False  # ถ้าเครื่องนี้ใช้ UIA ไม่ได้ จะเลิกลองเพื่อไม่ให้เสียเวลาทุกครั้ง

# ชนิด element ที่ถือว่าเป็น "ข้อความในกล่อง" (ไม่เอาปุ่ม/ไอคอน)
TEXT_TYPES = ("Text", "Edit", "Hyperlink", "Document")


def _get_desktop():
    global _desktop
    if _desktop is None:
        import comtypes

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except Exception:
            pass  # เธรดนี้อาจ init ไว้แล้ว ซึ่งไม่ใช่ปัญหา
        from pywinauto import Desktop

        _desktop = Desktop(backend="uia")
    return _desktop


def dialog_texts(handle: int, budget: float = 4.0) -> list:
    """คืนข้อความทั้งหมดในกล่อง (เฉพาะส่วนที่เป็นตัวหนังสือ ไม่รวมปุ่ม) — ล้มเหลวได้ ห้าม raise"""
    global _broken
    if not handle or _broken:
        return []

    def work():
        win = _get_desktop().window(handle=handle)
        out = []
        for c in win.descendants():
            try:
                if c.element_info.control_type not in TEXT_TYPES:
                    continue
                txt = (c.window_text() or "").strip()
            except Exception:
                continue
            if txt:
                out.append(txt)
        return out

    try:
        from .session import StepTimeout, with_timeout

        return with_timeout(work, budget, "อ่านข้อความในกล่องด้วย UI Automation") or []
    except StepTimeout:
        return []  # อ่านไม่ทันก็ไม่เป็นไร ผู้เรียกจะถือว่าอ่านไม่ได้ (fail-closed อยู่แล้ว)
    except Exception:
        _broken = True  # เครื่องนี้ใช้ UIA ไม่ได้เลย เลิกลองเพื่อไม่ให้ถ่วงทุกกล่อง
        return []


def buttons(handle: int, budget: float = 4.0) -> list:
    """คืนชื่อปุ่มในกล่องตามที่ UIA เห็น (สำรองไว้เผื่อ WM_GETTEXT อ่านชื่อปุ่มไม่ได้)"""
    if not handle or _broken:
        return []

    def work():
        win = _get_desktop().window(handle=handle)
        out = []
        for c in win.descendants():
            try:
                if c.element_info.control_type != "Button":
                    continue
                txt = (c.window_text() or "").strip()
            except Exception:
                continue
            if txt:
                out.append(txt)
        return out

    try:
        from .session import StepTimeout, with_timeout

        return with_timeout(work, budget, "อ่านชื่อปุ่มด้วย UI Automation") or []
    except StepTimeout:
        return []
    except Exception:
        return []
