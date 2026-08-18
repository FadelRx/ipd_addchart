"""HosxpSession — เชื่อม/เปิด hosmy.exe ด้วย pywinauto (win32 backend) และเตรียมหน้าจอ IPD Medication Profile

หลักความปลอดภัยของไฟล์นี้:
1. ไม่กดปุ่มใส่ dialog ที่ไม่รู้จักเด็ดขาด — Enter คือปุ่ม default ซึ่งมักแปลว่า "ตกลง"
   ถ้า dialog นั้นคือคำเตือนแพ้ยา/ยาตีกัน/ยาซ้ำ การกด Enter = ยืนยันสั่งยาทับคำเตือนแทนเภสัชกร
   จึงใช้ระบบ allowlist: รู้จักเท่านั้นถึงกด นอกนั้นหยุดงานแล้วปล่อย dialog ไว้ให้คนอ่าน
2. ทุกการอ่านข้อความจากหน้าต่างโปรแกรมอื่นต้องมี timeout (SendMessageTimeout)
3. เลือกเฉพาะ control ที่มองเห็นจริง — หน้า IPD มีแท็บซ้อนกัน 6 แท็บที่พิกัดเดียวกัน
"""
import re
import threading
import time

from pywinauto import keyboard
from pywinauto.application import Application
from pywinauto.findwindows import ElementAmbiguousError, ElementNotFoundError

from ..winutil import ELEVATION_HELP, is_elevated
from . import winapi

TEXT_TIMEOUT_MS = 800
DESKTOP_LOCKED_HELP = (
    "Windows ไม่ยอมให้ขยับเมาส์/คลิกในตอนนี้ โรบอทจึงทำงานต่อไม่ได้ "
    "สาเหตุที่พบบ่อย: หน้าจอถูกล็อก, การเชื่อมต่อรีโมท (เช่น Chrome Remote Desktop) หลุดหรือถูกย่อ, "
    "หรือมีหน้าต่างของ Windows เช่น UAC ครอบอยู่ "
    "— ให้ปลดล็อก/เชื่อมต่อรีโมทกลับมาให้เห็นหน้าจอจริง แล้วกดเริ่มใหม่ "
    "และห้ามล็อกหน้าจอหรือปิดรีโมทระหว่างที่โรบอทกำลังทำงาน"
)
# อักขระที่ pywinauto ตีความเป็นคำสั่งพิเศษ ต้องครอบด้วย {} เมื่อจะพิมพ์เป็นตัวอักษรจริง
_SPECIAL_KEYS = "^+%~(){}[]"


class RobotError(Exception):
    pass


class ConfigError(RobotError):
    pass


class StepTimeout(RobotError):
    """ขั้นตอนหนึ่งค้างเกินเวลา — มักเกิดเมื่อ HosXP ไม่ตอบสนอง (ค้าง/มี dialog ซ่อนอยู่)"""


class PopupNeedsHuman(RobotError):
    """เจอ dialog ที่ไม่อยู่ในรายการที่อนุญาตให้กดแทน — ต้องให้คนอ่านและตัดสินใจเอง"""


class PopupSkipPatient(RobotError):
    """เจอ dialog ที่ตั้งไว้ว่า 'ปิดแล้วข้ามคนนี้' เช่นเวชระเบียนถูกคนอื่นเปิดค้างอยู่
    ไม่ใช่ความผิดพลาดของระบบ — ข้ามไปคนถัดไป แล้วค่อยกลับมาทำรอบหลัง

    means_not_saved=True แปลว่า dialog นั้นยืนยันว่า "ไม่มีอะไรถูกบันทึก" (เช่น No Item)
    ใช้ตัดสินได้ว่าแม้จะเกิดหลังกดบันทึก ก็ไม่ต้องกลัวว่ายาออกไปแล้ว
    """

    def __init__(self, message: str, means_not_saved: bool = False):
        super().__init__(message)
        self.means_not_saved = means_not_saved


class PopupReviewPatient(RobotError):
    """เจอคำเตือนที่ 'เภสัชกรต้องอ่านเอง' แต่ปิดหน้าต่างแล้วหน้าจอกลับมาปกติได้ (เช่น Drug Interaction)

    ต่างจาก PopupNeedsHuman ตรงที่ไม่ต้องหยุดทั้งงาน:
        ปิดหน้าต่างให้ → ทำเครื่องหมายคนไข้รายนี้ว่า "ต้องตรวจเอง" → ไปทำคนถัดไปต่อ
    คนที่ติดธงจะไม่ถูกคีย์ซ้ำอัตโนมัติ ต้องให้เภสัชกรเข้าไปดูใน HosXP แล้วกดยืนยันเอง

    เหตุผลที่ต้องมี: ถ้าเจอคำเตือนแบบนี้แล้วหยุดทั้งงาน คนไข้ที่เหลืออีกหลายร้อยคนจะไม่ได้คีย์เลย
    ทั้งที่คนที่ต้องให้เภสัชกรดูมีแค่รายเดียว
    """


_orphans_lock = threading.Lock()
_orphan_threads = []  # เธรดที่หมดเวลาไปแล้วแต่ยังไม่ตาย — อาจไปคลิก/พิมพ์ใส่ HosXP ทีหลัง


def _remember_orphan(th) -> None:
    with _orphans_lock:
        _orphan_threads[:] = [t for t in _orphan_threads if t.is_alive()]
        _orphan_threads.append(th)


def orphan_threads_alive() -> int:
    """นับคำสั่งค้างที่ยังมีชีวิตอยู่ — ใช้ตัดสินว่าปลดล็อกให้รันงานใหม่ได้หรือยัง

    อันตรายของ StepTimeout ไม่ใช่ตัว timeout เอง แต่คือเธรดเก่าที่ยังค้างอยู่
    ถ้ามันไปคลิก/พิมพ์ถึง HosXP ตอนที่งานใหม่กำลังคีย์คนอื่นอยู่ = คีย์ผิดคน
    เมื่อเธรดเหล่านี้ตายหมดแล้ว ความเสี่ยงนั้นก็หมดไปด้วย
    """
    with _orphans_lock:
        _orphan_threads[:] = [t for t in _orphan_threads if t.is_alive()]
        return len(_orphan_threads)


def with_timeout(fn, seconds: float, what: str):
    """เรียกฟังก์ชันโดยมีเพดานเวลา — กัน pywinauto ค้างถาวรตอน HosXP ไม่ตอบ
    (การอ่านข้อความ control ใช้ SendMessage ซึ่งไม่มี timeout ในตัว)

    หมายเหตุสำคัญ: ถ้าหมดเวลา เธรดเบื้องหลังยังทำงานต่อและอาจไปคลิก/พิมพ์ใส่ HosXP ทีหลังได้
    ผู้เรียกจึงต้องถือว่าเซสชันนี้ใช้ต่อไม่ได้แล้ว (ดู HosxpSession.poisoned)
    เธรดที่ค้างจะถูกจดไว้ที่ _orphan_threads เพื่อให้รู้ทีหลังว่าปลอดภัยพอจะรันงานใหม่หรือยัง
    """
    box = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - ส่งต่อให้ผู้เรียกตัดสินใจ
            box["error"] = e

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    th.join(seconds)
    if th.is_alive():
        _remember_orphan(th)
        raise StepTimeout(
            f"ขั้นตอน '{what}' ค้างเกิน {int(seconds)} วินาที — HosXP อาจไม่ตอบสนอง "
            "หรือมีหน้าต่างซ่อนรออยู่ ต้องตรวจหน้าจอ HosXP แล้วกดปลดล็อกในหน้าเว็บก่อนรันต่อ"
        )
    if "error" in box:
        raise box["error"]
    return box.get("value")


def safe_text(handle, timeout_ms: int = TEXT_TIMEOUT_MS) -> str:
    """อ่านข้อความของ control แบบมี timeout — ไม่ค้างถ้าโปรแกรมปลายทางไม่ตอบ
    (ชนิดข้อมูลของ API ประกาศไว้ใน winapi.py ห้ามเรียก ctypes ตรง ๆ ดูเหตุผลในไฟล์นั้น)"""
    try:
        return winapi.get_text(handle, timeout_ms)
    except Exception:
        return ""


def is_visible(handle) -> bool:
    try:
        return winapi.is_visible(handle)
    except Exception:
        return False


def window_alive(handle) -> bool:
    """หน้าต่างยังอยู่จริงไหม — ใช้ตรวจว่า dialog ปิดไปแล้ว (แน่นอนกว่าการดูว่าอะไรอยู่หน้าสุด)"""
    try:
        return winapi.window_alive(handle)
    except Exception:
        return False


def values_equal(a: str, b: str) -> bool:
    """เทียบค่าที่พิมพ์กับค่าที่อ่านกลับมา — ยอมให้ต่างกันที่เลข 0 นำหน้า
    (HosXP ตัด 0 นำหน้าของ HN ให้เอง เช่นพิมพ์ 0931981 แล้วช่องแสดง 931981)"""
    a, b = (a or "").strip(), (b or "").strip()
    if a == b:
        return True
    da, db = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    return bool(da and db and da.lstrip("0") == db.lstrip("0"))


def escape_keys(text: str) -> str:
    out = []
    for ch in text:
        out.append("{" + ch + "}" if ch in _SPECIAL_KEYS else ch)
    return "".join(out)


def is_password_edit(ctrl) -> bool:
    """เช็คว่าช่องนี้เป็นช่องรหัสผ่านหรือไม่ — เชื่อถือได้กว่าการนับลำดับช่อง"""
    try:
        return winapi.is_password_field(ctrl.handle)
    except Exception:
        return False


def _spec_kwargs(spec: dict) -> dict:
    kw = {}
    if spec.get("class_name"):
        kw["class_name"] = spec["class_name"]
    if spec.get("title_re"):
        kw["title_re"] = spec["title_re"]
    if not kw:
        raise ConfigError("selector ว่าง — ตั้งค่า class_name/title_re ใน settings ก่อน")
    return kw


def set_clipboard_text(text: str) -> None:
    """ต้องใช้ SetClipboardText เท่านั้น — SetClipboardData ต้องการ handle ของหน่วยความจำ
    ถ้าส่งสตริงเข้าไปตรง ๆ จะได้ error 6 'The handle is invalid' ทุกครั้ง"""
    import win32clipboard
    import win32con

    last_err = None
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return
        except Exception as e:  # clipboard อาจถูกโปรแกรมอื่นล็อกชั่วคราว
            last_err = e
            time.sleep(0.15)
    raise RobotError(f"ใช้ clipboard ไม่ได้: {last_err}")


def clear_clipboard() -> None:
    try:
        set_clipboard_text(" ")
    except Exception:
        pass


def foreground_info():
    """(handle, class_name, title, root_handle, pid) ของหน้าต่างที่โฟกัสอยู่"""
    try:
        return winapi.foreground_info()
    except Exception:
        return 0, "", "", 0, 0


def child_handles(handle: int, limit: int = 60) -> list:
    """คืน handle ของ control ลูก (EnumChildWindows — ไม่ส่ง message จึงไม่ค้าง)"""
    try:
        return winapi.child_handles(handle, limit)
    except Exception:
        return []


def window_texts(handle: int, budget_seconds: float = 3.0) -> str:
    """อ่านข้อความทั้งหมดในหน้าต่าง (ใช้ดูว่า dialog เขียนว่าอะไร) — จำกัดเวลารวมไว้"""
    deadline = time.time() + budget_seconds
    parts = [safe_text(handle)]
    for h in child_handles(handle):
        if time.time() > deadline:
            parts.append("…(อ่านไม่ครบ)")
            break
        parts.append(safe_text(h))
    return " | ".join(t for t in parts if t)


def read_dialog(handle: int, attempts: int = 10, delay: float = 0.4, timeout_ms: int = 2500) -> dict:
    """แยกส่วนประกอบของ dialog: หัวเรื่อง / ปุ่ม / ข้อความในกล่อง

    อ่านซ้ำหลายรอบเพราะบางที dialog เพิ่งถูกสร้าง ข้อความยังมาไม่ครบ
    การแยก "ข้อความในกล่อง" ออกจากชื่อปุ่มสำคัญมาก เพราะใช้ตัดสินว่า dialog นี้
    เป็นคำเตือนทางคลินิก (ต้องมีข้อความ) หรือเป็นกล่องยืนยันเปล่า ๆ
    """
    info = {
        "title": "", "buttons": [], "message": "", "all": "", "class": "",
        "size": (0, 0), "children": 0, "read_by": "win32",
    }
    cls_name = ""
    try:
        cls_name = winapi.class_name_of(handle)
    except Exception:
        cls_name = ""
    # ขนาดกล่องเป็นเบาะแสสำคัญเมื่ออ่านข้อความไม่ได้ — กล่องของ Windows จะกว้าง/สูงตามความยาวข้อความ
    # จึงบันทึกไว้ใน log ทุกครั้ง เผื่อวันหลังต้องแยกกล่องที่หน้าตาเหมือนกันออกจากกัน
    rect = winapi.window_rect(handle) or (0, 0, 0, 0)
    size = (rect[2] - rect[0], rect[3] - rect[1])
    prev_sig = None
    for i in range(max(1, attempts)):
        # ใช้ timeout ยาวกว่าปกติ เพราะ dialog มักเด้งตอน HosXP กำลังยุ่ง (เช่นหลังกดบันทึก)
        # ถ้าอ่านข้อความไม่ได้ กฎทั้งหมดจะจับไม่ติดแล้วระบบจะหยุดทั้งที่เป็นข้อความธรรมดา
        title = safe_text(handle, timeout_ms)
        buttons, messages = [], []
        kids = child_handles(handle)
        for h in kids:
            try:
                cls = winapi.class_name_of(h)
            except Exception:
                cls = ""
            txt = (safe_text(h, timeout_ms) or "").strip()
            if not txt:
                continue
            if cls.lower().endswith("button"):
                buttons.append(txt)
            else:
                messages.append(txt)
        info = {
            "title": title,
            "buttons": buttons,
            "message": " ".join(messages).strip(),
            "all": " | ".join([title] + buttons + messages).strip(" |"),
            "class": cls_name,
            "size": size,
            "children": len(kids),
            "read_by": "win32",
        }
        if info["message"]:
            break
        # กล่องของ HosXP เป็น Task Dialog ที่ถามค่าข้อความตรง ๆ ไม่ได้ — ถ้าปุ่มอ่านได้ครบ
        # และผลเท่าเดิมสองรอบติด แปลว่ากล่องสร้างเสร็จแล้วแต่ข้อความอ่านด้วยวิธีนี้ไม่ได้จริง
        # ไม่ต้องเสียเวลาลองครบ 10 รอบ ให้ไปใช้ UI Automation แทน
        sig = (info["title"], tuple(info["buttons"]), info["children"])
        if info["buttons"] and sig == prev_sig:
            break
        prev_sig = sig
        if i < attempts - 1:
            time.sleep(delay)

    # ทางที่ 2: กล่องมาตรฐานของ Windows (Task Dialog) เก็บข้อความไว้ในชั้น DirectUI
    # ต้องอ่านผ่าน UI Automation เท่านั้น — ดูเหตุผลเต็มใน uia_text.py
    if not info["message"]:
        from . import uia_text

        found = uia_text.dialog_texts(handle)
        if found:
            info["message"] = " ".join(found).strip()
            info["all"] = " | ".join([info["title"]] + info["buttons"] + found).strip(" |")
            info["read_by"] = "uia"
    return info


class HosxpSession:
    # ค่าเริ่มต้นระดับคลาส เพื่อให้เครื่องมือสำรวจที่สร้าง instance แบบข้าม __init__ ใช้ได้ด้วย
    poisoned = False
    _evidence_left = 40  # เพดานจำนวนไฟล์หลักฐานต่อการรัน 1 ครั้ง
    _struct_evidence_left = 3  # กล่องที่จับได้จากรูปพรรณ เก็บภาพแค่ไม่กี่ใบก็พอเป็นตัวอย่าง
    _confirm_evidence_left = 2  # กล่องยืนยันการบันทึก เก็บภาพ 2 ใบแรกของรอบไว้ตรวจ
    saved_once = False  # โรบอทเคยกดบันทึกสำเร็จในรอบนี้แล้วหรือยัง (ใช้ตัดสินว่า dialog ที่ค้างอยู่เป็นของเราไหม)

    def __init__(self, settings: dict, log):
        self.settings = settings
        self.robot_cfg = settings.get("robot", {})
        self.app_cfg = settings.get("hosxp_app", {})
        self.timing = self.robot_cfg.get("timing", {})
        self._log = log  # callable(step, status, message)
        self.app = None
        self.main = None
        self.main_handle = None  # จำ handle ไว้ตั้งแต่ต้น (ดูเหตุผลใน _remember_main)
        self.poisoned = False  # True เมื่อเคย timeout — เธรดเก่าอาจยังคลิกอยู่ ห้ามใช้ต่อ

    def log(self, step: str, status: str, message: str) -> None:
        """log ต้องไม่มีวันโยน exception — ถ้า DB ล้มหลังบันทึกยาแล้ว จะกลายเป็นคีย์ซ้ำรอบหน้า"""
        try:
            self._log(step, status, message)
        except Exception:
            pass

    def t(self, key: str, default: float) -> float:
        try:
            return float(self.timing.get(key, default))
        except (TypeError, ValueError):
            return default

    @property
    def pid(self) -> int:
        try:
            return int(self.app.process)
        except Exception:
            return -1

    def _remember_main(self) -> None:
        """จำ handle ของหน้าต่างหลักไว้ครั้งเดียว

        ห้ามไปหาใหม่ทุกครั้งที่ต้องเทียบ เพราะการค้นหาต้องอ่าน title ผ่าน SendMessage
        ซึ่งช้า/พลาดได้ตอน HosXP กำลังยุ่ง (เช่นหลังกดบันทึก) ถ้าพลาดแล้วไม่รู้ handle หลัก
        ระบบจะเข้าใจผิดว่าหน้าต่างหลักเองคือ popup แล้วหยุดงานทั้งที่ไม่มีอะไรผิด
        อีกอย่าง: ตอน MDI ลูกถูกขยายเต็มจอ Windows จะต่อท้าย title ด้วย ' - [ชื่อฟอร์มลูก]'
        """
        try:
            self.main_handle = int(self.main.handle)
        except Exception as e:
            self.main_handle = None
            self.log("connect", "retry", f"จำ handle ของหน้าต่างหลักไม่ได้: {e}")

    def guard(self) -> None:
        if self.poisoned:
            raise RobotError(
                "เซสชันนี้เคยค้างมาก่อน จึงใช้ต่อไม่ได้ (อาจมีคำสั่งค้างที่ยังไม่ถึง HosXP) "
                "— ตรวจหน้าจอ HosXP แล้วกดปลดล็อกในหน้าเว็บก่อนรันต่อ"
            )

    def timed(self, fn, seconds: float, what: str):
        """เรียกงานที่แตะ GUI โดยมีเพดานเวลา — ถ้าหมดเวลาถือว่าเซสชันใช้ต่อไม่ได้"""
        self.guard()
        try:
            return with_timeout(fn, seconds, what)
        except StepTimeout:
            self.poisoned = True
            raise

    # ---------- เชื่อมต่อ / login ----------

    def connect(self) -> None:
        main_spec = self.robot_cfg.get("main_window", {})
        login_spec = self.robot_cfg.get("login_form", {})
        timeout = self.t("find_window_timeout", 15)
        exe = self.app_cfg.get("exe_path", "")

        # เช็คสิทธิ์ก่อนทุกอย่าง — ถ้าสิทธิ์ไม่พอ การกดคีย์/คลิกจะถูก Windows บล็อกแบบเงียบ ๆ
        if not is_elevated():
            raise RobotError(ELEVATION_HELP)
        if not winapi.has_interactive_desktop():
            raise RobotError(DESKTOP_LOCKED_HELP)

        try:
            self.app = Application(backend="win32").connect(**_spec_kwargs(main_spec), timeout=2)
            self.main = self.app.window(**_spec_kwargs(main_spec))
            self._remember_main()
            self.log("connect", "info", "พบ HosXP ที่ login อยู่แล้ว — ใช้หน้าต่างเดิม")
            return
        except ElementAmbiguousError:
            raise RobotError(
                "พบหน้าต่างหลักของ HosXP มากกว่าหนึ่งบาน — ปิดให้เหลือบานเดียวก่อนเริ่ม "
                "(เปิดหลายบานเสี่ยงคีย์ผิดหน้าต่าง)"
            )
        except ElementNotFoundError:
            pass
        except Exception as e:
            self.log(
                "connect",
                "info",
                f"ยังต่อหน้าต่างหลักไม่ได้ ({type(e).__name__}: {e or 'ไม่มีรายละเอียด'}) — ลองหาหน้า login",
            )

        login_class = login_spec.get("class_name", "TLoginForm2")
        try:
            self.app = Application(backend="win32").connect(class_name=login_class, timeout=2)
            self.log("connect", "info", "พบหน้าต่าง login ของ HosXP")
        except Exception:
            if not exe:
                raise RobotError("ไม่พบหน้าต่าง HosXP และไม่ได้ตั้งค่า exe_path ใน settings")
            if self._process_running(exe):
                raise RobotError(
                    "มีโปรแกรม HosXP ทำงานอยู่แต่หาหน้าต่างที่ต้องการไม่เจอ "
                    "(อาจถูกย่อ/ค้าง dialog อยู่) — จัดการหน้าจอให้เรียบร้อยแล้วกดเริ่มใหม่ "
                    "ระบบจะไม่เปิด HosXP ซ้ำอีกบานเพื่อกันคีย์ผิดหน้าต่าง"
                )
            self.log("connect", "info", f"เปิดโปรแกรม HosXP: {exe}")
            try:
                self.app = Application(backend="win32").start(exe)
            except Exception as e:
                if "740" in str(e) or "elevation" in str(e).lower():
                    raise RobotError(f"เปิด HosXP ไม่ได้เพราะต้องใช้สิทธิ์ผู้ดูแลระบบ — {ELEVATION_HELP}")
                raise RobotError(f"เปิดโปรแกรม HosXP ไม่สำเร็จ ({exe}): {e}")
            self.app.window(class_name=login_class).wait("visible", timeout=timeout * 2)

        self._login(login_spec, main_spec, timeout)

    def _resolve_login_edits(self, edits, login_spec):
        """หาว่าช่องไหนคือรหัสผู้ใช้/รหัสผ่าน — ตรวจจาก property ของ control ก่อน
        แล้วค่อยถอยไปใช้ลำดับที่ตั้งไว้ใน settings (ลำดับของช่องไม่เหมือนกันทุกเครื่อง)"""
        cfg_u = int(login_spec.get("user_edit_index", 1))
        cfg_p = int(login_spec.get("pass_edit_index", 0))
        if not login_spec.get("detect_password_field", True):
            return cfg_u, cfg_p

        pw_idx = [i for i, e in enumerate(edits) if is_password_edit(e)]
        if len(pw_idx) != 1:
            self.log(
                "login",
                "info",
                f"ตรวจหาช่องรหัสผ่านอัตโนมัติไม่ชัดเจน (พบ {len(pw_idx)} ช่องจาก {len(edits)}) "
                f"— ใช้ลำดับที่ตั้งไว้ ผู้ใช้={cfg_u} รหัสผ่าน={cfg_p}",
            )
            return cfg_u, cfg_p

        p = pw_idx[0]
        others = [i for i in range(len(edits)) if i != p]
        if not others:
            return cfg_u, cfg_p
        u = others[0]
        if (u, p) != (cfg_u, cfg_p):
            self.log(
                "login",
                "info",
                f"ตรวจพบช่องรหัสผ่านอยู่ลำดับ {p} และช่องรหัสผู้ใช้ลำดับ {u} "
                f"— ใช้ค่าที่ตรวจพบแทนค่าที่ตั้งไว้ ({cfg_u}/{cfg_p})",
            )
        return u, p

    @staticmethod
    def _process_running(exe_path: str) -> bool:
        try:
            Application(backend="win32").connect(path=exe_path, timeout=1)
            return True
        except Exception:
            return False

    def _login(self, login_spec: dict, main_spec: dict, timeout: float) -> None:
        if not self.app_cfg.get("auto_login", True):
            raise RobotError("HosXP ยังไม่ได้ login — login เองก่อน แล้วกดเริ่มใหม่ (auto_login ปิดอยู่)")
        user = self.app_cfg.get("username", "")
        password = self.app_cfg.get("password", "")
        if not user or not password:
            raise RobotError("ยังไม่ได้ตั้ง username/password ของ HosXP ในหน้า Settings")

        login = self.app.window(class_name=login_spec.get("class_name", "TLoginForm2"))
        login.wait("visible", timeout=timeout)
        login.set_focus()
        edit_class = login_spec.get("edit_class", "TcxCustomInnerTextEdit")
        edits = login.descendants(class_name=edit_class)
        ui, pi = self._resolve_login_edits(edits, login_spec)
        if len(edits) <= max(ui, pi):
            raise RobotError(
                f"หาช่อง login ไม่ครบ (พบ {len(edits)} ช่อง class {edit_class}) — ปรับ user_edit_index/pass_edit_index"
            )
        self.type_into(edits[ui], user, verify=True)
        try:
            self.type_into(edits[pi], password, verify=False, secret=True)
        finally:
            clear_clipboard()  # ไม่ทิ้งรหัสผ่านไว้ใน clipboard ของเครื่อง
        login.child_window(**_spec_kwargs(login_spec.get("ok_button", {}))).click_input()
        self.log("login", "info", f"login ด้วยบัญชี {user}")

        try:
            login.wait_not("visible", timeout=20)
        except Exception:
            raise RobotError(
                "login ไม่สำเร็จ — หน้าต่างลงชื่อเข้าใช้ของ HosXP ยังค้างอยู่ "
                "ตรวจ username/password ในหน้าตั้งค่า (หรือ login เองแล้วตั้ง hosxp_app.auto_login = false)"
            )

        dept_spec = self.robot_cfg.get("department_form", {})
        try:
            dept = self.app.window(class_name=dept_spec.get("class_name", "TDepartmentSelectForm"))
            dept.wait("visible", timeout=10)
            dept.child_window(**_spec_kwargs(dept_spec.get("ok_button", {}))).click_input()
            self.log("login", "info", "ยืนยันแผนกของเครื่องแล้ว")
        except Exception:
            pass

        self.main = self.app.window(**_spec_kwargs(main_spec))
        self.main.wait("exists visible", timeout=timeout * 2)
        self._remember_main()
        time.sleep(3)

        tips_spec = self.robot_cfg.get("tips_form", {})
        try:
            tips = self.app.window(
                class_name=tips_spec.get("class_name", "TJvForm"),
                title_re=tips_spec.get("title_re", ".*Tips.*"),
            )
            tips.wait("visible", timeout=8)
            tips.child_window(**_spec_kwargs(tips_spec.get("close_button", {}))).click_input()
            self.log("login", "info", "ปิดหน้าต่าง Tips and Tricks แล้ว")
        except Exception:
            pass

    # ---------- ค้นหา control ----------

    def find_all(self, scope, spec: dict, what: str, timeout: float = 20.0, visible_only: bool = True) -> list:
        """หา control ตาม class_name แล้วกรองด้วยข้อความ (อ่านแบบมี timeout)

        - ไม่ใช้ child_window(title_re=...) ของ pywinauto เพราะการเทียบข้อความของมัน
          ใช้ SendMessage แบบไม่มี timeout ซึ่งค้างถาวรได้ถ้า HosXP ไม่ตอบ
        - กรองเฉพาะที่มองเห็นจริง เพราะหน้า IPD มีแท็บซ้อนกัน 6 แท็บที่พิกัดเดียวกัน
          ถ้าเผลอไปคลิกปุ่มของแท็บที่ซ่อนอยู่ เมาส์จะไปโดนอย่างอื่นที่อยู่ข้างบนแทน
        """
        cls = spec.get("class_name") or ""
        title_re = spec.get("title_re") or ""
        if not cls and not title_re:
            raise ConfigError(f"selector ของ '{what}' ว่าง — ตั้งค่า class_name/title_re ก่อน")

        def work():
            items = scope.descendants(class_name=cls) if cls else scope.descendants()
            out = []
            rx = re.compile(title_re) if title_re else None
            for it in items:
                try:
                    h = it.handle
                except Exception:
                    continue
                if visible_only and not is_visible(h):
                    continue
                if rx is not None and not rx.search(safe_text(h) or ""):
                    continue
                out.append(it)
            return out

        return self.timed(work, timeout, f"ค้นหา {what}") or []

    def find_one(self, scope, spec: dict, what: str, timeout: float = 20.0, visible_only: bool = True):
        found = self.find_all(scope, spec, what, timeout, visible_only)
        if not found:
            return None
        if len(found) > 1:
            self.log(
                "find",
                "retry",
                f"พบ '{what}' {len(found)} รายการที่มองเห็นได้ — ใช้รายการแรก "
                "(ถ้าผิดตัวให้ระบุ title_re ให้เจาะจงขึ้น)",
            )
        return found[0]

    def wait_for(self, scope, spec: dict, what: str, timeout: float = 15.0, poll: float = 0.5):
        """รอจน control โผล่ (HosXP สร้าง control บางตัวหลังโหลดคนไข้เท่านั้น)"""
        deadline = time.time() + float(timeout)
        while True:
            found = self.find_one(scope, spec, what, timeout=min(10.0, float(timeout)))
            if found is not None:
                return found
            if time.time() >= deadline:
                return None
            time.sleep(poll)

    def ensure_ipd_form(self, sweep: bool = True):
        """คืน wrapper ของ TIPDRxForm — ต้องเปิดค้างไว้ในโปรแกรมก่อน

        หาไม่เจอครั้งแรกมักไม่ได้แปลว่าไม่ได้เปิดหน้านี้ แต่แปลว่ามีหน้าต่างอื่นลอยทับอยู่
        (บทเรียนจริง 10 ส.ค.: หน้าต่างเตือนแพ้ยาค้างบานเดียว ทำให้คนไข้ 3 คนถัดไปพังต่อกัน
         โดยข้อความ error บอกแค่ 'ไม่พบหน้า IPD' ซึ่งชี้ไปผิดทาง) จึงกวาดหน้าต่างก่อนแล้วลองใหม่
        """
        ipd_spec = self.robot_cfg.get("ipd_form", {})
        spec = {"class_name": ipd_spec.get("class_name"), "title_re": ipd_spec.get("title_re")}
        timeout = self.t("find_window_timeout", 15) * 2
        found = self.find_all(self.main, spec, "หน้า IPD Medication Profile", timeout=timeout)
        if not found and sweep and self.sweep_blocking_windows("เคลียร์หน้าจอ"):
            found = self.find_all(self.main, spec, "หน้า IPD Medication Profile", timeout=timeout)
        if len(found) > 1:
            raise RobotError("พบหน้า IPD Medication Profile มากกว่าหนึ่งบาน — ปิดให้เหลือบานเดียวก่อนเริ่ม")
        if not found:
            raise RobotError(
                "ไม่พบหน้า IPD Medication Profile — เปิดเมนูนี้ใน HosXP ค้างไว้หนึ่งครั้ง แล้วกดเริ่มใหม่ "
                "(หน้าต่างนี้ใช้ซ้ำได้ทุกคน ไม่ต้องปิดระหว่างวัน)" + self.describe_blocking()
            )
        return found[0]

    # ---------- พิมพ์ ----------

    def type_into(self, ctrl, text: str, clear: bool = True, verify: bool = True, secret: bool = False):
        """คลิกช่องแล้ววางข้อความผ่าน clipboard (ทริคจากโปรเจค UiPath — ช่อง DevExpress พิมพ์ตรง ๆ ไม่ติด)
        คืน True เมื่ออ่านค่ากลับมาตรวจแล้วตรง, False เมื่ออ่านค่ากลับไม่ได้"""
        self.guard()
        shown = "***" if secret else text
        for attempt in (1, 2):
            ctrl.click_input()
            time.sleep(0.2)
            if clear:
                keyboard.send_keys("^a{DELETE}", pause=0.05)
                time.sleep(0.15)
            pasted = False
            if attempt == 1:
                # วางผ่าน clipboard ก่อน (ช่อง DevExpress บางช่องไม่รับการพิมพ์ตรง ๆ)
                # แต่ถ้า clipboard ใช้ไม่ได้ ห้ามล้มทั้งคนไข้ — ถอยไปพิมพ์ทีละตัวแทน
                try:
                    set_clipboard_text(text)
                    keyboard.send_keys("^v", pause=0.05)
                    pasted = True
                except Exception as e:
                    self.log("type", "retry", f"ใช้ clipboard ไม่ได้ ({e}) — เปลี่ยนไปพิมพ์ทีละตัวแทน")
            if not pasted:
                keyboard.send_keys(escape_keys(text), pause=0.05, with_spaces=True)
            time.sleep(0.25)

            if not verify:
                return True
            try:
                current = (safe_text(ctrl.handle) or "").strip()
            except Exception:
                current = ""
            if not current:
                self.log("type", "info", f"ช่องนี้อ่านค่ากลับไม่ได้ — ข้ามการตรวจค่าที่พิมพ์ ({shown})")
                return False
            if values_equal(current, text):
                return True
            self.log(
                "type",
                "retry",
                f"ค่าที่พิมพ์ไม่ตรง (ต้องการ '{shown}' ได้ '{current if not secret else '***'}') — ลองใหม่",
            )
        raise RobotError(f"พิมพ์ค่าลงช่องไม่สำเร็จ (ต้องการ '{shown}') — ตรวจหน้าจอ HosXP")

    # ---------- dialog / popup ----------

    def looks_like_error(self, text: str) -> bool:
        words = self.robot_cfg.get("error_popup_keywords", [])
        return any(w and re.search(re.escape(w), text or "", re.IGNORECASE) for w in words)

    def popup_allowed(self, text: str) -> bool:
        """dialog นี้อยู่ในรายการที่อนุญาตให้โรบอทกดแทนคนหรือไม่ (ต้องตั้งเองหลังเห็นของจริง)"""
        for pattern in self.robot_cfg.get("popups", {}).get("allow", []) or []:
            try:
                if pattern and re.search(pattern, text or ""):
                    return True
            except re.error:
                continue
        return False

    def current_popup(self):
        """(handle, cls, title, text) ของ dialog ที่เด้งทับ HosXP อยู่ตอนนี้ หรือ None"""
        h, cls, title, root, pid = foreground_info()
        if not h:
            return None
        if self.main_handle and (root == self.main_handle or h == self.main_handle):
            return None
        # กันพลาดอีกชั้น: หน้าต่างที่เป็นคลาสเดียวกับหน้าต่างหลัก ไม่ใช่ dialog แน่นอน
        main_cls = (self.robot_cfg.get("main_window", {}) or {}).get("class_name") or ""
        if main_cls and cls == main_cls:
            return None
        if not self.main_handle:
            # ไม่รู้ handle หลัก = แยกไม่ออกว่าอะไรเป็น dialog จริง ห้ามเดา
            self.log("popup", "retry", "ยังไม่รู้ handle ของหน้าต่างหลัก — ข้ามการตรวจ dialog รอบนี้")
            return None
        my_pid = self.pid
        if my_pid > 0 and pid != my_pid:
            return None  # หน้าต่างของโปรแกรมอื่น — ไม่ใช่เรื่องของเรา ห้ามไปยุ่ง
        return h, cls, title, (window_texts(h) or title)

    def wait_for_popup(self, timeout: float, poll: float = 0.3):
        """รอ dialog ของ HosXP ที่เด้งขึ้นมาทับ — คืน (handle, cls, title, text) หรือ None"""
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            p = self.current_popup()
            if p is not None:
                return p
            time.sleep(poll)
        return None

    def bring_to_front(self, handle: int) -> None:
        """พยายามดึงหน้าต่างขึ้นหน้าสุดหลายวิธี

        SetForegroundWindow ล้วน ๆ มักถูก Windows ปฏิเสธเมื่อเรียกจากโปรเซสเบื้องหลัง
        pywinauto set_focus() ทำ AttachThreadInput ให้ด้วยจึงสำเร็จบ่อยกว่า
        """
        winapi.set_foreground(handle)
        try:
            from pywinauto.controls.hwndwrapper import HwndWrapper

            HwndWrapper(handle).set_focus()
        except Exception:
            pass

    def click_dialog_button(self, handle: int, spec: dict, what: str) -> bool:
        """คลิกปุ่มในหน้าต่าง/กล่อง dialog แบบเจาะจงชื่อปุ่ม (แน่นอนกว่าการกด Enter ซึ่งไปโดนปุ่ม default)
        คืน True เมื่อหน้าต่างนั้นปิดไปจริง"""
        title_re = spec.get("title_re") or ""
        want_cls = (spec.get("class_name") or "").lower()  # ว่าง = ไม่จำกัดคลาส
        rx = re.compile(title_re) if title_re else None
        target = None
        for h in child_handles(handle, limit=400):
            try:
                cls = winapi.class_name_of(h)
            except Exception:
                continue
            if want_cls and cls.lower() != want_cls:
                continue
            txt = (safe_text(h) or "").strip()
            if rx is None or rx.search(txt):
                target = (h, txt)
                break
        if target is None:
            raise RobotError(f"ไม่พบปุ่ม {what} ในกล่อง dialog — ไม่กดอะไรทั้งสิ้น")

        h, txt = target

        # ยืนยันว่าจุดที่จะคลิกเป็นปุ่มนั้นจริง ๆ ก่อนคลิก
        # การคลิกใช้พิกัดบนจอ ถ้ามีหน้าต่างอื่นบังอยู่ (ดึงขึ้นหน้าสุดไม่สำเร็จ ซึ่ง Windows บล็อกบ่อย)
        # คลิกจะไปตกหน้าต่างอื่นแทน — ในระบบสั่งยาห้ามคลิกมั่วเด็ดขาด
        rect = winapi.window_rect(h)
        if not rect:
            raise RobotError(f"อ่านตำแหน่งปุ่ม '{txt}' ไม่ได้ — ไม่กดอะไรทั้งสิ้น")
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        on_top = 0
        for attempt in range(4):
            self.bring_to_front(handle)
            time.sleep(0.2 if attempt == 0 else 0.4)
            on_top = winapi.window_from_point(cx, cy)
            if on_top == h:
                break
        if on_top != h:
            raise RobotError(
                f"จุดที่จะคลิกปุ่ม '{txt}' ถูกหน้าต่างอื่นบังอยู่ "
                f"(พบ [{winapi.class_name_of(on_top)}] แทน) — ไม่กดเพื่อกันคลิกผิดที่"
            )
        try:
            from pywinauto.controls.hwndwrapper import HwndWrapper

            HwndWrapper(h).click_input()
        except Exception as e:
            if "active desktop" in str(e) or not winapi.has_interactive_desktop():
                raise RobotError(f"กดปุ่ม '{txt}' ไม่ได้ — {DESKTOP_LOCKED_HELP}")
            raise RobotError(f"กดปุ่ม '{txt}' ในกล่อง dialog ไม่สำเร็จ: {e}")
        self.log("dialog", "info", f"กดปุ่ม '{txt}' ในกล่อง dialog แล้ว")
        for _ in range(20):  # รอสูงสุด ~4 วินาที
            time.sleep(0.2)
            if not window_alive(handle):
                return True
        return False

    def click_control(self, ctrl, what: str, attempts: int = 4) -> None:
        """คลิกปุ่มบนฟอร์ม โดยยืนยันก่อนทุกครั้งว่า "จุดที่จะคลิกคือปุ่มนั้นจริง"

        เดิมปุ่ม New / Add Chart F5 / บันทึก ถูกคลิกด้วยพิกัดล้วน ๆ โดยไม่ตรวจอะไรเลย
        ซึ่งเป็นการคลิกที่อันตรายที่สุดในระบบ ถ้ามีหน้าต่างอื่นลอยทับ หรือมือคนไปโดนเมาส์
        จังหวะนั้นพอดี คลิกจะไปตกที่อื่นโดยไม่มีใครรู้

        ตอนนี้: ตรวจว่าจุดกึ่งกลางปุ่มเป็นของปุ่มนั้นจริงก่อนคลิก ถ้าไม่ใช่ก็ดึงหน้าต่างขึ้นมาแล้วลองใหม่
        (มือไปโดนเมาส์ = แค่ลองใหม่ ไม่ใช่พลาดทั้งคน) ครบจำนวนครั้งแล้วยังไม่ได้ค่อยยอมแพ้
        """
        h = int(ctrl.handle)
        blocker = ""
        for attempt in range(max(1, attempts)):
            rect = winapi.window_rect(h)
            if not rect:
                raise RobotError(f"อ่านตำแหน่งของ {what} ไม่ได้ — ไม่กดอะไรทั้งสิ้น")
            cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            on_top = winapi.window_from_point(cx, cy)
            if on_top == h or winapi.is_descendant(h, on_top):
                self.timed(ctrl.click_input, 15, f"กด{what}")
                return
            try:
                blocker = winapi.class_name_of(on_top)
            except Exception:
                blocker = "?"
            if attempt == 0:
                self.log("click", "retry", f"จุดที่จะกด{what}ถูก [{blocker}] บังอยู่ — ดึงหน้าจอขึ้นมาแล้วลองใหม่")
            try:
                if self.main_handle:
                    self.bring_to_front(self.main_handle)
            except Exception:
                pass
            time.sleep(0.4 + 0.3 * attempt)
        raise RobotError(
            f"กด{what}ไม่ได้ เพราะจุดที่จะคลิกถูก [{blocker}] บังอยู่ตลอด — "
            "ไม่กดเพื่อกันคลิกผิดที่" + self.describe_blocking()
        )

    def press_on_popup(self, handle: int, keys: str) -> bool:
        """ส่งปุ่มไปยัง dialog ที่ระบุโดยเฉพาะ (ดึงมาเป็นหน้าต่างหน้าสุดก่อน) แล้วรอจนมันปิด
        คืน True ถ้า dialog นั้นหายไปจริง"""
        winapi.set_foreground(handle)
        time.sleep(0.2)
        fg = foreground_info()[0]
        if fg != handle:
            raise RobotError("ดึง dialog ขึ้นมาเป็นหน้าต่างหน้าสุดไม่ได้ — ไม่กดปุ่มใด ๆ เพื่อกันกดผิดหน้าต่าง")
        keyboard.send_keys(keys, pause=0.05)
        for _ in range(20):  # รอสูงสุด ~4 วินาที
            time.sleep(0.2)
            if not window_alive(handle):
                return True
        return False

    def keep_evidence(self, handle: int, reason: str, kind: str = "halt") -> str:
        """เก็บโครงสร้าง + ภาพของ dialog ไว้เป็นหลักฐาน แล้วคืนข้อความบอกที่อยู่ไฟล์

        จำเป็นเพราะ HosXP ไม่ยอมให้อ่านข้อความในกล่อง เวลาโรบอทหยุดหรือกดอะไรไป
        ถ้าไม่มีภาพเก็บไว้ ก็ไม่มีทางรู้ย้อนหลังว่ามันเจอกล่องอะไร
        มีเพดานจำนวนไฟล์ เพื่อไม่ให้การรันทั้งโรงพยาบาลถมดิสก์
        kind: halt = ตอนหยุดงาน (เก็บเสมอจนเต็มเพดาน) / struct = กล่องที่จับจากรูปพรรณ
              / confirm = กล่องยืนยันการบันทึก (เก็บไม่กี่ใบแรกพอเป็นตัวอย่าง)
        """
        if self._evidence_left <= 0:
            return ""
        if kind == "struct":
            if self._struct_evidence_left <= 0:
                return ""
            self._struct_evidence_left -= 1
        elif kind == "confirm":
            if self._confirm_evidence_left <= 0:
                return ""
            self._confirm_evidence_left -= 1
        self._evidence_left -= 1
        try:
            from .dialog_dump import dump_dialog

            return dump_dialog(handle, reason)
        except Exception as e:  # หลักฐานเก็บไม่ได้ ต้องไม่ทำให้เหตุการณ์หลักหายไป
            return f"(เก็บหลักฐานไม่สำเร็จ: {e})"

    def match_popup_rule(self, text: str, dlg: dict = None, step: str = ""):
        """หา 'กฎ' ที่ตั้งไว้สำหรับ dialog นี้ (robot.popups.rules) — คืน (กฎ, เหตุผลที่จับได้)

        กฎเป็นการตัดสินใจที่คนตั้งไว้ล่วงหน้าอย่างเจาะจง จึงมีน้ำหนักเหนือรายการคำเตือนทั่วไป
        แต่ต้องเขียน match ให้เจาะจงพอ (เช่นข้อความเต็มของ dialog นั้น) ห้ามใช้คำกว้าง ๆ

        ทางที่ 1 (ดีที่สุด): จับจากข้อความในกล่อง
        ทางที่ 2 (สำรอง): จับจาก "รูปพรรณ" ของกล่อง — คลาส + หัวเรื่อง + ชุดปุ่ม
            จำเป็นเพราะ dialog ของ HosXP วาดข้อความเองจนอ่านค่าไม่ได้ (ดู capture.py)
            ใช้ได้เฉพาะเมื่อเข้าเงื่อนไขครบทุกข้อ:
              - อ่านข้อความในกล่องไม่ได้เลย (ถ้าอ่านได้แต่ไม่ตรงกฎ = คนละกล่อง ห้ามเดา)
              - อยู่ในจังหวะที่ระบุไว้ใน contexts เท่านั้น
        """
        rules = self.robot_cfg.get("popups", {}).get("rules", []) or []
        for rule in rules:
            pattern = rule.get("match") or ""
            if not pattern:
                continue
            try:
                if re.search(pattern, text or ""):
                    return rule, "ข้อความในกล่องตรงกับกฎ"
            except re.error:
                continue

        if not dlg or (dlg.get("message") or "").strip():
            return None, ""  # อ่านข้อความได้แต่ไม่ตรงกฎไหน = กล่องอื่น ห้ามเดาจากรูปพรรณ
        for rule in rules:
            shape = rule.get("dialog")
            if not shape:
                continue
            contexts = shape.get("contexts") or []
            if not contexts or step not in contexts:
                continue  # ไม่ระบุจังหวะ = ไม่ยอมให้จับจากรูปพรรณเลย (fail-closed)
            if shape.get("require_after_save") and not self.saved_once:
                # กล่องที่ค้างอยู่ก่อนที่โรบอทจะเคยกดบันทึกในรอบนี้ = ไม่ใช่ของเรา
                # (อาจเป็นกล่องที่คนอื่นเปิดค้างไว้ เช่นยืนยันการลบใบสั่งยา) ห้ามกดแทนเด็ดขาด
                continue
            want_cls = shape.get("class_name") or ""
            if want_cls and want_cls != (dlg.get("class") or ""):
                continue
            title_re = shape.get("title_re") or ""
            if title_re and not re.search(title_re, dlg.get("title") or ""):
                continue
            want_btn = shape.get("buttons")
            if want_btn is not None:
                got = sorted(b.strip() for b in (dlg.get("buttons") or []))
                if sorted(str(b).strip() for b in want_btn) != got:
                    continue
            return rule, (
                f"จับจากรูปพรรณของกล่อง (คลาส {dlg.get('class')!r} หัวเรื่อง {dlg.get('title')!r} "
                f"ปุ่ม {dlg.get('buttons')}) เพราะ HosXP ไม่ยอมให้อ่านข้อความในกล่องนี้"
            )
        return None, ""

    def check_level_rule(self, rule: dict, text: str):
        """ตรวจกฎที่มี 'เพดานความรุนแรง' เช่นคำเตือน Drug Interaction ที่มีคอลัมน์ Level

        คืน (ผ่านไหม, คำอธิบาย) — ออกแบบให้ fail-closed:
        ถ้าอ่านค่า level ไม่ได้ ถือว่า "ไม่ผ่าน" เสมอ ห้ามเดาว่าเป็นระดับต่ำ
        """
        max_level = rule.get("max_level")
        if max_level is None:
            return True, ""
        level_re = rule.get("level_re") or ""
        if not level_re:
            return False, "กฎนี้ตั้งเพดาน level ไว้ แต่ยังไม่ได้ตั้ง level_re สำหรับอ่านค่า level"
        try:
            found = re.findall(level_re, text or "")
        except re.error as e:
            return False, f"level_re ไม่ถูกต้อง: {e}"
        levels = []
        for item in found:
            token = item if isinstance(item, str) else (item[0] if item else "")
            token = str(token).strip()
            if token.isdigit():
                levels.append(int(token))
        if not levels:
            return False, (
                "อ่านค่า level จาก dialog ไม่ได้ "
                "(ตารางของ HosXP อาจเป็นแบบวาดเองที่อ่านข้อความไม่ได้) — ไม่กดปิดให้"
            )
        worst = max(levels)
        if worst > int(max_level):
            return False, f"พบ level {worst} เกินเพดานที่ตั้งไว้ ({max_level}) — ต้องให้เภสัชกรอ่านเอง"
        return True, f"level ที่อ่านได้ = {sorted(set(levels))} ไม่เกินเพดาน {max_level}"

    def dismiss_dialog(self, handle: int, rule: dict, cls: str) -> None:
        """ปิด dialog ตามกฎ — กดปุ่มที่ระบุถ้ามี ไม่งั้นใช้ปุ่มบนคีย์บอร์ด"""
        button = rule.get("button") or {}
        if button.get("title_re"):
            closed = self.click_dialog_button(handle, button, rule.get("label") or "ปิด dialog")
        else:
            closed = self.press_on_popup(handle, rule.get("keys") or "{ENTER}")
        if not closed:
            raise PopupNeedsHuman(f"ปิด dialog [{cls}] ไม่สำเร็จ — หยุดงานไว้ให้คนตรวจ")

    def quick_has_control(self, parent_handle: int, spec: dict, budget: float = 4.0) -> bool:
        """เช็คเร็ว ๆ ว่ามี control ตาม spec โผล่อยู่ในหน้าต่างนี้ไหม — ใช้ Windows API ตรง ๆ

        ต่างจาก find_one() ตรงที่ "ค้างไม่ได้เลย" เพราะทุกการอ่านมีเพดานเวลาในตัว
        และถ้าอ่านไม่ทันก็แค่ตอบว่า "ยังไม่เจอ" ไม่ถือว่าเซสชันพัง

        จำเป็นในจังหวะหลังกดบันทึก (บทเรียนจริง run 292): ตอนนั้น HosXP ยุ่งที่สุด
        การใช้ find_one() ที่มีเพดานเวลาแล้วเลยเวลา จะทำให้ทั้งเซสชันถูกตีตราว่าพัง
        และหยุดงานทั้งหมด ทั้งที่ความจริงแค่ต้องรออีกนิดเดียว
        """
        cls = (spec.get("class_name") or "").lower()
        title_re = spec.get("title_re") or ""
        try:
            rx = re.compile(title_re) if title_re else None
        except re.error:
            return False
        deadline = time.time() + float(budget)
        for h in child_handles(parent_handle, limit=400):
            if time.time() > deadline:
                return False  # อ่านไม่ทัน = ตอบว่ายังไม่เจอ (ผู้เรียกจะวนมาถามใหม่)
            if not is_visible(h):
                continue
            if cls:
                try:
                    if winapi.class_name_of(h).lower() != cls:
                        continue
                except Exception:
                    continue
            if rx is not None and not rx.search(safe_text(h, 400) or ""):
                continue
            return True
        return False

    def find_windows_by_class(self, class_name: str = "", class_re: str = "", timeout: float = 12.0) -> list:
        """หาหน้าต่างของ HosXP ตามชื่อคลาส ไม่ว่าจะอยู่หน้าสุดหรือไม่

        ต่างจาก current_popup() ที่ดูเฉพาะหน้าต่างที่โฟกัสอยู่ — บางหน้าต่างของ HosXP
        (เช่นหน้าต่าง Note ที่เด้งมาเวลาคนไข้มี Note) ไม่ใช่ dialog และอาจไม่ได้อยู่หน้าสุด
        แต่ "ลอยทับ" ปุ่มที่เราจะกด ถ้าไม่ปิดก่อน คลิกจะไปตกที่หน้าต่างนั้นแทน

        รับ class_re ได้ด้วย เพราะ "ชื่อคลาส" กับ "หัวเรื่องหน้าต่าง" ของ Delphi มักต่างกันแค่ตัว T
        (บทเรียนจริง run 324: ตั้งกฎเป็น PtNoteViewForm ซึ่งเป็นหัวเรื่อง แต่คลาสจริงคือ
         TPtNoteViewForm จึงหาไม่เจอเลยสักครั้ง แล้วโรบอทไปหยุดเอาตอนกด New ไปแล้ว)
        """
        if not class_name and not class_re:
            return []

        def work():
            from pywinauto.findwindows import find_elements

            kw = {"class_name_re": class_re} if class_re else {"class_name": class_name}
            try:
                els = find_elements(process=self.pid, top_level_only=False, **kw)
            except Exception:
                els = []
            out = []
            for e in els:
                try:
                    h = int(e.handle)
                except Exception:
                    continue
                if winapi.is_visible(h):
                    out.append(h)
            return out

        return self.timed(work, timeout, f"ค้นหาหน้าต่าง {class_name or class_re}") or []

    def hosxp_forms(self, timeout: float = 12.0) -> list:
        """คืน (handle, คลาส, หัวเรื่อง) ของ "ฟอร์ม" ทุกบานของ HosXP ที่มองเห็นอยู่ตอนนี้

        ใช้หาว่ามีหน้าต่างอะไรลอยทับอยู่บ้าง — ทั้งตอนกวาดปิด และตอนรายงานว่าอะไรขวางอยู่
        กรองเฉพาะคลาสที่ลงท้ายด้วย Form (มาตรฐานการตั้งชื่อของ Delphi) จึงไม่ไปโดน control ย่อยเป็นพัน ๆ ตัว
        """

        def work():
            from pywinauto.findwindows import find_elements

            try:
                els = find_elements(class_name_re=r"^T.*Form$", process=self.pid, top_level_only=False)
            except Exception:
                els = []
            out = []
            for e in els:
                try:
                    h = int(e.handle)
                except Exception:
                    continue
                if not winapi.is_visible(h) or h == self.main_handle:
                    continue
                out.append((h, winapi.class_name_of(h), safe_text(h, 600) or ""))
            return out

        return self.timed(work, timeout, "ไล่ดูหน้าต่างของ HosXP") or []

    def blocking_windows(self, exclude: set = None) -> list:
        """หน้าต่างที่ไม่ใช่หน้าหลักและไม่ใช่หน้า IPD — คือตัวที่อาจลอยทับปุ่มที่เราจะกด"""
        ipd_cls = (self.robot_cfg.get("ipd_form", {}) or {}).get("class_name") or ""
        skip = set(exclude or ())
        out = []
        for h, cls, title in self.hosxp_forms():
            if h in skip or (ipd_cls and cls == ipd_cls):
                continue
            out.append((h, cls, title))
        return out

    def sweep_blocking_windows(self, step: str) -> int:
        """กวาดปิดหน้าต่างแจ้งเตือนที่ลอยทับอยู่ แล้วคืนจำนวนที่ปิดได้

        ปิดให้เฉพาะหน้าต่างที่ "ปุ่มของมันเป็นการปิด/รับทราบเท่านั้น" เช่น ปิด / รับทราบ / Close
        เพราะปุ่มพวกนี้ไม่ได้ตัดสินใจอะไรแทนคน แค่ปิดหน้าต่างทิ้ง

        จงใจไม่แตะปุ่มอย่าง ตกลง/OK/Yes เพราะในกล่องยืนยันมันแปลว่า "ทำเลย"
        (เช่น 'ต้องการลบใบสั่งยาหรือไม่ [ตกลง]') — กล่องพวกนั้นต้องมีกฎเจาะจงเท่านั้น
        และไม่แตะ checkbox ใด ๆ ทั้งสิ้น โดยเฉพาะ 'ไม่ต้องเตือนเรื่องนี้อีก'
        """
        cfg = self.robot_cfg.get("popups", {}) or {}
        if not cfg.get("sweep_unknown_windows", True):
            return 0
        words = cfg.get("sweep_close_buttons") or ["ปิด", "รับทราบ", "Close"]
        rx = re.compile(r"^\s*(" + "|".join(re.escape(w) for w in words) + r")\s*$", re.IGNORECASE)

        closed = 0
        for h, cls, title in self.blocking_windows():
            btn = None
            for k in child_handles(h, limit=400):
                txt = (safe_text(k, 600) or "").strip()
                if txt and rx.match(txt):
                    btn = txt
                    break
            if btn is None:
                continue  # ไม่มีปุ่มปิดที่ปลอดภัย = ไม่ใช่หน้าต่างแจ้งเตือนธรรมดา ปล่อยให้คนจัดการ
            # ไม่บันทึกเนื้อหาในหน้าต่างลง log (อาจเป็นข้อมูลทางคลินิกของคนไข้) บอกแค่คลาสกับปุ่มที่กด
            self.log(step, "info", f"พบหน้าต่าง [{cls}] ลอยทับอยู่ — กดปุ่ม '{btn}' ปิดให้แล้วทำงานต่อ "
                                   + self.keep_evidence(h, f"หน้าต่าง {cls} ที่กวาดปิดอัตโนมัติ", kind="struct"))
            try:
                self.click_dialog_button(h, {"title_re": rx.pattern}, f"ปิดหน้าต่าง {cls}")
                closed += 1
            except RobotError as e:
                self.log(step, "retry", f"ปิดหน้าต่าง [{cls}] ไม่สำเร็จ: {e}")
        return closed

    def describe_blocking(self) -> str:
        """ข้อความสั้น ๆ บอกว่าตอนนี้มีหน้าต่างอะไรลอยทับอยู่ — ใช้ต่อท้ายข้อความ error ให้รู้ต้นเหตุทันที"""
        try:
            wins = self.blocking_windows()
        except Exception:
            return ""
        if not wins:
            return " (ตรวจแล้วไม่มีหน้าต่างอื่นลอยทับอยู่)"
        names = ", ".join(f"[{cls}]{(' ' + title[:40]) if title else ''}" for _h, cls, title in wins[:5])
        return f" — ขณะนี้มีหน้าต่างเหล่านี้เปิดค้างอยู่: {names}"

    def handle_window_rules(self, step: str) -> int:
        """จัดการหน้าต่างที่รู้จักตามชื่อคลาส (robot.popups.rules ที่มี class_name)
        คืนจำนวนหน้าต่างที่จัดการไป"""
        self.guard()
        handled = 0
        for rule in self.robot_cfg.get("popups", {}).get("rules", []) or []:
            cls = rule.get("class_name") or ""
            cls_re = rule.get("class_name_re") or ""
            if not cls and not cls_re:
                continue
            for h in self.find_windows_by_class(cls, cls_re):
                label = rule.get("label") or cls or cls_re
                action = rule.get("action", "halt")
                if action == "halt":
                    raise PopupNeedsHuman(f"พบหน้าต่าง '{label}' ที่ตั้งไว้ว่าต้องให้คนจัดการ")
                # ไม่ log เนื้อหาใน Note ลงไฟล์ (เป็นข้อมูลทางคลินิกของคนไข้) บอกแค่ว่าเจอและปิดให้แล้ว
                self.log(step, "info", f"พบหน้าต่าง '{label}' ลอยทับอยู่ — ปิดก่อนทำงานต่อ")
                self.dismiss_dialog(h, rule, cls or cls_re)
                handled += 1
                if action == "skip_patient":
                    raise PopupSkipPatient(f"ข้ามคนไข้รายนี้เพราะ '{label}'")
                if action == "review_patient":
                    raise PopupReviewPatient(
                        f"พบ '{label}' ของคนไข้รายนี้ — ปิดหน้าต่างให้แล้วและทำเครื่องหมายไว้ว่า "
                        "'ต้องตรวจเอง' เพื่อให้เภสัชกรเข้าไปอ่านและตัดสินใจใน HosXP "
                        "(ไม่คีย์ซ้ำให้อัตโนมัติ) แล้วทำคนถัดไปต่อ"
                    )
        return handled

    def handle_popups(self, step: str, max_count: int, delay: float) -> list:
        """จัดการ dialog ที่เด้งขึ้นมาระหว่างทำงาน

        ลำดับการตัดสิน:
            1. ตรงกับกฎที่ตั้งไว้ (popups.rules) -> ทำตามกฎ (ปิดแล้วไปต่อ / ปิดแล้วข้ามคนนี้ / หยุด)
            2. เข้าข่ายคำเตือน (error_popup_keywords) -> หยุด ให้คนอ่านเอง
            3. อยู่ในรายการอนุญาต (popups.allow) -> กด Enter ให้
            4. นอกนั้นทั้งหมด -> หยุด ปล่อย dialog ค้างไว้บนจอ
        """
        self.guard()
        # จัดการหน้าต่างที่รู้จักตามคลาสก่อน (พวกที่ไม่ใช่ dialog และอาจไม่ได้อยู่หน้าสุด)
        self.handle_window_rules(step)
        # แล้วกวาดหน้าต่างแจ้งเตือนที่ยังไม่รู้จักแต่มีปุ่มปิดชัดเจน
        # (ถ้าไม่กวาด หน้าต่างเดียวที่ค้างจะทำให้คนไข้ที่เหลือทั้งรอบพังต่อกันเป็นทอด ๆ)
        self.sweep_blocking_windows(step)
        seen = []
        idle = 0
        for _ in range(max(1, int(max_count))):
            time.sleep(delay)
            p = self.current_popup()
            if p is None:
                idle += 1
                if idle >= 2:
                    break
                continue
            h, cls, title, _quick = p
            dlg = read_dialog(h)
            text = dlg["all"] or title
            seen.append(f"{cls}:{title}")

            rule, matched_by = self.match_popup_rule(text, dlg, step)
            if rule is not None:
                action = rule.get("action", "halt")
                label = rule.get("label") or rule.get("match")
                if action == "halt":
                    raise PopupNeedsHuman(
                        f"เจอ dialog '{label}' ที่ตั้งไว้ว่าต้องให้คนจัดการ: {text[:300]} "
                        + self.keep_evidence(h, f"dialog ตามกฎ '{label}' ที่ต้องให้คนจัดการ")
                    )
                ok, why = self.check_level_rule(rule, text)
                if not ok:
                    raise PopupNeedsHuman(
                        f"เจอ dialog '{label}' แต่ยังกดปิดให้ไม่ได้: {why} "
                        f"— หยุดงานและปล่อยหน้าต่างนี้ค้างไว้ให้เภสัชกรอ่านและตัดสินใจเอง | {text[:300]} "
                        + self.keep_evidence(h, f"dialog '{label}' ที่กดปิดให้ไม่ได้: {why}")
                    )
                # บันทึกไว้เป็นหลักฐานว่าโรบอทกดผ่านคำเตือนอะไรไปบ้าง (ตรวจย้อนหลังได้)
                evidence = ""
                if rule.get("dialog") and "รูปพรรณ" in matched_by:
                    # กล่องที่จับได้จากรูปพรรณ ไม่ใช่จากข้อความ = เก็บภาพไว้ให้ตรวจย้อนหลังได้ว่ากดอะไรไป
                    evidence = " " + self.keep_evidence(h, f"กดตามกฎ '{label}' โดยจับจากรูปพรรณ", kind="struct")
                self.log(
                    step,
                    "info",
                    f"เจอ dialog ตามกฎ '{label}' ({matched_by})"
                    f"{(' — ' + why) if why else ''}: {text[:300]}{evidence}",
                )
                self.dismiss_dialog(h, rule, cls)
                if action == "skip_patient":
                    raise PopupSkipPatient(
                        f"ข้ามคนไข้รายนี้เพราะ '{label}' — ปิด dialog ให้แล้ว "
                        "รอบถัดไปจะกลับมาทำให้เองถ้าสถานการณ์คลี่คลาย",
                        means_not_saved=bool(rule.get("means_not_saved")),
                    )
                if action == "review_patient":
                    raise PopupReviewPatient(
                        f"พบ '{label}' ของคนไข้รายนี้ — ปิด dialog ให้แล้วและทำเครื่องหมายไว้ว่า "
                        "'ต้องตรวจเอง' เพื่อให้เภสัชกรเข้าไปอ่านและตัดสินใจใน HosXP "
                        "(ไม่คีย์ซ้ำให้อัตโนมัติ) แล้วทำคนถัดไปต่อ"
                    )
                idle = 0
                continue

            if self.looks_like_error(text):
                raise PopupNeedsHuman(
                    f"HosXP ขึ้นข้อความที่ต้องให้คนอ่านเอง: [{cls}] {text[:300]} "
                    "— หยุดงานและปล่อยหน้าต่างนี้ค้างไว้ให้เภสัชกรจัดการ "
                    + self.keep_evidence(h, "dialog ที่เข้าข่ายคำเตือน")
                )
            if not self.popup_allowed(text):
                raise PopupNeedsHuman(
                    f"เจอ dialog ที่ยังไม่ได้อนุญาตให้โรบอทกดแทน: [{cls}] {text[:300]} "
                    "— หยุดงานไว้ก่อน "
                    + self.keep_evidence(h, "dialog ที่ยังไม่รู้จัก")
                )
            self.log(step, "info", f"ปิด dialog ที่อนุญาตไว้: [{cls}] {text[:200]}")
            if not self.press_on_popup(h, "{ENTER}"):
                raise PopupNeedsHuman(
                    f"กด Enter ใส่ dialog [{cls}] แล้วยังไม่ปิด — หยุดงานไว้ให้คนตรวจ"
                )
            idle = 0
        return seen
