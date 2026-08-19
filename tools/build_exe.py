r"""สร้าง IPDaddChart.exe ไฟล์เดียว

วิธีใช้:  ดับเบิลคลิก build.bat   (หรือ  venv\Scripts\python.exe tools\build_exe.py)

ผลลัพธ์: dist\IPDaddChart.exe  — ก๊อปไฟล์เดียวไปเครื่องอื่นแล้วดับเบิลคลิกได้เลย
         ไม่ต้องลง Python ไม่ต้องลงไลบรารี ไม่ต้อง setup.bat

สิ่งที่ฝังเข้าไปในตัว exe:
    - โค้ดทั้งหมด + ไลบรารีที่ใช้ (fastapi, uvicorn, pywinauto, comtypes, pymysql, openpyxl)
    - หน้าเว็บใน app\static\
    - config\default_settings.json (ค่าตั้งต้น ไม่มีรหัสผ่าน)

สิ่งที่ "ไม่" ฝัง และจะไปอยู่ข้าง ๆ ไฟล์ .exe แทน (เพื่อให้ข้อมูลไม่หายเวลาอัปเดตโปรแกรม):
    - data\           ฐานข้อมูล log ประวัติการรัน ภาพหลักฐาน
    - config\settings.json   ค่าที่ตั้งเองต่อเครื่อง (ที่อยู่ฐานข้อมูล บัญชี รหัสผ่าน)

ตัวเลือก --with-config : ฝังไฟล์ settings.json ของเครื่องนี้เข้าไปด้วย
    ใช้เมื่อต้องการให้ผู้รับ "ดับเบิลคลิกแล้วใช้ได้เลย ไม่ต้องกรอกอะไร"
    แต่ต้องรู้ตัวว่า **รหัสผ่านฐานข้อมูลจะติดไปในไฟล์ exe ด้วย** ใครได้ไฟล์ไปก็ได้รหัสไปด้วย

ตัวเลือก --outroot <โฟลเดอร์> : ย้ายที่พักไฟล์ระหว่างสร้างไปไดรฟ์อื่น
    ตอนสร้างต้องใช้เนื้อที่ชั่วคราวหลาย GB ถ้าไดรฟ์ C: เต็มจะสร้างไม่ผ่าน
    เช่น  --outroot E:\build   จะได้ผลลัพธ์ที่ E:\build\dist\IPDaddChart.exe
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "IPDaddChart"


def main() -> int:
    with_config = "--with-config" in sys.argv
    # --no-uac ใช้เฉพาะตอนทดสอบเส้นทางโค้ดของไฟล์ที่แพ็กแล้ว (ไฟล์จริงต้องขอสิทธิ์แอดมินเสมอ)
    no_uac = "--no-uac" in sys.argv
    name = NAME + ("_test" if no_uac else "")
    py = sys.executable
    sep = ";"  # ตัวคั่นของ --add-data บน Windows

    if not (ROOT / "config" / "default_settings.json").exists():
        print("[ผิดพลาด] ไม่พบ config/default_settings.json")
        return 1

    # ที่พักไฟล์ระหว่างสร้าง — ย้ายไปไดรฟ์อื่นได้เมื่อไดรฟ์โปรแกรมเต็ม
    out_root = ROOT
    if "--outroot" in sys.argv:
        i = sys.argv.index("--outroot")
        if i + 1 >= len(sys.argv):
            print("[ผิดพลาด] --outroot ต้องตามด้วยที่อยู่โฟลเดอร์")
            return 1
        out_root = Path(sys.argv[i + 1]).resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        # PyInstaller ยังแตกไฟล์ชั่วคราวลง TEMP ของระบบอยู่ดี ต้องย้ายตามไปด้วย
        # ไม่งั้นย้าย workpath ไปเปล่า ๆ แล้วยังเต็มที่เดิม
        tmp = out_root / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = os.environ["TMP"] = str(tmp)
        print(f"ที่พักไฟล์ระหว่างสร้าง: {out_root}")

    args = [
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--noconsole",              # ไม่มีหน้าต่างดำ — error ตอนเริ่มจะเด้งเป็นกล่องข้อความแทน
        "--name", name,
        "--distpath", str(out_root / "dist"),
        "--workpath", str(out_root / "build"),
        "--specpath", str(out_root / "build"),
        "--add-data", f"{ROOT / 'app' / 'static'}{sep}app/static",
        "--add-data", f"{ROOT / 'config' / 'default_settings.json'}{sep}config",
        # โมดูลที่ถูกเรียกแบบไดนามิก ตัวแพ็กมองไม่เห็นเอง
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "pymysql",
        "--hidden-import", "openpyxl",
        "--hidden-import", "win32clipboard",
        "--hidden-import", "win32timezone",
        "--collect-submodules", "comtypes",
        "--collect-submodules", "pywinauto",
    ]
    if not no_uac:
        args.insert(6, "--uac-admin")   # ขอสิทธิ์แอดมินเอง ไม่ต้องมี run.bat คอยยกสิทธิ์

    if with_config:
        cfg = ROOT / "config" / "settings.json"
        if not cfg.exists():
            print("[ผิดพลาด] --with-config แต่ไม่พบ config/settings.json ในเครื่องนี้")
            return 1
        print("!! กำลังฝัง settings.json (มีรหัสผ่านฐานข้อมูล) เข้าไปในไฟล์ exe")
        print("!! ใครได้ไฟล์ exe นี้ไป จะได้รหัสผ่านไปด้วย — แจกเฉพาะคนที่ควรได้เท่านั้น")
        args += ["--add-data", f"{cfg}{sep}config"]

    args.append(str(ROOT / "run_app.py"))

    print("กำลังสร้าง .exe — ใช้เวลาสักครู่...\n")
    r = subprocess.run(args, cwd=str(ROOT))
    if r.returncode != 0:
        print("\n[ไม่สำเร็จ] สร้างไม่ผ่าน ดูข้อความด้านบน")
        return r.returncode

    exe = out_root / "dist" / f"{name}.exe"
    if not exe.exists():
        print("\n[ไม่สำเร็จ] ไม่พบไฟล์ผลลัพธ์")
        return 1
    size = exe.stat().st_size / (1024 * 1024)
    print(f"\nสำเร็จ: {exe}  ({size:.1f} MB)")
    print("\nวิธีเอาไปใช้เครื่องอื่น:")
    print("  1. ก๊อป IPDaddChart.exe ไปวางในโฟลเดอร์ว่าง ๆ ของเครื่องนั้น")
    print("  2. ดับเบิลคลิก -> กด Yes ที่ UAC -> เบราว์เซอร์เปิดให้เอง")
    if not with_config:
        print("  3. ครั้งแรกให้เข้าหน้าตั้งค่า ใส่ที่อยู่/บัญชีฐานข้อมูล HOSxP")
    print("\nโปรแกรมจะสร้างโฟลเดอร์ data\\ และ config\\ ข้าง ๆ ไฟล์ exe เอง")
    return 0


if __name__ == "__main__":
    sys.exit(main())
