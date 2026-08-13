"""ขั้นตอนคีย์ต่อคนไข้ 1 คนในหน้า IPD Medication Profile ของ HosXP

ลำดับตามที่ทำด้วยมือ:
    คีย์ HN ในกรอบ "เลือกคนไข้" → Enter → คนไข้ขึ้นในกรอบ "ข้อมูลผู้ป่วย"
    → กดปุ่ม New → กดปุ่ม Add Chart F5 → กดบันทึก → popup ยืนยัน → วนคนถัดไป

หลักความปลอดภัยที่ยึดไว้ทั้งไฟล์ — "ยิ่งไปไกล ยิ่งห้ามเดา":
    ก่อนกด New       พลาดได้ = ข้ามคนนี้ไปคนถัดไปอย่างปลอดภัย (StepFailed)
    หลังกด New       พลาด = มีชาร์ตค้างเปิดอยู่ ห้ามไปคนถัดไป ต้องหยุดทั้งงาน (ChartDirtyError)
    หลังกดบันทึก     พลาด = ไม่รู้ว่ายาออกไปหรือยัง ต้องหยุดและให้คนตรวจ (AmbiguousSaveError)
"""
import re
import time

from ..patients import label as patient_label
from ..patients import type_value
from .session import (
    ConfigError,
    HosxpSession,
    PopupNeedsHuman,
    PopupReviewPatient,
    PopupSkipPatient,
    RobotError,
    read_dialog,
    safe_text,
)

STEP_TIMEOUT = 25.0


class StepFailed(RobotError):
    """พลาดก่อนแตะชาร์ต — ข้ามคนนี้ไปคนถัดไปได้อย่างปลอดภัย"""


class ChartDirtyError(RobotError):
    """พลาดหลังเริ่มแก้ชาร์ตแล้วแต่ยังไม่บันทึก — มีชาร์ตค้างเปิดอยู่ ต้องหยุดทั้งงาน"""


class AmbiguousSaveError(RobotError):
    """กดบันทึกไปแล้วแต่ไม่รู้ว่าสำเร็จหรือไม่ — ต้องหยุดทั้งงานให้คนตรวจ ห้ามเดา"""


class CancelledMidPatient(Exception):
    pass


def _contains_number(text: str, value: str) -> bool:
    """หาเลขในข้อความแบบไม่ให้ชนกับเลขอื่น (0043462 กับ 43462 ถือว่าตรงกัน แต่ 434621 ไม่ตรง)"""
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return False
    core = digits.lstrip("0") or "0"
    return re.search(r"(?<!\d)0*" + re.escape(core) + r"(?!\d)", text) is not None


def _texts_of(session: HosxpSession, container, limit: int = 200) -> str:
    def work():
        parts = []
        try:
            items = container.descendants()
        except Exception:
            items = []
        for c in items[:limit]:
            try:
                txt = (safe_text(c.handle) or "").strip()
            except Exception:
                continue
            if txt:
                parts.append(txt)
        return "\n".join(parts)

    return session.timed(work, STEP_TIMEOUT, "อ่านข้อความบนหน้าจอ") or ""


def run_patient(session: HosxpSession, patient: dict, dry_run: bool, cancel_check) -> str:
    cfg = session.robot_cfg
    steps = cfg.get("steps", {})
    ipd_spec = cfg.get("ipd_form", {})
    t = session.t
    log = session.log

    patient_key = ipd_spec.get("patient_key", "hn")
    value = type_value(patient, patient_key)
    if not value:
        raise StepFailed(f"ไม่มีค่า {patient_key.upper()} ของคนไข้รายนี้ — ข้าม")

    log("step", "info", "หาหน้าต่าง IPD Medication Profile")
    ipd = session.ensure_ipd_form()
    session.timed(session.main.set_focus, 10, "ดึงโฟกัสมาที่ HosXP")

    # เคลียร์ dialog ที่อาจค้างจากคนก่อนหน้าก่อนเริ่ม (เช่น "Select new patient ?")
    # ถ้าไม่เคลียร์ หน้าจอจะยังไม่กลับมาที่กรอบเลือกคนไข้ แล้วคนนี้จะคีย์ไม่ได้
    session.handle_popups("เตรียมหน้าจอ", 2, t("between_enters", 0.5))

    # --- 1. คีย์ HN ในกรอบเลือกคนไข้ ---
    cancel_check()
    select_spec = ipd_spec.get("select_group", {})
    grp = session.find_one(ipd, select_spec, "กรอบเลือกคนไข้", STEP_TIMEOUT)
    if grp is None:
        # HosXP ซ่อนกรอบ 'เลือกคนไข้' ทิ้งไปเมื่อมีคนไข้เปิดอยู่ ต้องกดปุ่ม 'เลือกใหม่' กลับมาก่อน
        # (โหมดทดสอบไม่ได้บันทึก จึงไม่มี dialog 'Select new patient ?' มาพากลับให้เอง)
        back_spec = ipd_spec.get("select_new_button") or {}
        back = None
        if back_spec.get("class_name") or back_spec.get("title_re"):
            back = session.find_one(ipd, back_spec, "ปุ่มเลือกใหม่", STEP_TIMEOUT)
        if back is not None:
            log("step", "info", "หน้าจอยังอยู่ที่คนก่อนหน้า — กดปุ่ม 'เลือกใหม่' เพื่อกลับไปหน้าเลือกคนไข้")
            session.timed(back.click_input, 15, "กดปุ่มเลือกใหม่")
            time.sleep(t("after_select_new", 1.5))
            session.handle_popups("หลังกดเลือกใหม่", 2, t("between_enters", 0.5))
            grp = session.find_one(ipd, select_spec, "กรอบเลือกคนไข้", STEP_TIMEOUT)
        if grp is None:
            raise StepFailed(
                "ไม่พบกรอบ 'เลือกคนไข้' ในหน้า IPD Medication Profile "
                "— หน้าจอยังค้างอยู่ที่รายการยาของคนก่อนหน้า และกดปุ่ม 'เลือกใหม่' กลับมาไม่ได้ "
                "(ตรวจ selector ที่ ipd_form.select_new_button)" + session.describe_blocking()
            )

    edit_class = ipd_spec.get("patient_edit_class", "TdxEdit")
    idx = int(ipd_spec.get("patient_edit_index", 0))
    edits = session.timed(
        lambda: grp.descendants(class_name=edit_class), STEP_TIMEOUT, "หาช่องคีย์คนไข้"
    ) or []
    if len(edits) <= idx:
        raise StepFailed(
            f"ไม่พบช่องคีย์คนไข้ (พบ {len(edits)} ช่อง class {edit_class}) — ปรับ ipd_form.patient_edit_index"
        )
    edit = edits[idx]
    log("step", "info", f"พบช่องคีย์ {len(edits)} ช่อง — ใช้ช่องที่ {idx} พิมพ์ {patient_key.upper()} {value}")
    session.timed(lambda: session.type_into(edit, value), STEP_TIMEOUT, f"พิมพ์ {patient_key.upper()}")

    from pywinauto import keyboard  # นำเข้าตรงนี้เพื่อให้ชัดว่าใช้เฉพาะการกดปุ่มบนคีย์บอร์ด

    # HosXP ต้องกด Enter สองจังหวะ: ครั้งแรกยืนยัน HN แล้วรายการ visit จะขึ้นในตาราง
    # ครั้งที่สองเลือก visit ที่ไฮไลต์อยู่ (ล่าสุด) — ตั้งจำนวน/ปุ่มได้ที่ ipd_form.after_type_keys
    # หยุดกดทันทีที่คนไข้ขึ้นแล้ว เพื่อไม่ให้ Enter ส่วนเกินตกไปโดนอย่างอื่นในชาร์ตที่เพิ่งเปิด
    info_spec = ipd_spec.get("info_group", {})
    primary = patient.get("hn") if patient_key == "hn" else patient.get("an")

    def loaded_now():
        """คนไข้ 'คนที่เราต้องการ' ขึ้นแล้วหรือยัง

        ต้องเช็คว่าเลขตรงด้วย ไม่ใช่แค่ 'มีกรอบข้อมูลผู้ป่วย' เฉย ๆ
        เพราะกรอบอาจค้างข้อมูลคนก่อนหน้าอยู่ ถ้าเช็คแค่ว่ามีกรอบ
        ลูปจะเลิกกดปุ่มทั้งที่ยังไม่ได้โหลดคนใหม่ แล้วทุกคนตั้งแต่คนที่สองจะพลาดหมด
        """
        try:
            found = session.find_one(ipd, info_spec, "กรอบข้อมูลผู้ป่วย", timeout=8)
        except Exception:
            return None
        if found is None:
            return None
        if not primary:
            return found
        try:
            return found if _contains_number(_texts_of(session, found), primary) else None
        except Exception:
            return None

    key_steps = ipd_spec.get("after_type_keys") or [{"label": "ยืนยัน", "keys": "{ENTER}", "wait": 1.2}]
    for ks in key_steps:
        cancel_check()
        if loaded_now() is not None:
            log("step", "info", "คนไข้ที่ต้องการขึ้นแล้ว — ไม่กดปุ่มเพิ่ม")
            break
        keys = ks.get("keys", "{ENTER}")
        keyboard.send_keys(keys, pause=0.05)
        log("step", "info", f"กด {ks.get('label') or keys}")
        time.sleep(float(ks.get("wait", 1.2)))
        session.handle_popups("load_patient", int(t("load_popup_checks", 3)), t("between_enters", 0.5))

    # --- 2. รอจน "ข้อมูลผู้ป่วย" แสดงคนที่เราตั้งใจจริง ๆ ---
    # ไม่ใช่แค่รอให้กรอบโผล่ เพราะกรอบอาจค้างข้อมูลคนก่อนหน้าอยู่ (คีย์ไม่ติด = ยังเห็นคนเดิม)
    deadline = time.time() + t("patient_load_timeout", 20)
    info, texts = None, ""
    while True:
        cancel_check()
        info = session.find_one(ipd, info_spec, "กรอบข้อมูลผู้ป่วย", STEP_TIMEOUT)
        if info is not None:
            texts = _texts_of(session, info)
            if not primary or _contains_number(texts, primary):
                break
        if time.time() >= deadline:
            break
        time.sleep(0.5)

    if info is None:
        raise StepFailed(
            f"คีย์ {patient_key.upper()} {value} แล้วคนไข้ไม่ขึ้น (ไม่พบกรอบ 'ข้อมูลผู้ป่วย') "
            "— ตรวจว่าเลขถูกต้องและยัง admit อยู่จริง"
        )
    log("step", "info", "คนไข้ขึ้นแล้ว (พบกรอบข้อมูลผู้ป่วย)")

    # --- ตรวจตัวตนคนไข้ ---
    # โหมดคีย์จริงบังคับ strict เสมอ — ห้ามสั่งยาให้คนที่ยืนยันตัวตนไม่ได้
    verify = "strict" if not dry_run else cfg.get("verify_patient_loaded", "strict")
    if verify:
        checks = []
        if patient.get("hn"):
            checks.append(("HN", patient["hn"]))
        if patient.get("an"):
            checks.append(("AN", patient["an"]))
        if not checks:
            raise StepFailed("ไม่มีทั้ง HN และ AN ให้ยืนยันตัวตนคนไข้ — ไม่คีย์ให้เพื่อความปลอดภัย")
        missing = [f"{n} {v}" for n, v in checks if not _contains_number(texts, v)]
        if missing:
            msg = (
                f"ตรวจไม่พบ {', '.join(missing)} ในกรอบข้อมูลผู้ป่วยหลังโหลด "
                "— อาจโหลดไม่สำเร็จ หรือได้คนละคน/คนละ visit "
                "(ถ้าคนไข้มีหลาย admit การกด Enter จะเลือกแถวที่ไฮไลต์อยู่ ซึ่งอาจไม่ใช่ AN ที่ต้องการ) "
                "จึงไม่กดเพิ่มยาให้"
            )
            if str(verify) == "warn":
                log("verify", "retry", msg + " (โหมด warn: ทำต่อ)")
            else:
                raise StepFailed(msg)
        else:
            log("verify", "info", "ยืนยันจากกรอบข้อมูลผู้ป่วย: พบ " + ", ".join(f"{n} {v}" for n, v in checks))

    if dry_run:
        return f"โหมดทดสอบ: โหลดคนไข้ {patient_label(patient)} และตรวจหน้าจอสำเร็จ (ไม่ได้กดเพิ่มยา/บันทึก)"

    # --- 3. กดปุ่มตามลำดับ (New แล้ว Add Chart F5) ---
    scope = ipd
    tab_spec = ipd_spec.get("drug_tab", {})
    if tab_spec.get("class_name") or tab_spec.get("title_re"):
        tab = session.find_one(ipd, tab_spec, "แท็บสั่งยา", STEP_TIMEOUT)
        if tab is not None:
            scope = tab

    buttons = steps.get("action_buttons") or []
    if not buttons:
        raise ConfigError("ยังไม่ได้ตั้งค่าปุ่มที่ต้องกด (robot.steps.action_buttons) — ดู README")

    dirty = False  # True เมื่อเริ่มแก้ชาร์ตแล้ว = ห้ามข้ามไปคนถัดไปถ้าพลาด
    btn_wait = t("button_wait_timeout", 10)
    # เคลียร์หน้าต่างที่ลอยทับก่อนเริ่มคลิก (เช่นหน้าต่าง Note ของคนไข้)
    # การคลิกใช้พิกัดบนจอ ถ้ามีหน้าต่างอื่นทับอยู่ คลิกจะไปตกที่หน้าต่างนั้นแทนปุ่มที่ตั้งใจ
    session.handle_popups("ก่อนกดปุ่ม", 1, t("between_enters", 0.5))
    try:
        for spec in buttons:
            cancel_check()
            name = spec.get("label") or spec.get("title_re") or "(ไม่มีชื่อ)"
            btn = session.wait_for(scope, spec, f"ปุ่ม {name}", timeout=btn_wait)
            if btn is None and scope is not ipd:
                btn = session.wait_for(ipd, spec, f"ปุ่ม {name}", timeout=3)
            if btn is None:
                raise StepFailed(
                    f"ไม่พบปุ่ม '{name}' ที่มองเห็นได้ในหน้าจอหลังโหลดคนไข้ "
                    "— ตรวจว่าอยู่แท็บสั่งยาจริง หรือแก้ selector ที่ robot.steps.action_buttons"
                )
            session.timed(btn.click_input, 15, f"กดปุ่ม {name}")
            dirty = True
            log("action", "info", f"กดปุ่ม {name}")
            time.sleep(float(spec.get("wait_after", 1.0)))
            session.handle_popups(f"หลังกด {name}", 2, t("between_enters", 0.5))
    except CancelledMidPatient:
        if dirty:
            raise ChartDirtyError(
                f"ถูกสั่งหยุดหลังเริ่มแก้ชาร์ตของ {patient_label(patient)} แต่ยังไม่ได้บันทึก "
                "— ไปตรวจหน้าจอ HosXP ว่ามีรายการค้างที่ต้องลบหรือบันทึกเองหรือไม่"
            )
        raise
    except Exception as e:
        if dirty:
            raise ChartDirtyError(
                f"พลาดหลังเริ่มแก้ชาร์ตของ {patient_label(patient)} แต่ยังไม่ได้บันทึก: {e} "
                "— หยุดงานทั้งหมด ไปตรวจหน้าจอ HosXP ว่ามีรายการค้างหรือไม่ก่อนรันต่อ"
            )
        raise

    # --- 4. บันทึก — ตั้งแต่จุดนี้ทุกความผิดพลาดคือ "ไม่รู้ว่ายาออกหรือยัง" ---
    try:
        cancel_check()
        save_spec = steps.get("save_button") or {}
        save_btn = None
        if save_spec.get("class_name") or save_spec.get("title_re"):
            save_btn = session.wait_for(scope, save_spec, "ปุ่มบันทึก", timeout=btn_wait)
            if save_btn is None and scope is not ipd:
                save_btn = session.wait_for(ipd, save_spec, "ปุ่มบันทึก", timeout=3)
        if save_btn is None:
            raise RobotError(
                "ไม่พบปุ่มบันทึกที่มองเห็นได้ — ไม่ใช้ปุ่มลัด F9 แทน เพราะถ้าโฟกัสไม่ได้อยู่ที่ฟอร์ม "
                "ปุ่มลัดอาจไปตกที่หน้าต่างอื่น ตรวจ robot.steps.save_button"
            )
        session.timed(save_btn.click_input, 15, "กดปุ่มบันทึก")
        log("save", "info", f"กดปุ่ม {save_spec.get('label', 'บันทึก')} แล้ว รอ popup ยืนยัน")
        time.sleep(t("after_save_key", 1.0))
    except CancelledMidPatient:
        raise ChartDirtyError(
            f"ถูกสั่งหยุดตอนกำลังจะบันทึกของ {patient_label(patient)} — ไปตรวจหน้าจอ HosXP"
        )
    except Exception as e:
        raise AmbiguousSaveError(
            f"กดบันทึกไม่สำเร็จหลังแก้ชาร์ตของ {patient_label(patient)}: {e} "
            "— หยุดงานทั้งหมด ตรวจหน้าจอ HosXP ว่ารายการถูกบันทึกหรือค้างอยู่"
        )

    # --- 5. popup ยืนยัน: กดให้เฉพาะข้อความที่อนุญาตไว้ล่วงหน้าเท่านั้น ---
    # HosXP แทรกกล่องเตือนของตัวเองก่อนกล่องยืนยันได้ (เช่น "ตรวจพบรายการยาที่สั่ง
    # ผู้ป่วยได้รับแล้วจาก OPD Visit ก่อน Admit") — เดิมกล่องพวกนี้ทำให้หยุดทั้งงานเสมอ
    # เพราะจังหวะนี้ไม่ได้อ่าน robot.popups.rules เลย ตอนนี้อ่านแล้ว แต่คุมแคบกว่าที่อื่น:
    #   - รับเฉพาะกฎที่ action = click (ปิดแล้วไปต่อ) เท่านั้น
    #     กฎ halt/skip_patient/review_patient ยังหยุดทั้งงาน เพราะจังหวะนี้ยัง "ไม่รู้ว่ายาออกหรือยัง"
    #     การข้ามหรือติดธงแล้วไปคนถัดไปจะทิ้งชาร์ตที่ไม่รู้สถานะไว้ข้างหลัง
    #   - เก็บหลักฐานทุกครั้งที่กดปิด เพราะเป็นการกดในช่วงที่ยาอาจกำลังถูกบันทึก
    #   - กล่องที่ไม่ตรงกฎไหนเลย ยังหยุดทั้งงานเหมือนเดิมทุกประการ
    max_pre = int(steps.get("max_dialogs_before_confirm", 3))
    dismissed_before_confirm = 0
    popup = None
    while True:
        try:
            popup = session.wait_for_popup(timeout=float(steps.get("confirm_timeout", 12)))
        except Exception as e:
            raise AmbiguousSaveError(f"รอ popup ยืนยันไม่สำเร็จ: {e} — ตรวจหน้าจอ HosXP")
        if popup is None:
            break
        pre_h, pre_cls, pre_title, _pre_text = popup
        pre_dlg = read_dialog(pre_h)
        # จงใจไม่ส่ง dlg เข้าไป = ปิดการจับกฎ "จากรูปพรรณ" ในจังหวะนี้ ให้จับจากข้อความเท่านั้น
        # เพราะกล่องยืนยันการบันทึกเองก็มีรูปพรรณ #32770 + Confirm + [Yes/No] เหมือนกฎ
        # "Select new patient ?" เป๊ะ ๆ ถ้าเครื่องไหนอ่านข้อความในกล่องไม่ได้ กล่องยืนยันจะถูกจับผิดกฎทันที
        rule, matched_by = session.match_popup_rule(pre_dlg["all"] or pre_title, None, "save")
        if rule is None:
            break  # กล่องนี้ต้องเป็นกล่องยืนยัน — ไปตรวจตามกติกาเดิมข้างล่าง
        label = rule.get("label") or rule.get("match") or pre_cls
        if dismissed_before_confirm >= max_pre:
            raise AmbiguousSaveError(
                f"หลังกดบันทึกมีกล่องแทรกก่อนกล่องยืนยันเกิน {max_pre} กล่อง (กล่องล่าสุดคือ '{label}') "
                "— ผิดปกติจากที่เคยเจอ หยุดงานทั้งหมด ตรวจหน้าจอ HosXP ก่อนรันใหม่ "
                + session.keep_evidence(pre_h, f"กล่อง '{label}' ที่เกินเพดานก่อนกล่องยืนยัน")
            )
        action = rule.get("action", "halt")
        if action != "click":
            raise AmbiguousSaveError(
                f"หลังกดบันทึกขึ้นกล่อง '{label}' ที่ตั้ง action ไว้เป็น '{action}' "
                "— จังหวะหลังกดบันทึกยังไม่รู้ว่ายาออกหรือยัง จึงหยุดงานทั้งหมดให้เภสัชกรตรวจเอง "
                "(กล่องที่จะให้กดผ่านจังหวะนี้ได้ต้องตั้ง action เป็น click เท่านั้น) "
                + session.keep_evidence(pre_h, f"กล่อง '{label}' ก่อนกล่องยืนยัน (action {action})")
            )
        ok, why = session.check_level_rule(rule, pre_dlg["all"] or pre_title)
        if not ok:
            raise AmbiguousSaveError(
                f"หลังกดบันทึกขึ้นกล่อง '{label}' แต่ยังกดปิดให้ไม่ได้: {why} "
                "— หยุดงานทั้งหมด ปล่อยกล่องนี้ค้างไว้ให้เภสัชกรอ่านและตัดสินใจเอง "
                + session.keep_evidence(pre_h, f"กล่อง '{label}' ก่อนกล่องยืนยันที่กดปิดให้ไม่ได้")
            )
        log(
            "save",
            "info",
            f"หลังกดบันทึกขึ้นกล่องตามกฎ '{label}' ({matched_by}) ก่อนกล่องยืนยัน — ปิดตามกฎแล้วรอกล่องยืนยันต่อ "
            + session.keep_evidence(pre_h, f"กล่อง '{label}' ที่ปิดตามกฎก่อนกล่องยืนยัน", kind="struct"),
        )
        try:
            session.dismiss_dialog(pre_h, rule, pre_cls)
        except Exception as e:
            raise AmbiguousSaveError(
                f"ปิดกล่อง '{label}' ที่ขึ้นก่อนกล่องยืนยันไม่สำเร็จ: {e} — ตรวจหน้าจอ HosXP"
            )
        dismissed_before_confirm += 1
        popup = None

    if popup is None:
        if steps.get("require_confirm", True):
            raise AmbiguousSaveError(
                f"กดบันทึกแล้วไม่พบ popup ยืนยันภายในเวลาที่กำหนด — ไม่แน่ใจว่า {patient_label(patient)} "
                "บันทึกสำเร็จหรือไม่ จึงหยุดงานทั้งหมดเพื่อให้ตรวจหน้าจอ HosXP ก่อน"
            )
        log("save", "retry", "ไม่พบ popup ยืนยัน — ข้ามตามการตั้งค่า require_confirm=false")
    else:
        h, cls, _title, _text = popup
        dlg = read_dialog(h)
        conf = steps.get("confirm_dialog") or {}
        # บันทึกขนาดกล่องกับจำนวน control ไว้ด้วย เพราะ HosXP ไม่ยอมให้อ่านข้อความข้างใน
        # ขนาดกล่องจึงเป็นเบาะแสเดียวที่บอกได้ว่า "กล่องเดิม" หรือ "กล่องใหม่ที่ยาวกว่า"
        detail = (
            f"[{cls}] หัวเรื่อง {dlg['title']!r} ปุ่ม {dlg['buttons']} "
            f"ขนาด {dlg['size'][0]}x{dlg['size'][1]} control {dlg['children']} ตัว "
            f"ข้อความ {dlg['message'][:200]!r}"
        )

        # คำเตือนทางคลินิก (แพ้ยา/ยาตีกัน/ยาซ้ำ) ต้องให้คนอ่านและตัดสินใจเองเสมอ
        if session.looks_like_error(dlg["all"]):
            raise AmbiguousSaveError(
                f"หลังกดบันทึกขึ้นข้อความที่ต้องให้คนอ่านและตัดสินใจเอง: {detail} "
                "— หยุดงานทั้งหมด ปล่อยหน้าต่างนี้ค้างไว้ให้เภสัชกรจัดการ "
                + session.keep_evidence(h, "dialog หลังกดบันทึกที่เข้าข่ายคำเตือน")
            )

        title_re = conf.get("title_re") or ""
        if not title_re or not re.search(title_re, dlg["title"] or ""):
            raise AmbiguousSaveError(
                f"หลังกดบันทึกขึ้น dialog ที่หัวเรื่องไม่ตรงกับที่อนุญาตไว้: {detail} "
                "— หยุดงานทั้งหมด ให้เภสัชกรอ่านและกดเอง (ตั้งค่าที่ robot.steps.confirm_dialog.title_re) "
                + session.keep_evidence(h, "dialog หลังกดบันทึกที่หัวเรื่องไม่ตรง")
            )

        # กติกาสำคัญ: ข้อความในกล่องต้อง "ตรงตามที่คาดไว้" เสมอ
        # ถ้าตั้ง message_re ว่าง แปลว่าคาดว่าเป็นกล่องยืนยันเปล่า ๆ ที่ไม่มีข้อความ
        # ดังนั้นถ้าจู่ ๆ มีข้อความโผล่มา = ไม่ใช่กล่องเดิม = ต้องหยุดให้คนอ่าน
        message_re = conf.get("message_re") or ""
        if message_re:
            if not re.search(message_re, dlg["message"] or ""):
                if dlg["message"]:
                    raise AmbiguousSaveError(
                        f"หลังกดบันทึกขึ้น dialog ที่ข้อความไม่ตรงกับที่อนุญาตไว้: {detail} "
                        "— หยุดงานทั้งหมด ให้เภสัชกรอ่านและกดเอง "
                        + session.keep_evidence(h, "dialog หลังกดบันทึกที่ข้อความไม่ตรง")
                    )
                # อ่านข้อความในกล่องไม่ได้เลย (ทั้ง WM_GETTEXT และ UI Automation)
                # ยังกดต่อได้เพราะเงื่อนไขอื่นครบ (หัวเรื่อง Confirm + ปุ่ม Yes/No) ซึ่งเท่ากับ
                # ระดับความปลอดภัยเดิมก่อนมี UI Automation — แต่ต้องบันทึกไว้ว่าตรวจข้อความไม่ได้
                log(
                    "save",
                    "retry",
                    "อ่านข้อความในกล่องยืนยันไม่ได้เลย จึงตรวจไม่ได้ว่าตรงกับ "
                    f"'{message_re}' หรือไม่ — กดต่อตามเงื่อนไขอื่น (หัวเรื่อง/ปุ่ม) "
                    "ถ้าเจอบ่อยควรตรวจว่าเครื่องนี้ใช้ UI Automation ได้หรือไม่",
                )
        elif dlg["message"]:
            raise AmbiguousSaveError(
                f"หลังกดบันทึกขึ้น dialog ที่มีข้อความซึ่งยังไม่ได้อนุญาต: {detail} "
                "— หยุดงานทั้งหมด ให้เภสัชกรอ่านเอง ถ้าเป็นข้อความปกติให้ใส่ที่ "
                "robot.steps.confirm_dialog.message_re "
                + session.keep_evidence(h, "dialog หลังกดบันทึกที่มีข้อความซึ่งยังไม่ได้อนุญาต")
            )

        # เก็บภาพกล่องยืนยัน 2 ใบแรกของแต่ละรอบไว้เป็นหลักฐานว่าโรบอทกด Yes ใส่กล่องหน้าตาแบบไหน
        # (จำเป็นเพราะอ่านข้อความในกล่องไม่ได้ — ถ้าไม่เก็บภาพก็ตรวจย้อนหลังไม่ได้เลย)
        proof = session.keep_evidence(h, "กล่องยืนยันการบันทึกที่โรบอทกด Yes", kind="confirm")
        log("save", "info", f"ยืนยันการบันทึก: {detail}{(' ' + proof) if proof else ''}")
        try:
            yes_spec = conf.get("yes_button") or {}
            if yes_spec.get("title_re"):
                closed = session.click_dialog_button(h, yes_spec, "ยืนยัน (Yes)")
            else:
                closed = session.press_on_popup(h, steps.get("confirm_key", "{ENTER}"))
        except Exception as e:
            raise AmbiguousSaveError(f"กดยืนยันไม่สำเร็จ: {e} — ตรวจหน้าจอ HosXP")
        if not closed:
            raise AmbiguousSaveError(
                "กดยืนยันแล้วแต่ popup ยังค้างอยู่ — หยุดงานไว้ก่อน ไปดูหน้าจอ HosXP"
            )
        # ตั้งแต่นี้ไป dialog ที่ค้างอยู่ถือว่าเป็นผลจากการบันทึกของโรบอทเอง
        # (ใช้เป็นเงื่อนไขให้กฎที่จับกล่องจากรูปพรรณทำงานได้ ดู session.match_popup_rule)
        session.saved_once = True

    # --- 6. เก็บกวาด dialog ที่ตามมาหลังบันทึก ---
    # HosXP จะถาม "Rx-Queue : n / Select new patient ?" ต่อ ต้องกด Yes เพื่อกลับไปหน้าเลือกคนไข้
    # ไม่งั้นคนถัดไปจะคีย์ไม่ได้ (กฎอยู่ที่ robot.popups.rules)
    # ถึงตรงนี้ยาถูกบันทึกแล้ว ถ้ามี dialog แปลก ๆ ตามมาต้องถือเป็น "ไม่รู้ผล" ไม่ใช่ fail ธรรมดา
    # ไม่งั้นคนนี้จะไม่ถูกบันทึกว่าทำแล้ว แล้วโดนคีย์ซ้ำในรอบถัดไป
    # HosXP ใช้เวลาบันทึกจริงหลายวินาที กล่อง "Select new patient ?" จึงโผล่ช้ากว่าที่คิดมาก
    # (จาก log จริง: กด Yes 10:54:09 กล่องโผล่หลัง 10:54:16) ถ้าเลิกรอเร็วเกินไป
    # กล่องจะไปโผล่ตอนกำลังเตรียมคนถัดไปแล้วทำให้งานสะดุด — จึงรอจนกลับมาหน้าเลือกคนไข้จริง ๆ
    # สำคัญ: ห้ามใช้ find_one() ในลูปนี้ (บทเรียนจริง run 292) — จังหวะหลังกด Yes
    # คือช่วงที่ HosXP ยุ่งที่สุด การค้นหาที่ปกติเสร็จใน 1 วิ อาจใช้ 5-10 วิ
    # พอเลยเพดาน ระบบจะตีตราว่าเซสชันพังแล้วหยุดงานทั้งหมด ทั้งที่แค่ต้องรออีกนิด
    # quick_has_control() อ่านด้วย Windows API ที่มีเพดานเวลาในตัว จึงค้างไม่ได้เลย
    try:
        ipd_handle = ipd.handle
        deadline = time.time() + t("post_save_wait", 25)
        back = False
        while True:
            session.handle_popups("save", int(t("post_save_checks", 2)), t("between_enters", 0.5))
            if session.quick_has_control(ipd_handle, select_spec):
                back = True
                break
            if time.time() >= deadline:
                break
            time.sleep(0.5)
        if back:
            log("save", "info", "กลับมาที่หน้าเลือกคนไข้แล้ว พร้อมทำคนถัดไป")
        else:
            log(
                "save",
                "retry",
                "บันทึกเรียบร้อยแล้วแต่ยังไม่กลับมาหน้าเลือกคนไข้ "
                "— จะกดปุ่ม 'เลือกใหม่' ให้เองตอนเริ่มคนถัดไป",
            )
    except PopupReviewPatient:
        # คำเตือนที่ต้องให้เภสัชกรอ่านเอง (เช่น Drug Interaction) — ปิดหน้าต่างไปแล้ว
        # หน้าจอกลับมาปกติ จึงไม่ต้องหยุดทั้งงาน แค่ติดธงคนนี้ไว้แล้วไปคนถัดไป
        raise
    except PopupSkipPatient as e:
        # กฎที่ประกาศไว้ว่า dialog นี้แปลว่า "ไม่มีอะไรถูกบันทึก" (เช่น No Item)
        # จึงมั่นใจได้ว่ายายังไม่ออก — นับเป็นข้ามคนนี้ ไม่ใช่สถานะไม่รู้ผล
        if getattr(e, "means_not_saved", False):
            raise
        raise AmbiguousSaveError(
            f"บันทึกยาไปแล้วแต่มี dialog ที่สั่งให้ข้ามคนนี้ตามมา: {e} "
            f"— หยุดงานทั้งหมด ตรวจว่า {patient_label(patient)} บันทึกครบหรือไม่ก่อนรันใหม่"
        )
    except PopupNeedsHuman as e:
        raise AmbiguousSaveError(
            f"บันทึกยาไปแล้วแต่มี dialog ตามมาที่ต้องให้คนตรวจ: {e} "
            f"— หยุดงานทั้งหมด ตรวจว่า {patient_label(patient)} บันทึกครบหรือไม่ก่อนรันใหม่"
        )
    except Exception as e:
        raise AmbiguousSaveError(
            f"บันทึกยาไปแล้วแต่เก็บกวาดหน้าจอต่อไม่ได้: {e} — ตรวจหน้าจอ HosXP ก่อนรันใหม่"
        )

    return f"เพิ่ม chart ยาและบันทึกสำเร็จ — {patient_label(patient)}"
