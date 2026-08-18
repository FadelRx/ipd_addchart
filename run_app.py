"""จุดเริ่มโปรแกรมสำหรับไฟล์ .exe ไฟล์เดียว

โหมดการทำงาน:
    IPDaddChart.exe                 -> เปิดเซิร์ฟเวอร์ + เปิดเบราว์เซอร์ให้
    IPDaddChart.exe --overlay <url> -> ทำตัวเป็นแถบความคืบหน้าลอยเหนือ HosXP
                                       (ตัวโปรแกรมเรียกตัวเองด้วยโหมดนี้ ไม่ต้องมี python.exe แยก)

หมายเหตุ: ไฟล์นี้ถูกแพ็กแบบ --noconsole จึงไม่มีหน้าต่างดำให้เห็น error
ทุกความผิดพลาดตอนเริ่มโปรแกรมจึงต้องเด้งเป็นกล่องข้อความ + เขียนลงไฟล์ ไม่งั้นผู้ใช้จะงงว่าทำไมไม่ขึ้นอะไรเลย
"""
import sys
import threading
import time
import traceback


def _message_box(title: str, text: str) -> None:
    try:
        import ctypes

        ctypes.WinDLL("user32", use_last_error=True).MessageBoxW(None, text, title, 0x10)
    except Exception:
        pass


def _write_startup_log(text: str) -> str:
    try:
        from app.config import DATA_DIR

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        p = DATA_DIR / "startup_error.txt"
        p.write_text(text, encoding="utf-8")
        return str(p)
    except Exception:
        return "(เขียนไฟล์ log ไม่ได้)"


def _open_browser_later(url: str, delay: float = 2.5) -> None:
    def go():
        time.sleep(delay)
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=go, daemon=True).start()


def main() -> int:
    argv = sys.argv[1:]

    # --- โหมดแถบความคืบหน้า (โปรแกรมเรียกตัวเอง) ---
    if argv and argv[0] == "--overlay":
        from app.overlay import main as overlay_main

        return overlay_main(argv[1] if len(argv) > 1 else "http://127.0.0.1:8770/api/status")

    # --- โหมดปกติ: เปิดเซิร์ฟเวอร์ ---
    from app.config import BASE_DIR, CONFIG_DIR, DATA_DIR, load_settings

    # สร้างโฟลเดอร์ที่ต้องเขียนไว้ข้าง .exe ตั้งแต่รอบแรก
    for d in (DATA_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    settings = load_settings()
    port = int(settings.get("server", {}).get("port", 8770))
    url = f"http://127.0.0.1:{port}"

    return _serve(url, port)


def _serve(url: str, port: int) -> int:
    from app.winutil import ELEVATION_HELP, is_elevated

    if not is_elevated():
        # เตือนแบบ "ไม่บล็อก" เท่านั้น — กล่องข้อความของ Windows เป็น modal
        # ถ้าเรียกตรง ๆ ตรงนี้ โปรแกรมจะค้างรอคนกด OK ก่อนเปิดเซิร์ฟเวอร์
        # และเพราะแพ็กแบบไม่มีหน้าต่างคอนโซล ผู้ใช้จะเห็นแค่ "เปิดแล้วไม่ขึ้นอะไรเลย"
        # (เจอจริงตอนทดสอบไฟล์ exe ครั้งแรก) หน้าเว็บมีแถบแดงเตือนอยู่แล้วด้วย
        threading.Thread(
            target=_message_box, args=("IPDaddChart — สิทธิ์ไม่พอ", ELEVATION_HELP), daemon=True
        ).start()

    import uvicorn

    from app.main import app

    _open_browser_later(url)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        detail = traceback.format_exc()
        path = _write_startup_log(detail)
        _message_box(
            "IPDaddChart เปิดไม่สำเร็จ",
            f"{detail.strip().splitlines()[-1]}\n\nรายละเอียดทั้งหมดอยู่ที่\n{path}",
        )
        sys.exit(1)
