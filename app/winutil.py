"""ตัวช่วยเกี่ยวกับ Windows — เช็คสิทธิ์ผู้ดูแลระบบ

HosXP (hosmy.exe) ประกาศ requireAdministrator ไว้ในตัวโปรแกรม จึงรันด้วยสิทธิ์สูงเสมอ
Windows จะบล็อกไม่ให้โปรเซสสิทธิ์ต่ำส่งคีย์บอร์ด/เมาส์ไปยังหน้าต่างของโปรเซสสิทธิ์สูง (UIPI)
ดังนั้นโปรแกรมนี้ต้องรันแบบ Run as administrator ไม่งั้นโรบอทจะกดอะไรไม่เข้าเลยโดยไม่มี error
"""
import ctypes


def is_elevated() -> bool:
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.IsUserAnAdmin.argtypes = []
        shell32.IsUserAnAdmin.restype = ctypes.c_int
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


ELEVATION_HELP = (
    "โปรแกรมนี้ไม่ได้รันด้วยสิทธิ์ผู้ดูแลระบบ แต่ HosXP บังคับให้ใช้สิทธิ์ผู้ดูแลระบบ "
    "ทำให้เปิดโปรแกรมไม่ได้ และถึงเปิดค้างไว้เองก็สั่งคีย์บอร์ด/เมาส์เข้าไปไม่ได้ (Windows บล็อก) "
    "— ปิดหน้าต่างนี้แล้วเปิดใหม่ด้วย run.bat (จะมีหน้าต่าง UAC ขึ้นให้กด Yes) "
    "หรือคลิกขวาที่ run.bat แล้วเลือก Run as administrator"
)
