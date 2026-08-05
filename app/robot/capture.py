"""ถ่ายภาพหน้าต่างเก็บไว้เป็นหลักฐานตอนโรบอทหยุดเพราะเจอ dialog ที่ไม่รู้จัก

ทำไมต้องมี: dialog ของ HosXP วาดข้อความเองโดยไม่ผ่าน control มาตรฐาน
เราจึง "ถามค่าข้อความ" ไม่ได้ (WM_GETTEXT คืนค่าว่าง ทั้งที่บนจอมีตัวหนังสือชัด ๆ)
เมื่ออ่านไม่ได้ก็ต้องเก็บภาพไว้ ไม่งั้นเวลาโรบอทหยุดจะไม่มีใครรู้ว่ามันเจออะไร

เก็บเป็น PNG โดยไม่ต้องลง Pillow (เขียนไฟล์ PNG เองด้วย zlib ซึ่งมีอยู่แล้วใน Python)
ภาพเก็บไว้ในเครื่องนี้เท่านั้น (data\\scans\\) ไม่ได้ส่งออกไปไหน
"""
import struct
import zlib

MAX_SIDE = 2400  # กันภาพใหญ่เกินจนกินหน่วยความจำ


def _png_bytes(width: int, height: int, rows_rgb: list) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + row for row in rows_rgb)  # filter type 0 ทุกแถว
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def save_window_png(handle: int, path) -> bool:
    """เก็บภาพหน้าต่างลงไฟล์ PNG — คืน True เมื่อสำเร็จ (ล้มเหลวถือว่าไม่เป็นไร ห้ามทำให้งานหลักพัง)"""
    import win32con
    import win32gui
    import win32ui

    from . import winapi

    rect = winapi.window_rect(handle)
    if not rect:
        return False
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0 or w > MAX_SIDE or h > MAX_SIDE:
        return False

    win_dc = mfc_dc = save_dc = bmp = None
    try:
        win_dc = win32gui.GetWindowDC(handle)
        mfc_dc = win32ui.CreateDCFromHandle(win_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)

        # PW_RENDERFULLCONTENT = 2 — ได้ภาพแม้มีหน้าต่างอื่นบังบางส่วน
        if not winapi.print_window(handle, save_dc.GetSafeHdc(), 2):
            if not winapi.print_window(handle, save_dc.GetSafeHdc(), 0):
                # ทางสำรองสุดท้าย: ก๊อปจากภาพหน้าจอตรงตำแหน่งหน้าต่างนั้น
                screen = win32gui.GetDC(0)
                try:
                    screen_dc = win32ui.CreateDCFromHandle(screen)
                    save_dc.BitBlt((0, 0), (w, h), screen_dc, (left, top), win32con.SRCCOPY)
                finally:
                    win32gui.ReleaseDC(0, screen)

        bits = bmp.GetBitmapBits(True)  # BGRA เรียงจากบนลงล่าง
        stride = w * 4
        rows = []
        for y in range(h):
            line = bits[y * stride : (y + 1) * stride]
            rows.append(bytes(b for i in range(0, len(line), 4) for b in (line[i + 2], line[i + 1], line[i])))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_png_bytes(w, h, rows))
        return True
    except Exception:
        return False
    finally:
        try:
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass
        try:
            if save_dc is not None:
                save_dc.DeleteDC()
            if mfc_dc is not None:
                mfc_dc.DeleteDC()
        except Exception:
            pass
        try:
            if win_dc:
                win32gui.ReleaseDC(handle, win_dc)
        except Exception:
            pass
