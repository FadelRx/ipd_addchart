"""ประกาศชนิดข้อมูลของ Windows API ที่ใช้ ให้ถูกต้องตามระบบ 64-bit

ทำไมต้องมีไฟล์นี้ (บทเรียนจริง 2026-08-03):
    เดิมเรียก user32.SendMessageTimeoutW โดยไม่ประกาศ argtypes และรับผลลัพธ์เป็น c_ulong (4 ไบต์)
    แต่พารามิเตอร์สุดท้ายของมันคือ PDWORD_PTR ซึ่งบน 64-bit คือ 8 ไบต์
    Windows จึงเขียนทับหน่วยความจำเกินไป 4 ไบต์ทุกครั้งที่อ่านข้อความจาก control
    ผลคือ heap corruption แล้วโปรเซส Python ตายทั้งตัว (exception 0xC0000374)
    ตายกลางคันแบบนี้อันตรายเป็นพิเศษ เพราะอาจตายคาระหว่างที่กำลังคีย์ยาอยู่

กติกา: ทุกฟังก์ชันที่เรียกจากไฟล์นี้ต้องประกาศ argtypes/restype ครบเสมอ ห้ามเรียกลอย ๆ
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# DWORD_PTR / ULONG_PTR = ขนาดเท่าพอยน์เตอร์ (4 ไบต์บน 32-bit, 8 ไบต์บน 64-bit)
ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
EM_GETPASSWORDCHAR = 0x00D2
SMTO_ABORTIFHUNG = 0x0002
GA_ROOT = 2
GWL_STYLE = -16
ES_PASSWORD = 0x0020

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    ULONG_PTR,
    ctypes.c_void_p,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ULONG_PTR),
]
user32.SendMessageTimeoutW.restype = LRESULT

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG

user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL


def send_timeout(handle, msg: int, wparam=0, lparam=None, timeout_ms: int = 800):
    """ส่ง message แบบมีเพดานเวลา — คืน (สำเร็จหรือไม่, ค่าที่ได้)"""
    if not handle:
        return False, 0
    result = ULONG_PTR(0)
    ok = user32.SendMessageTimeoutW(
        handle, msg, wparam, lparam, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result)
    )
    return bool(ok), int(result.value)


def get_text(handle, timeout_ms: int = 800) -> str:
    """อ่านข้อความของ control/หน้าต่างแบบไม่ค้าง (ใช้ SendMessageTimeout)"""
    ok, length = send_timeout(handle, WM_GETTEXTLENGTH, 0, None, timeout_ms)
    if not ok or length <= 0:
        return ""
    length = min(length, 8192)  # กันค่าเพี้ยนจนจองหน่วยความจำมหาศาล
    buf = ctypes.create_unicode_buffer(length + 1)
    ok, _ = send_timeout(
        handle, WM_GETTEXT, length + 1, ctypes.cast(buf, ctypes.c_void_p), timeout_ms
    )
    return buf.value if ok else ""


def is_password_field(handle, timeout_ms: int = 500) -> bool:
    try:
        if user32.GetWindowLongW(handle, GWL_STYLE) & ES_PASSWORD:
            return True
    except Exception:
        pass
    ok, ch = send_timeout(handle, EM_GETPASSWORDCHAR, 0, None, timeout_ms)
    return bool(ok and ch)


def window_alive(handle) -> bool:
    if not handle:
        return False
    return bool(user32.IsWindow(handle) and user32.IsWindowVisible(handle))


def is_visible(handle) -> bool:
    return bool(handle and user32.IsWindowVisible(handle))


def foreground_info():
    """(handle, class_name, title, root_handle, pid) ของหน้าต่างที่โฟกัสอยู่"""
    h = user32.GetForegroundWindow()
    if not h:
        return 0, "", "", 0, 0
    cls_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, cls_buf, 256)
    title_buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, title_buf, 512)
    root = user32.GetAncestor(h, GA_ROOT) or 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return int(h), cls_buf.value, title_buf.value, int(root), int(pid.value)


def set_foreground(handle) -> bool:
    try:
        return bool(user32.SetForegroundWindow(handle))
    except Exception:
        return False


user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL
DESKTOP_READOBJECTS = 0x0001


user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
user32.GetThreadDesktop.restype = wintypes.HANDLE
user32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
]
user32.GetUserObjectInformationW.restype = wintypes.BOOL
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
UOI_NAME = 2


def _desktop_name(handle) -> str:
    if not handle:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    need = wintypes.DWORD(0)
    try:
        if user32.GetUserObjectInformationW(
            handle, UOI_NAME, ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(need)
        ):
            return buf.value
    except Exception:
        pass
    return ""


def has_interactive_desktop() -> bool:
    """โปรเซสนี้ส่งเมาส์/คีย์บอร์ดไปที่หน้าจอที่ผู้ใช้เห็นอยู่ได้ไหม

    ไม่พอที่จะเช็คแค่ว่าเปิด input desktop ได้ — ต้องเทียบว่า "เดสก์ท็อปของเธรดเรา"
    กับ "เดสก์ท็อปที่กำลังรับอินพุตอยู่" เป็นอันเดียวกัน
    ถ้าจอถูกล็อก เดสก์ท็อปที่รับอินพุตจะกลายเป็น Winlogon ซึ่งเราส่งอะไรเข้าไปไม่ได้เลย
    """
    try:
        h_input = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not h_input:
            return False
        try:
            input_name = _desktop_name(h_input)
        finally:
            user32.CloseDesktop(h_input)
        mine = _desktop_name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
        if not input_name or not mine:
            return True  # อ่านชื่อไม่ได้ก็อย่าไปขวางการทำงาน
        return input_name.lower() == mine.lower()
    except Exception:
        return True


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL


def window_from_point(x: int, y: int) -> int:
    """หน้าต่างที่อยู่บนสุด ณ พิกัดนี้ — ใช้ยืนยันว่าคลิกแล้วจะโดนสิ่งที่ตั้งใจจริง"""
    try:
        return int(user32.WindowFromPoint(POINT(int(x), int(y))) or 0)
    except Exception:
        return 0


def window_rect(handle):
    """(left, top, right, bottom) ของหน้าต่าง หรือ None"""
    r = wintypes.RECT()
    try:
        if user32.GetWindowRect(handle, ctypes.byref(r)):
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return None


user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
user32.GetDlgCtrlID.restype = ctypes.c_int


def print_window(handle, hdc, flags: int = 2) -> bool:
    """วาดภาพหน้าต่างลงใน device context — ใช้เก็บภาพ dialog เป็นหลักฐาน"""
    try:
        return bool(user32.PrintWindow(handle, hdc, flags))
    except Exception:
        return False


def is_descendant(parent, child) -> bool:
    """child อยู่ใต้ parent หรือเป็นตัวเดียวกันไหม — ใช้ยืนยันว่าคลิกโดนปุ่มที่ตั้งใจ
    (ปุ่มบางตัวมี control ลูกซ้อนอยู่ จุดกึ่งกลางจึงคืน handle ของลูกแทนตัวปุ่มเอง)"""
    if not parent or not child:
        return False
    h = int(child)
    for _ in range(12):   # กันลูปไม่รู้จบถ้าโครงสร้างหน้าต่างเพี้ยน
        if h == int(parent):
            return True
        nxt = user32.GetParent(h)
        if not nxt or int(nxt) == h:
            return False
        h = int(nxt)
    return False


def control_id(handle) -> int:
    """เลขประจำ control ในกล่อง dialog (เช่น ปุ่ม Yes = 6, No = 7, ข้อความ = 65535)"""
    try:
        return int(user32.GetDlgCtrlID(handle))
    except Exception:
        return 0


def window_style(handle) -> int:
    try:
        return int(user32.GetWindowLongW(handle, GWL_STYLE)) & 0xFFFFFFFF
    except Exception:
        return 0


def class_name_of(handle) -> str:
    if not handle:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, buf, 256)
    return buf.value


def child_handles(handle, limit: int = 60) -> list:
    out = []
    if not handle:
        return out

    def _cb(hwnd, _lparam):
        out.append(int(hwnd))
        return 1 if len(out) < limit else 0

    cb = WNDENUMPROC(_cb)  # ต้องเก็บ reference ไว้ตลอดการเรียก ไม่งั้นถูกเก็บกวาดแล้วพัง
    try:
        user32.EnumChildWindows(handle, cb, 0)
    except Exception:
        pass
    return out

# ---------- สั่งงานแบบส่ง message ตรงไปที่ control (ไม่ใช้เมาส์/คีย์บอร์ดจริง) ----------
# พิสูจน์กับ HosXP จริงแล้ว 18 ส.ค. 2569 ว่าใช้ได้ "แม้ HosXP ไม่ได้เป็นหน้าต่างหน้าสุด"
# (ตอนทดสอบหน้าสุดคือ ConsoleWindowClass) จึงเปิดทางให้ผู้ใช้ทำงานอื่นระหว่างโรบอทคีย์ได้
# ข้อสำคัญ: โปรเซสต้องมีสิทธิ์แอดมิน ไม่งั้น Windows บล็อกการส่ง message แบบ "เขียน" เงียบ ๆ

WM_SETTEXT = 0x000C
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
EM_SETSEL = 0x00B1
EM_REPLACESEL = 0x00C2
BM_CLICK = 0x00F5
VK_RETURN = 0x0D

user32.SendMessageTimeoutW.restype = LRESULT  # ประกาศไว้แล้วข้างบน คงไว้ให้ชัด


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL


def focused_control(handle) -> int:
    """control ที่ถือโฟกัสคีย์บอร์ดอยู่ "ภายในเธรดของหน้าต่างนั้น"

    ใช้ GetGUIThreadInfo ไม่ใช่ GetFocus เพราะ GetFocus บอกได้เฉพาะเธรดตัวเอง
    และค่านี้ไม่ขึ้นกับว่าหน้าต่างไหนเป็นหน้าสุด จึงส่งปุ่มไปถูกที่แม้ HosXP อยู่ข้างหลัง
    """
    if not handle:
        return 0
    try:
        tid = user32.GetWindowThreadProcessId(handle, None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            return int(info.hwndFocus or 0)
    except Exception:
        pass
    return 0


def set_text(handle, text: str, timeout_ms: int = 2000) -> bool:
    """ใส่ข้อความลง control ตรง ๆ — คืน True เมื่อส่งสำเร็จ (ผู้เรียกต้องอ่านกลับมาตรวจเองเสมอ)"""
    ok, _ = send_timeout(handle, WM_SETTEXT, 0, ctypes.c_wchar_p(text), timeout_ms)
    return bool(ok)


def replace_text(handle, text: str, timeout_ms: int = 2000) -> bool:
    """เลือกข้อความทั้งหมดแล้ววางทับ — ใช้กับช่องที่ไม่ยอมรับ WM_SETTEXT"""
    send_timeout(handle, EM_SETSEL, 0, ctypes.c_void_p(-1), timeout_ms)
    ok, _ = send_timeout(handle, EM_REPLACESEL, 1, ctypes.c_wchar_p(text), timeout_ms)
    return bool(ok)


def click_button(handle, timeout_ms: int = 4000) -> bool:
    """กดปุ่มโดยส่ง BM_CLICK — ไปถึงปุ่มนั้นตรง ๆ จึงคลิกผิดหน้าต่างไม่ได้เลย"""
    ok, _ = send_timeout(handle, BM_CLICK, 0, None, timeout_ms)
    return bool(ok)


user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ULONG_PTR, ctypes.c_ssize_t]
user32.PostMessageW.restype = wintypes.BOOL


def press_key(handle, vk: int = VK_RETURN) -> bool:
    """กดปุ่มคีย์บอร์ด 1 ครั้งใส่ control ที่ระบุ (ไม่ผ่านคีย์บอร์ดจริง)"""
    if not handle:
        return False
    try:
        user32.PostMessageW(handle, WM_KEYDOWN, vk, 0)
        user32.PostMessageW(handle, WM_KEYUP, vk, 0xC0000000)
        return True
    except Exception:
        return False
