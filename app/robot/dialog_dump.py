"""เก็บหลักฐานของ dialog ที่โรบอทไม่รู้จัก (โครงสร้าง + ภาพ)

ที่ต้องมีเพราะ dialog ของ HosXP อ่านข้อความไม่ได้ (ดู capture.py)
เวลาโรบอทหยุดเพราะเจอกล่องที่ไม่รู้จัก ถ้าไม่เก็บอะไรไว้เลย
คนที่มาดูทีหลังจะไม่มีทางรู้ว่ามันเจอกล่องอะไร และจะตั้งกฎให้ถูกไม่ได้

ไฟล์ที่ได้เก็บไว้ในเครื่องนี้เท่านั้น (data\\scans\\) — ภาพอาจมีข้อมูลคนไข้
จึงห้ามส่งออกนอกเครื่องโดยไม่ได้ตั้งใจ
"""
from datetime import datetime

from ..config import DATA_DIR
from . import winapi
from .capture import save_window_png


def _describe(handle: int) -> list:
    lines = []
    cls = winapi.class_name_of(handle)
    title = winapi.get_text(handle, 2000)
    rect = winapi.window_rect(handle) or (0, 0, 0, 0)
    size = (rect[2] - rect[0], rect[3] - rect[1])
    lines.append(f"class={cls!r} title={title!r}")
    lines.append(f"rect={rect} ขนาด={size[0]}x{size[1]} style=0x{winapi.window_style(handle):08X}")
    kids = winapi.child_handles(handle, limit=200)
    lines.append(f"control ลูก {len(kids)} ตัว:")
    for i, k in enumerate(kids):
        kcls = winapi.class_name_of(k)
        ktxt = winapi.get_text(k, 2000)
        ok_len, length = winapi.send_timeout(k, winapi.WM_GETTEXTLENGTH, 0, None, 2000)
        krect = winapi.window_rect(k) or (0, 0, 0, 0)
        lines.append(
            f"  [{i:02d}] class={kcls!r} id={winapi.control_id(k)} "
            f"style=0x{winapi.window_style(k):08X} ความยาวข้อความ={'?' if not ok_len else length} "
            f"ข้อความ={ktxt!r} rect={krect}"
        )
    return lines


def dump_dialog(handle: int, reason: str = "") -> str:
    """เก็บโครงสร้าง + ภาพของหน้าต่างนี้ไว้ คืนข้อความสั้น ๆ บอกที่อยู่ไฟล์ (ห้าม raise)"""
    try:
        # ต้องมีมิลลิวินาทีด้วย ไม่งั้น dialog 2 ใบที่เกิดในวินาทีเดียวกันจะเขียนทับกัน
        # (เจอจริงตอนทดสอบ: กล่องยืนยันกับกล่องที่ตามมาติด ๆ ได้ชื่อไฟล์เดียวกัน)
        now = datetime.now()
        stamp = now.strftime("%Y%m%d_%H%M%S_") + f"{now.microsecond // 1000:03d}"
        out_dir = DATA_DIR / "scans"
        out_dir.mkdir(parents=True, exist_ok=True)
        txt_path = out_dir / f"dialog_{stamp}.txt"
        png_path = out_dir / f"dialog_{stamp}.png"

        lines = [
            f"เวลา {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"เหตุผลที่เก็บ: {reason}",
            "",
            "=== หน้าต่างที่เจอ ===",
        ]
        lines += _describe(handle)

        got_png = save_window_png(handle, png_path)
        lines.append("")
        lines.append(f"ภาพหน้าจอ: {'บันทึกแล้ว ' + png_path.name if got_png else 'บันทึกไม่สำเร็จ'}")
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        return f"เก็บหลักฐานไว้ที่ data\\scans\\{txt_path.name}" + (f" และ {png_path.name}" if got_png else "")
    except Exception as e:  # การเก็บหลักฐานล้มเหลว ต้องไม่ทำให้งานหลักพังเพิ่ม
        return f"(เก็บหลักฐานไม่สำเร็จ: {e})"
