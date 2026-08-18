"""แถบความคืบหน้าลอยอยู่เหนือหน้าจอ HosXP

ทำไมต้องมี: พอกดเริ่มคีย์ โรบอทจะดึงหน้าต่าง HosXP ขึ้นมาหน้าสุด หน้าเว็บจึงถูกบังจนมองไม่เห็น
ว่าทำถึงตึกไหน คนที่เท่าไหร่ สำเร็จกี่คน — เดิมต้องขยับเมาส์ไปสลับหน้าต่างดู ซึ่งเป็นจังหวะที่
มือไปโดนเมาส์แล้วทำให้โรบอทพลาดได้ แถบนี้จึงลอยอยู่มุมจอตลอดเวลาที่ทำงาน

ทำไมต้องเป็น "คนละโปรเซส": tkinter ใช้ในเธรดแยกของโปรเซสเดียวกันไม่ได้อย่างปลอดภัย
(Tcl ฟ้อง "async handler deleted by the wrong thread" ตอนปิด ซึ่งทำโปรเซสตายได้)
ถ้าเซิร์ฟเวอร์ตายกลางคันระหว่างคีย์ยา = อันตราย จึงแยกออกไปเป็นโปรเซสเล็ก ๆ ต่างหาก
แล้วให้มันดึงสถานะเองผ่าน /api/status ที่มีอยู่แล้ว — ตายยังไงก็ไม่กระทบงานคีย์ยา

ข้อบังคับ: ห้ามแย่งโฟกัสเด็ดขาด ถ้าแย่งไปตอนโรบอทกำลังพิมพ์ ตัวอักษรจะตกใส่แถบนี้แทน
จึงตั้ง WS_EX_NOACTIVATE ไว้ กดยังไงหน้าต่างนี้ก็ไม่รับโฟกัส
"""
import json
import subprocess
import sys
import urllib.request

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
IDLE_EXIT_SECONDS = 20.0   # ไม่มีงานรันติดกันเกินเท่านี้ = ปิดตัวเอง


def launch(status_url: str):
    """เปิดแถบความคืบหน้าเป็นโปรเซสแยก — คืน Popen หรือ None (ล้มเหลวได้ ห้ามทำให้งานหลักพัง)

    เรียกไฟล์นี้ตรง ๆ ด้วย path เต็ม ไม่ใช้ -m app.overlay เพราะแบบนั้นต้องให้ cwd
    เป็นโฟลเดอร์โปรเจคพอดี ซึ่งไม่จริงเสมอไป (เช่นตอนเปิดเองผ่าน Scheduled Task)
    แล้วแถบจะไม่ขึ้นโดยไม่มีใครรู้ — ไฟล์นี้ใช้แต่ของใน stdlib จึงรันเดี่ยว ๆ ได้
    """
    try:
        import os

        here = os.path.abspath(__file__)
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        return subprocess.Popen(
            [sys.executable, here, status_url],
            cwd=os.path.dirname(os.path.dirname(here)),
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None


def _no_focus(root) -> None:
    """ทำให้หน้าต่างนี้ไม่รับโฟกัสไม่ว่าจะกดยังไง (สำคัญที่สุดของไฟล์นี้)"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        hwnd = int(root.winfo_id())
        top = user32.GetParent(hwnd) or hwnd
        cur = user32.GetWindowLongW(top, GWL_EXSTYLE)
        user32.SetWindowLongW(top, GWL_EXSTYLE, cur | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def main(status_url: str) -> int:
    import time
    import tkinter as tk

    root = tk.Tk()
    root.overrideredirect(True)          # ไม่มีกรอบ ไม่ขึ้นใน taskbar
    root.attributes("-topmost", True)    # ลอยเหนือ HosXP เสมอ
    root.configure(bg="#0f172a")
    w, h = 430, 132
    root.geometry(f"{w}x{h}+{root.winfo_screenwidth() - w - 24}+24")   # มุมขวาบน

    head = tk.Label(root, text="IPDaddChart", bg="#0f172a", fg="#93c5fd",
                    font=("Segoe UI", 11, "bold"), anchor="w")
    head.pack(fill="x", padx=12, pady=(9, 0))
    big = tk.Label(root, text="—", bg="#0f172a", fg="#e2e8f0",
                   font=("Segoe UI", 20, "bold"), anchor="w")
    big.pack(fill="x", padx=12)
    sub = tk.Label(root, text="", bg="#0f172a", fg="#cbd5e1",
                   font=("Segoe UI", 10), anchor="w", justify="left")
    sub.pack(fill="x", padx=12)
    barbg = tk.Frame(root, bg="#1e293b", height=8)
    barbg.pack(fill="x", padx=12, pady=(7, 10))
    bar = tk.Frame(barbg, bg="#22c55e", height=8)
    bar.place(x=0, y=0, relwidth=0.0, height=8)

    # ลากย้ายตำแหน่งได้ เผื่อไปบังอะไรบนจอ
    drag = {"x": 0, "y": 0}
    for wdg in (root, head, big, sub):
        wdg.bind("<Button-1>", lambda e: drag.update(
            x=e.x_root - root.winfo_x(), y=e.y_root - root.winfo_y()))
        wdg.bind("<B1-Motion>", lambda e: root.geometry(
            f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}"))

    root.update_idletasks()
    _no_focus(root)
    idle_since = [None]

    def tick():
        try:
            with urllib.request.urlopen(status_url, timeout=3) as r:
                st = json.loads(r.read().decode("utf-8"))
        except Exception:
            st = {}

        running = bool(st.get("running"))
        if running:
            idle_since[0] = None
        else:
            if idle_since[0] is None:
                idle_since[0] = time.time()
            elif time.time() - idle_since[0] > IDLE_EXIT_SECONDS:
                root.quit()      # งานจบแล้วและเงียบไปพักหนึ่ง — ปิดตัวเอง
                return

        total = int(st.get("total") or 0)
        done = int(st.get("done") or 0)
        ok, fail, skip = (int(st.get(k) or 0) for k in ("ok", "fail", "skip"))
        cur = st.get("current") or {}
        dry = st.get("dry_run")

        if not running:
            head.configure(text="IPDaddChart — จบงานแล้ว", fg="#94a3b8")
        else:
            head.configure(
                text="IPDaddChart — โหมดทดสอบ (ไม่บันทึกจริง)" if dry else "IPDaddChart — กำลังคีย์ยาจริง",
                fg="#fcd34d" if dry else "#93c5fd")
        big.configure(text=f"{done}/{total}    ✅ {ok}   ⚠ {fail}   ⏭ {skip}")
        name = str(cur.get("ptname") or "").strip()
        if running and name:
            ward = str(cur.get("ward") or "").strip() or "-"
            bed = str(cur.get("bedno") or "").strip()
            sub.configure(text=f"ตึก {ward}" + (f" · เตียง {bed}" if bed else "") + f"\nกำลังคีย์: {name}")
        else:
            sub.configure(text=(st.get("phase") or "") + "\n")
        bar.place_configure(relwidth=(done / total) if total else 0.0)
        bar.configure(bg="#ef4444" if (fail and not ok) else "#22c55e")

        root.attributes("-topmost", True)   # HosXP ชอบแย่งขึ้นหน้าสุด ต้องย้ำทุกรอบ
        root.after(700, tick)

    tick()
    try:
        root.mainloop()
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8770/api/status"
    try:
        sys.exit(main(url))
    except Exception:
        sys.exit(1)
