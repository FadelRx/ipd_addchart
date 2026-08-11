"""JobManager — รันงานคีย์ยาใน background thread เดียว พร้อม cancel / circuit breaker / กันคีย์ซ้ำ"""
import threading
import traceback

from . import db
from .config import load_settings
from .patients import dedup_key, is_done, needs_review, norm_id


class JobManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = threading.Event()
        self._state = {"running": False}
        self._blocked = ""  # เหตุผลที่ห้ามรันงานใหม่จนกว่าจะรีสตาร์ตโปรแกรม

    def _block(self, reason: str) -> None:
        with self._lock:
            self._blocked = reason
            self._state["blocked"] = reason

    @property
    def blocked(self) -> str:
        with self._lock:
            return self._blocked

    def unblock(self) -> None:
        """ปลดล็อกหลังเภสัชกรตรวจหน้าจอ HosXP แล้ว — ใช้แทนการปิดโปรแกรมแล้วเปิดใหม่

        เหตุผลเดิมที่ต้องปิดโปรแกรมคือ "คำสั่งค้าง": ตอนขั้นตอนหนึ่ง timeout
        เธรดที่ค้างยังทำงานต่อ และอาจไปคลิก/พิมพ์ถึง HosXP ทีหลัง
        ถ้าตอนนั้นงานใหม่กำลังคีย์คนอื่นอยู่ = คีย์ผิดคน
        จึงปลดให้เฉพาะตอนที่เธรดค้างตายหมดแล้วจริง ๆ เท่านั้น (ไม่ใช่แค่ผู้ใช้กดยืนยัน)
        เซสชันใหม่ถูกสร้างใหม่ทุกครั้งที่เริ่มงาน ธง poisoned ของเซสชันเก่าจึงไม่ตกทอดมา
        """
        with self._lock:
            if self._state.get("running"):
                raise RuntimeError("มีงานกำลังรันอยู่ — หยุดงานก่อนแล้วค่อยปลดล็อก")
            if not self._blocked:
                return
        try:
            from .robot.session import orphan_threads_alive

            stuck = orphan_threads_alive()
        except Exception:
            stuck = 0  # นำเข้าโมดูลโรบอทไม่ได้ = โรบอทไม่เคยรัน จึงไม่มีคำสั่งค้าง
        if stuck:
            raise RuntimeError(
                f"ยังมีคำสั่งค้างอยู่ {stuck} รายการที่อาจไปถึง HosXP ทีหลัง — ปลดล็อกไม่ได้ตอนนี้ "
                "รอสักครู่แล้วกดใหม่ ถ้ายังไม่หายให้ปิดโปรแกรมนี้แล้วเปิดใหม่"
            )
        with self._lock:
            self._blocked = ""
            self._state["blocked"] = ""

    # ---------- public ----------

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._state.get("running"))

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def start(self, patients: list, dry_run: bool, source: str, operator: str) -> int:
        with self._lock:
            if self._state.get("running"):
                raise RuntimeError("มีงานกำลังรันอยู่ — หยุดงานเดิมก่อน")
            if self._blocked:
                raise RuntimeError(self._blocked)
            run_id = db.create_run(source, len(patients), dry_run, operator)
            self._cancel.clear()
            self._state = {
                "running": True,
                "run_id": run_id,
                "total": len(patients),
                "done": 0,
                "ok": 0,
                "fail": 0,
                "skip": 0,
                "dry_run": dry_run,
                "current": None,
                "phase": "starting",
                "stop_requested": False,
                "blocked": self._blocked,
            }
            self._thread = threading.Thread(
                target=self._worker, args=(run_id, patients, dry_run), daemon=True
            )
            self._thread.start()
            return run_id

    def stop(self) -> None:
        # ตั้ง event ใต้ lock เดียวกับ start() — กันสั่งหยุดไปโดนงานใหม่ที่เพิ่งเริ่ม
        with self._lock:
            run_id = self._state.get("run_id")
            running = self._state.get("running")
            if running and run_id:
                self._cancel.set()
                self._state["stop_requested"] = True
        if running and run_id:
            db.request_cancel(run_id)

    def shutdown(self, timeout: float = 30.0) -> None:
        """ปิดโปรแกรมอย่างสุภาพ — สั่งหยุดแล้วรอให้คนไข้คนปัจจุบันจบก่อน
        (thread เป็น daemon ถ้าไม่รอ อาจถูกฆ่าคาระหว่างกด F9 = ยาบันทึกแล้วแต่ไม่ถูกบันทึกว่าทำแล้ว)"""
        with self._lock:
            thread = self._thread
            running = self._state.get("running")
        if not (running and thread and thread.is_alive()):
            return
        self.stop()
        thread.join(timeout)

    # ---------- worker ----------

    def _set(self, **kw) -> None:
        with self._lock:
            self._state.update(kw)

    def _worker(self, run_id: int, patients: list, dry_run: bool) -> None:
        # ทุกอย่างต้องอยู่ใน try — ถ้า import หรือการเตรียมค่าพังนอก try
        # เธรดจะตายทั้งที่สถานะยังเป็น "กำลังรัน" แล้วกดเริ่มงานใหม่ไม่ได้อีกเลย
        ok = fail = skip = 0
        consecutive_fails = 0
        final_status, final_msg = "done", ""
        cur = {"an": "", "hn": "", "ptname": ""}

        def safe_log(step: str, status: str, message: str) -> None:
            try:
                db.add_log(run_id, cur["an"], cur["hn"], cur["ptname"], step, status, message)
            except Exception:
                traceback.print_exc()

        try:
            from .robot.ipd_addmed import (
                AmbiguousSaveError,
                CancelledMidPatient,
                ChartDirtyError,
                StepFailed,
                run_patient,
            )
            from .robot.session import (
                ConfigError,
                HosxpSession,
                PopupNeedsHuman,
                PopupReviewPatient,
                PopupSkipPatient,
                RobotError,
                StepTimeout,
            )

            settings = load_settings()
            robot_cfg = settings.get("robot", {})
            breaker = robot_cfg.get("circuit_breaker", {})
            pause_after = max(1, int(breaker.get("pause_after_fails", 3)))
            pause_secs = float(breaker.get("pause_seconds", 60))
            abort_after = int(breaker.get("abort_after_fails", 6))
            between = float(robot_cfg.get("timing", {}).get("between_patients", 1.0))
            dedup_hours = int(robot_cfg.get("dedup_window_hours", 20))
            patient_key = robot_cfg.get("ipd_form", {}).get("patient_key", "hn")

            # ตรึง "วัน" ไว้ตั้งแต่เริ่มงาน — งานที่รันคร่อมเที่ยงคืนต้องบันทึกอยู่วันเดียวกันทั้งงาน
            run_day = db.today_str()

            def log(step: str, status: str, message: str) -> None:
                db.add_log(run_id, cur["an"], cur["hn"], cur["ptname"], step, status, message)

            def cancel_requested() -> bool:
                if self._cancel.is_set():
                    return True
                try:  # ธงใน DB ทำให้สั่งหยุดข้ามโปรเซสได้ (เช่นเปิดหน้าเว็บจากอีกแท็บ/สคริปต์)
                    if db.is_cancel_requested(run_id):
                        self._cancel.set()
                        return True
                except Exception:
                    pass
                return False

            def cancel_check() -> None:
                if self._cancel.is_set():
                    raise CancelledMidPatient()

            self._set(phase="connect")
            safe_log("start", "info", f"เริ่มงาน {'(โหมดทดสอบ)' if dry_run else '(คีย์จริง)'} ทั้งหมด {len(patients)} คน")
            session = HosxpSession(settings, safe_log)
            session.connect()
            safe_log("start", "info", "ตรวจว่าหน้า IPD Medication Profile เปิดอยู่")
            session.ensure_ipd_form()
            safe_log("start", "info", "พร้อมทำงาน")
            self._set(phase="running")

            done_index = db.done_index(run_day, dedup_hours)

            def mark(p, status):
                """บันทึกว่าคนไข้รายนี้ถูกแตะแล้ว — เก็บทั้ง AN และ HN เพื่อกันซ้ำข้ามแหล่งข้อมูล"""
                an_n, hn_n = norm_id(p.get("an")), norm_id(p.get("hn"))
                target = "an" if status == "done" else "unknown_an"
                if an_n:
                    done_index.setdefault(target, set()).add(an_n)
                if hn_n:
                    done_index.setdefault("hn" if status == "done" else "unknown_hn", set()).add(hn_n)
                last = None
                for _ in range(4):
                    try:
                        db.mark_done(
                            run_day, dedup_key(p), an_n, hn_n, p.get("hn", ""), run_id, status
                        )
                        return True, None
                    except Exception as e:
                        last = e
                        self._cancel.wait(1.0)
                return False, last

            for i, p in enumerate(patients):
                if cancel_requested():
                    final_status = "cancelled"
                    break

                cur.update(
                    {"an": p.get("an", ""), "hn": p.get("hn", ""), "ptname": p.get("ptname", "")}
                )
                self._set(done=i, ok=ok, fail=fail, skip=skip, current=dict(cur))

                if not p.get(patient_key):
                    skip += 1
                    safe_log("start", "skip", f"ไม่มี {patient_key.upper()} ของคนไข้รายนี้ — ข้าม")
                    self._set(done=i + 1, ok=ok, fail=fail, skip=skip)
                    continue

                # กันคีย์ซ้ำชั้นสุดท้าย (หน้าเว็บกรองให้แล้วรอบหนึ่ง) — เทียบทั้ง AN และ HN
                if not dry_run and is_done(p, done_index):
                    skip += 1
                    safe_log("start", "skip", "คนไข้รายนี้คีย์สำเร็จไปแล้ว — ข้ามเพื่อกันยาออกซ้ำ")
                    self._set(done=i + 1, ok=ok, fail=fail, skip=skip)
                    continue

                # เคยจบแบบไม่รู้ผลการบันทึก — ห้ามคีย์ซ้ำอัตโนมัติ ต้องให้คนไปตรวจใน HosXP ก่อน
                if not dry_run and needs_review(p, done_index):
                    skip += 1
                    safe_log(
                        "start",
                        "skip",
                        "คนไข้รายนี้เคยจบแบบไม่รู้ว่าบันทึกสำเร็จหรือไม่ — ข้าม ต้องเข้าไปตรวจใน HosXP เอง",
                    )
                    self._set(done=i + 1, ok=ok, fail=fail, skip=skip)
                    continue

                try:
                    safe_log(
                        "start",
                        "info",
                        f"เริ่มคีย์ {patient_key.upper()} {p.get(patient_key)} ({p.get('ptname', '')})",
                    )
                    msg = run_patient(session, p, dry_run, cancel_check)
                except CancelledMidPatient:
                    final_status = "cancelled"
                    safe_log(
                        "cancel",
                        "fail",
                        "ถูกสั่งหยุดระหว่างคีย์คนนี้ — ตรวจหน้าจอ HosXP ว่ามีรายการค้างไม่ได้บันทึกหรือไม่",
                    )
                    break
                except AmbiguousSaveError as e:
                    # กดบันทึกไปแล้วแต่ไม่รู้ว่าสำเร็จหรือไม่ — ห้ามเดา ห้ามทำต่อ
                    # และต้องบันทึกไว้ว่า "ไม่รู้ผล" เพื่อไม่ให้รอบหน้าคีย์ซ้ำให้คนนี้อัตโนมัติ
                    fail += 1
                    marked, mark_err = mark(p, "unknown")
                    final_status = "error"
                    final_msg = str(e)
                    if not marked:
                        final_msg += f" (บันทึกสถานะ 'ไม่รู้ผล' ลงฐานข้อมูลไม่สำเร็จด้วย: {mark_err})"
                    safe_log("save", "fail", final_msg)
                    break
                except ChartDirtyError as e:
                    # แก้ชาร์ตค้างไว้แต่ยังไม่บันทึก — ไปคนถัดไปไม่ได้ ต้องให้คนไปเคลียร์หน้าจอก่อน
                    fail += 1
                    final_status, final_msg = "error", str(e)
                    safe_log("chart", "fail", str(e))
                    break
                except PopupReviewPatient as e:
                    # คำเตือนที่เภสัชกรต้องอ่านเอง (เช่น Drug Interaction) แต่ปิดหน้าต่างแล้วหน้าจอปกติ
                    # จึงไม่หยุดทั้งงาน — ติดธง "ต้องตรวจเอง" ให้คนนี้ (กันคีย์ซ้ำ) แล้วทำคนถัดไปต่อ
                    # ถ้าหยุดทั้งงาน คนที่เหลืออีกหลายร้อยคนจะไม่ได้ยา เพราะคนเดียวที่ต้องให้คนดู
                    fail += 1
                    consecutive_fails = 0  # ไม่ใช่อาการระบบพัง จึงไม่นับเข้า circuit breaker
                    marked, mark_err = mark(p, "unknown")
                    msg = str(e)
                    if not marked:
                        msg += (
                            f" (บันทึกสถานะ 'ต้องตรวจเอง' ลงฐานข้อมูลไม่สำเร็จ: {mark_err} "
                            "— ต้องจดคนไข้รายนี้ไว้เองด้วย)"
                        )
                    safe_log("review", "fail", msg)
                except PopupSkipPatient as e:
                    # ไม่ใช่ความผิดพลาดของระบบ (เช่นเวชระเบียนถูกคนอื่นเปิดค้าง) — ข้ามคนนี้ ทำคนถัดไปต่อ
                    skip += 1
                    consecutive_fails = 0
                    safe_log("popup", "skip", str(e))
                except PopupNeedsHuman as e:
                    fail += 1
                    final_status, final_msg = "error", str(e)
                    safe_log("popup", "fail", str(e))
                    break
                except StepTimeout as e:
                    # HosXP ไม่ตอบสนอง — คำสั่งที่ค้างอยู่อาจไปถึง HosXP ทีหลังโดยไม่มีใครคุม
                    # จึงห้ามรันงานใหม่ในโปรเซสนี้อีก จนกว่าจะปิดโปรแกรมแล้วเปิดใหม่
                    fail += 1
                    final_status, final_msg = "error", str(e)
                    safe_log("timeout", "fail", str(e))
                    self._block(
                        "เคยมีขั้นตอนค้างระหว่างสั่งงาน HosXP — ไปตรวจหน้าจอ HosXP ให้เรียบร้อยก่อน "
                        "(ปิด dialog ที่ค้าง / ดูว่ามีรายการยาค้างไม่ได้บันทึกหรือไม่) "
                        "แล้วกดปุ่ม 'ตรวจหน้าจอ HosXP แล้ว — ปลดล็อก' ด้านล่างนี้เพื่อรันต่อ"
                    )
                    break
                except ConfigError as e:
                    fail += 1
                    final_status, final_msg = "error", str(e)
                    safe_log("config", "fail", str(e))
                    break  # ปัญหา config กระทบทุกคน — หยุดทั้งงาน
                except (StepFailed, RobotError) as e:
                    fail += 1
                    consecutive_fails += 1
                    safe_log("finish", "fail", str(e))
                except Exception as e:
                    fail += 1
                    consecutive_fails += 1
                    safe_log("finish", "fail", f"ข้อผิดพลาดไม่คาดคิด: {e}\n{traceback.format_exc(limit=3)}")
                else:
                    # ถึงตรงนี้ = ยาถูกบันทึกใน HosXP แล้ว (ย้อนกลับไม่ได้)
                    # ห้ามให้ exception หลังจากนี้ตกไปเป็น fail ธรรมดา เพราะจะกลายเป็นคีย์ซ้ำรอบหน้า
                    ok += 1
                    consecutive_fails = 0
                    if not dry_run:
                        marked, mark_err = mark(p, "done")
                        if not marked:
                            final_status, final_msg = (
                                "error",
                                f"{dedup_key(p)} บันทึกยาใน HosXP สำเร็จแล้ว "
                                f"แต่บันทึกตารางกันคีย์ซ้ำไม่ได้: {mark_err} — ห้ามรันซ้ำจนกว่าจะตรวจสอบ",
                            )
                            safe_log("finish", "fail", final_msg)
                            break
                    safe_log("finish", "ok", msg)

                self._set(done=i + 1, ok=ok, fail=fail, skip=skip)

                if consecutive_fails >= abort_after:
                    final_status, final_msg = (
                        "error",
                        f"พลาดติดกัน {consecutive_fails} คน — หยุดงานเพื่อความปลอดภัย ตรวจหน้าจอ HosXP ก่อนรันใหม่",
                    )
                    safe_log("breaker", "fail", final_msg)
                    break
                if consecutive_fails and consecutive_fails % pause_after == 0:
                    safe_log(
                        "breaker",
                        "retry",
                        f"พลาดติดกัน {consecutive_fails} คน — พัก {int(pause_secs)} วินาทีแล้วลองต่อ",
                    )
                    if self._cancel.wait(pause_secs):
                        final_status = "cancelled"
                        break

                if self._cancel.wait(between):
                    final_status = "cancelled"
                    break

        except Exception as e:
            final_status, final_msg = "error", str(e)
            safe_log("run", "fail", str(e))
        finally:
            # เขียนผลลง DB ให้เสร็จก่อนประกาศว่าไม่ running — และห้าม exception ตรงนี้ทำให้ manager ค้าง
            summary = f"สำเร็จ {ok} / พลาด {fail} / ข้าม {skip} จาก {len(patients)} คน"
            for fn in (
                lambda: db.update_counts(run_id, ok, fail, skip),
                lambda: db.finish_run(
                    run_id, final_status, (final_msg + " " if final_msg else "") + summary
                ),
                lambda: db.add_log(run_id, "", "", "", "run", "info", f"จบงาน ({final_status}) — {summary}"),
            ):
                try:
                    fn()
                except Exception:
                    traceback.print_exc()
            self._set(
                running=False,
                done=ok + fail + skip,
                ok=ok,
                fail=fail,
                skip=skip,
                current=None,
                phase="finished",
            )


manager = JobManager()
