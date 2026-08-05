"""ตัวช่วยเกี่ยวกับรายชื่อผู้ป่วย — การระบุตัวตนและคีย์กันคีย์ซ้ำ

กันคีย์ซ้ำต้องทนต่อความจริงข้อนี้: คนไข้คนเดียวกันอาจมาจากคนละแหล่งในแต่ละรอบ
(รอบเช้าดึงจาก DB ได้ทั้ง AN+HN, รอบบ่ายอัพ Excel มีแต่ HN) ถ้าใช้คีย์เดียวจะกันไม่ติด
จึงเก็บและเทียบ "ทั้ง AN และ HN" แบบตัดศูนย์นำหน้าออกเสมอ
"""
import re


def norm_id(value) -> str:
    """ตัดทุกอย่างที่ไม่ใช่ตัวเลขและศูนย์นำหน้าออก — 0931981, 931981, ' 931981 ' ถือเป็นค่าเดียวกัน"""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.lstrip("0") or ("0" if digits else "")


def dedup_key(p: dict) -> str:
    """คีย์หลักสำหรับเก็บลง completions — ใช้ AN ก่อนเพราะระบุการ admit ครั้งนั้นชัดเจน
    (การ "เคยทำแล้วหรือยัง" ไม่ได้ดูจากคีย์นี้อย่างเดียว ดู is_done ประกอบ)"""
    an = norm_id(p.get("an"))
    if an:
        return f"AN:{an}"
    hn = norm_id(p.get("hn"))
    return f"HN:{hn}" if hn else ""


def is_done(p: dict, index: dict) -> bool:
    """เคยคีย์สำเร็จแล้วหรือยัง — ตรงกับ AN หรือ HN อันใดอันหนึ่งก็ถือว่าเคยแล้ว"""
    an, hn = norm_id(p.get("an")), norm_id(p.get("hn"))
    return bool((an and an in index.get("an", ())) or (hn and hn in index.get("hn", ())))


def needs_review(p: dict, index: dict) -> bool:
    """เคยจบแบบ 'ไม่รู้ว่าบันทึกสำเร็จหรือไม่' — ต้องให้คนไปตรวจก่อน ห้ามคีย์ซ้ำอัตโนมัติ"""
    an, hn = norm_id(p.get("an")), norm_id(p.get("hn"))
    return bool(
        (an and an in index.get("unknown_an", ())) or (hn and hn in index.get("unknown_hn", ()))
    )


def type_value(p: dict, patient_key: str) -> str:
    """ค่าที่จะพิมพ์ลงช่องเลือกคนไข้ของ HosXP (ตั้งได้ว่าใช้ hn หรือ an)"""
    return str(p.get(patient_key) or "").strip()


def label(p: dict) -> str:
    parts = []
    if p.get("hn"):
        parts.append(f"HN {p['hn']}")
    if p.get("an"):
        parts.append(f"AN {p['an']}")
    if p.get("ptname"):
        parts.append(str(p["ptname"]))
    return " ".join(parts) or "(ไม่มีข้อมูล)"
