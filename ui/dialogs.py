# timeclock/ui/dialogs.py
# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets, QtCore
import sqlite3
import logging

from timeclock.utils import Message
from timeclock import sync_manager
from ui.async_helper import run_job_with_progress_async


class _SilentWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    @QtCore.pyqtSlot()
    def run(self):
        try:
            ok = bool(self._fn())
            self.finished.emit(ok, "")
        except Exception as e:
            self.finished.emit(False, str(e))

class ChangePasswordDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("비밀번호 변경")
        self.setModal(True)
        self.resize(380, 170)

        self.le_new = QtWidgets.QLineEdit()
        self.le_new.setEchoMode(QtWidgets.QLineEdit.Password)
        self.le_new2 = QtWidgets.QLineEdit()
        self.le_new2.setEchoMode(QtWidgets.QLineEdit.Password)

        form = QtWidgets.QFormLayout()
        form.addRow("새 비밀번호", self.le_new)
        form.addRow("새 비밀번호(확인)", self.le_new2)

        self.btn_ok = QtWidgets.QPushButton("변경")
        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addLayout(btns)
        self.setLayout(layout)

    def get_password(self):
        p1 = self.le_new.text().strip()
        p2 = self.le_new2.text().strip()
        if not p1 or len(p1) < 4:
            return None
        if p1 != p2:
            return None
        return p1


# ==========================================================
# ★ [최종 수정] 이의 제기 대화방 (중첩 테이블로 강제 줄바꿈)
# ==========================================================
class DisputeTimelineDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, db=None, user_id=None, dispute_id=None, my_role="worker"):
        super().__init__(parent)
        self.db = db
        self.user_id = user_id
        self.dispute_id = dispute_id
        self.my_role = my_role

        self.current_status = "PENDING"
        self.header_info = {}
        self._load_data()

        self.setWindowTitle("이의 제기 대화방")
        self.resize(550, 800)

        # --- 내부 상태(카톡식) ---
        self._sync_in_progress = False
        self._pending_upload = False  # 업로드 실패/대기 플래그

        # ---------------- 레이아웃 구성 ----------------
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. 상단 고정 헤더
        self.header_widget = self._create_fixed_header()
        layout.addWidget(self.header_widget)

        # 2. 채팅 브라우저
        self.browser = QtWidgets.QTextBrowser()
        self.browser.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.browser.setStyleSheet("background-color: #b2c7d9;")
        layout.addWidget(self.browser, 1)

        # 3. 하단 입력창
        input_container = QtWidgets.QWidget()
        input_container.setStyleSheet("background-color: white; border-top: 1px solid #ddd;")
        input_layout = QtWidgets.QHBoxLayout(input_container)
        input_layout.setContentsMargins(10, 10, 10, 10)

        # [사업주 전용] 상태 변경 콤보박스
        self.cb_status = None
        if self.my_role == "owner":
            self.cb_status = QtWidgets.QComboBox()
            self.cb_status.addItem("검토 중", "IN_REVIEW")
            self.cb_status.addItem("처리 완료", "RESOLVED")
            self.cb_status.addItem("기각", "REJECTED")
            self._set_combo_index_by_data(self.current_status)
            self.cb_status.setMinimumHeight(35)
            input_layout.addWidget(self.cb_status)

        # ✅ 조용한 동기화 상태 표시(작게)
        self.lbl_sync = QtWidgets.QLabel("")
        self.lbl_sync.setStyleSheet("color:#777; font-size:11px; padding-right:6px;")
        input_layout.addWidget(self.lbl_sync)

        self.le_input = QtWidgets.QLineEdit()
        self.le_input.setPlaceholderText("메시지를 입력하세요...")
        self.le_input.setMinimumHeight(35)
        self.le_input.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.le_input, 1)

        self.btn_send = QtWidgets.QPushButton("전송")
        # noinspection PyUnresolvedReferences
        self.btn_send.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_send.setStyleSheet("""
            QPushButton {
                background-color: #fef01b; color: #3c1e1e; border: none;
                border-radius: 4px; padding: 0 15px; font-weight: bold; height: 35px;
            }
            QPushButton:hover { background-color: #e5d817; }
        """)
        self.btn_send.clicked.connect(self.send_message)
        input_layout.addWidget(self.btn_send)

        layout.addWidget(input_container)
        self.setLayout(layout)

        # 최초 표시
        self.refresh_timeline()

        # ✅ 카톡처럼: 주기적으로 조용히(입력 중이면 건너뜀) 최신 내용 반영
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(1500)  # 1.5초
        self._poll_timer.timeout.connect(self._silent_poll_refresh)
        self._poll_timer.start()

    def _ensure_db_conn(self) -> bool:
        """
        conn이 None인 상태(=DB가 닫힌 상태)에서 execute가 불리면 바로 터진다.
        대화방에서는 항상 '사용 직전'에 연결을 보장한다.
        """
        if not self.db:
            return False
        try:
            if getattr(self.db, "conn", None) is None:
                self.db.reconnect()
            return True
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "오류", f"DB 재연결 실패: {e}\n프로그램을 재시작하세요.")
            return False

    def _load_data(self):
        if not self.db or not self.dispute_id:
            return

        if not self._ensure_db_conn():
            return

        try:
            row = self.db.conn.execute(
                "SELECT work_log_id, dispute_type, status FROM disputes WHERE id=?",
                (self.dispute_id,)
            ).fetchone()
            if not row:
                return

            work_log_id, dispute_type, status = row
            self.current_status = status or "PENDING"

            # 헤더에 표시할 정보
            w = self.db.conn.execute(
                """
                SELECT w.work_date, u.username
                FROM work_logs w
                LEFT JOIN users u ON u.id = w.user_id
                WHERE w.id=?
                """,
                (work_log_id,)
            ).fetchone()

            self.header_info = {
                "work_date": w[0] if w else "",
                "worker_username": w[1] if w else "",
                "dispute_type": dispute_type or "",
            }
        except Exception as e:
            logging.exception("_load_data failed")
            QtWidgets.QMessageBox.warning(self, "오류", f"이의제기 정보 로드 실패: {e}")

    def _create_fixed_header(self):
        widget = QtWidgets.QWidget()
        widget.setStyleSheet("background-color: #e2e2e2; border-bottom: 1px solid #c0c0c0;")

        vbox = QtWidgets.QVBoxLayout(widget)
        vbox.setContentsMargins(15, 10, 15, 10)
        vbox.setSpacing(4)

        w_date = self.header_info.get("work_date", "-")
        d_type = self.header_info.get("dispute_type", "-")

        lbl_info = QtWidgets.QLabel(f"<b>근무 일자:</b> {w_date}")
        lbl_info.setStyleSheet("font-size: 14px; color: #333;")
        # noinspection PyUnresolvedReferences
        lbl_info.setAlignment(QtCore.Qt.AlignCenter)

        lbl_type = QtWidgets.QLabel(f"<b>이의 유형:</b> {d_type}")
        lbl_type.setStyleSheet("font-size: 13px; color: #d9534f;")
        # noinspection PyUnresolvedReferences
        lbl_type.setAlignment(QtCore.Qt.AlignCenter)

        vbox.addWidget(lbl_info)
        vbox.addWidget(lbl_type)

        return widget

    def _set_combo_index_by_data(self, status_code):
        if not self.cb_status: return
        idx = self.cb_status.findData(status_code)
        if idx >= 0:
            self.cb_status.setCurrentIndex(idx)
        else:
            self.cb_status.setCurrentIndex(0)

    def _silent_upload(self, tag="dispute_chat"):
        # 중복 업로드 방지
        if getattr(self, "_uploading", False):
            return
        self._uploading = True

        def _do():
            try:
                from timeclock import backup_manager, sync_manager
                backup_manager.run_backup(tag)
                ok = sync_manager.upload_current_db()
                return ok, None
            except Exception as e:
                return False, str(e)

        def _done(result):
            self._uploading = False
            ok, err = result
            if not ok and err:
                # 조용히 실패 로그만 남기고, UX는 유지
                try:
                    import logging
                    logging.exception("[DisputeChat] silent upload failed: %s", err)
                except Exception:
                    pass

        self._run_silent(_do, _done)

    def _run_silent(self, work_fn, done_fn):
        # dialogs.py 안에서 쓰는 조용한 스레드 실행기
        try:
            from async_helper import SilentWorker
        except Exception:
            # fallback: 최소 동작
            import threading

            def _t():
                res = work_fn()
                QtCore.QTimer.singleShot(0, lambda: done_fn(res))

            threading.Thread(target=_t, daemon=True).start()
            return

        w = SilentWorker(work_fn)
        w.finished.connect(done_fn)
        w.start()

        # worker가 GC로 날아가지 않도록 보관
        if not hasattr(self, "_silent_workers"):
            self._silent_workers = []
        self._silent_workers.append(w)

    def send_message(self):
        try:
            msg = (self.input.toPlainText() or "").strip()
            if not msg:
                return

            # DB 연결 보장 (동기화/새로고침에서 close될 수 있음)
            if hasattr(self.db, "ensure_connection"):
                self.db.ensure_connection()

            # ✅ add_dispute_message 시그니처: (dispute_id, user_id, sender_role, message, ...)
            # DB.add_dispute_message()가 4개 필수 인자를 요구하는 구조로 되어 있으므로 반드시 맞춰 호출
            self.db.add_dispute_message(
                self.dispute_id,
                self.user_id,
                self.my_role,
                msg
            )

            self.input.clear()
            self.refresh_timeline()

            # 채팅은 “즉시 업로드”가 UX적으로 중요 → 조용히 업로드 트리거
            self._silent_upload(tag="dispute_chat")

        except Exception as e:
            import traceback
            traceback.print_exc()
            Message.err(self, "오류", f"메시지 저장 실패: {e}")

    def _silent_poll_refresh(self):
        """
        카톡처럼:
        - 타이핑 중에는 절대 방해하지 않음
        - 유휴 상태일 때만 서버 최신 DB를 조용히 내려받고(가능하면) 화면 갱신
        """
        # 1) 입력 중이면 건너뜀(타이핑 방해 금지)
        if self.le_input.hasFocus() or (self.le_input.text().strip() != ""):
            return

        # 2) 업로드/다운로드 진행 중이면 건너뜀
        if getattr(self, "_sync_in_progress", False):
            return

        self._sync_in_progress = True
        self.lbl_sync.setText("동기화 중…")

        def _job():
            # DB 파일 교체(download_latest_db)는 파일 잠금이 치명적이므로
            # 반드시 연결을 끊고 교체 후 재연결
            try:
                try:
                    self.db.close_connection()
                except Exception:
                    pass

                ok, _msg = sync_manager.download_latest_db()
                return ok
            finally:
                try:
                    self.db.reconnect()
                except Exception:
                    # reconnect 실패는 다음 액션에서 터질 수 있으므로 False로 반환
                    return False

        self._poll_thread = QtCore.QThread(self)
        self._poll_worker = _SilentWorker(_job)
        self._poll_worker.moveToThread(self._poll_thread)

        def _on_done(ok: bool, err: str):
            self._sync_in_progress = False

            if ok:
                # 조용히 최신 반영
                self.lbl_sync.setText("")
                try:
                    self.refresh_timeline()
                except Exception:
                    pass
            else:
                # 팝업 대신 상태만 표시(타이핑 UX 유지)
                self.lbl_sync.setText("동기화 실패(대기)…")

        self._poll_thread.started.connect(self._poll_worker.run)
        self._poll_worker.finished.connect(_on_done)
        self._poll_worker.finished.connect(self._poll_thread.quit)
        self._poll_worker.finished.connect(self._poll_worker.deleteLater)
        self._poll_thread.finished.connect(self._poll_thread.deleteLater)

        self._poll_thread.start()

    def _on_upload_done(self, ok: bool, err: str):
        self._sync_in_progress = False

        if ok:
            self.lbl_sync.setText("")  # 조용히 성공
            # 업로드 성공이면 “대기 업로드” 플래그는 해제
            self._pending_upload = False
            self._upload_retry_count = 0
            return

        # 업로드 실패: 팝업 대신 라벨만 표시 + 대기 상태로 전환
        self.lbl_sync.setText("동기화 실패(대기)…")
        self._pending_upload = True

        # 과도한 재시도(200ms 연타) 방지: 점진적 백오프
        cnt = getattr(self, "_upload_retry_count", 0) + 1
        self._upload_retry_count = cnt

        # 1회: 1초, 2~3회: 3초, 4회 이상: 8초
        if cnt <= 1:
            delay_ms = 1000
        elif cnt <= 3:
            delay_ms = 3000
        else:
            delay_ms = 8000

        # 타이핑 중이면 재시도 안 함(다음 전송/유휴 때 자연스럽게)
        if self.le_input.hasFocus() or (self.le_input.text().strip() != ""):
            return

        QtCore.QTimer.singleShot(delay_ms, self._silent_upload)

    def _poll_refresh(self):
        """
        준-실시간 갱신:
        - DB 연결을 잠깐 끊고
        - 클라우드 최신 DB를 다운로드(있으면)
        - 다시 연결 후 타임라인 갱신
        """
        if self._sending:
            return

        if not self.db:
            return

        # 다운로드가 실패해도(클라우드 DB 없음 등) 화면은 로컬 기준으로 유지
        try:
            self.db.close_connection()
        except Exception:
            pass

        def job_fn(progress_callback):
            # 폴링은 조용히 처리(메시지 최소)
            ok, msg = sync_manager.download_latest_db()
            return ok, msg

        def on_done(ok_thread, result_data, err):
            try:
                self.db.reconnect()
            except Exception:
                return

            # 최신 DB 반영 후 화면 갱신
            try:
                self._load_data()
                self.refresh_timeline()
            except Exception:
                pass

        run_job_with_progress_async(
            self,
            "동기화 중...",
            job_fn,
            on_done=on_done
        )

    def closeEvent(self, event):
        try:
            if hasattr(self, "_poll_timer") and self._poll_timer:
                self._poll_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)

    def refresh_timeline(self):
        # DB 연결 보장
        try:
            if hasattr(self.db, "ensure_connection"):
                self.db.ensure_connection()
            elif getattr(self.db, "conn", None) is None:
                self.db.reconnect()
        except Exception:
            return

        try:
            timeline_events = self.db.get_dispute_timeline(self.dispute_id)
        except Exception:
            return

        # 이하(HTML 렌더링)는 기존 구현 그대로 유지되어도 됩니다.
        # 너의 dialogs.py는 QTextBrowser 기반 HTML 렌더링을 쓰고 있으니,
        # 여기서는 DB 안정성만 보강하고 나머지는 건드리지 않습니다.

        KAKAO_BG = "#B2C7D9"
        MY_BUBBLE = "#FEE500"
        OTHER_BUBBLE = "#FFFFFF"
        TIME_COLOR = "#666666"
        SPACER_W = "45%"

        def esc(s: str) -> str:
            if s is None:
                return ""
            s = str(s)
            s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            s = s.replace("\n", "<br>")
            return s

        def date_only(ts: str) -> str:
            if not ts:
                return ""
            return ts.split(" ")[0].strip()

        def time_only(ts: str) -> str:
            if not ts:
                return ""
            parts = ts.split(" ")
            if len(parts) < 2:
                return ts
            tpart = parts[1]
            return tpart[:5] if len(tpart) >= 5 else tpart

        def date_chip(xd: str) -> str:
            xd = esc(xd)
            return f"""
            <div align="center" style="margin:10px 0 14px 0;">
              <span style="background:#D7E2EC; color:#333; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:bold;">
                {xd}
              </span>
            </div>
            """

        def sys_chip(msg: str) -> str:
            msg = esc(msg)
            return f"""
            <div align="center" style="margin:18px 0 6px 0;">
              <span style="background:#90A4AE; color:#fff; padding:5px 12px; border-radius:14px; font-size:12px; font-weight:bold;">
                {msg}
              </span>
            </div>
            """

        def bubble_html(text: str, ttime_str: str, bg: str, align: str) -> str:
            text = esc(text).strip()
            ttime_str = esc(ttime_str)
            if not text:
                return ""
            bubble_div = (
                f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;">'
                f'  <tr>'
                f'    <td bgcolor="{bg}" style="padding:10px 14px; border-radius:8px; font-size:13px; color:#111;">{text}</td>'
                f'    <td style="width:8px;"></td>'
                f'    <td style="font-size:11px; color:{TIME_COLOR}; vertical-align:bottom; white-space:nowrap;">{ttime_str}</td>'
                f'  </tr>'
                f'</table>'
            )
            if align == "right":
                return f'<div align="right" style="margin:8px 10px 8px {SPACER_W};">{bubble_div}</div>'
            return f'<div align="left" style="margin:8px {SPACER_W} 8px 10px;">{bubble_div}</div>'

        html = ""
        last_date = None
        for ev in timeline_events:
            who = ev.get("who") or ""
            text = ev.get("comment") or ""
            at = ev.get("at") or ""
            st_code = ev.get("status_code")

            d = date_only(at)
            t = time_only(at)

            if d and d != last_date:
                last_date = d
                html += date_chip(d)

            # 상태 시스템 메시지(옵션)
            if st_code and who == "owner":
                # 예: 상태 변경을 대화 중간에 표시하고 싶으면 여기서 sys_chip 사용
                pass

            is_me = (self.my_role == "owner" and who == "owner") or (self.my_role != "owner" and who == "worker")
            bg = MY_BUBBLE if is_me else OTHER_BUBBLE
            align = "right" if is_me else "left"
            html += bubble_html(text, t, bg, align)

        self.browser.setHtml(f'<div style="background:{KAKAO_BG}; padding:10px;">{html}</div>')
        QtCore.QTimer.singleShot(50, lambda: self.browser.verticalScrollBar().setValue(
            self.browser.verticalScrollBar().maximum()))


# timeclock/ui/dialogs.py 파일 맨 아래에 추가하세요.

class DateRangeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("기간 선택")
        self.resize(300, 150)

        layout = QtWidgets.QVBoxLayout()

        # 설명 라벨
        lbl_guide = QtWidgets.QLabel("급여를 정산할 기간을 선택하세요.")
        # noinspection PyUnresolvedReferences
        lbl_guide.setAlignment(QtCore.Qt.AlignCenter)
        lbl_guide.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(lbl_guide)

        # 폼 레이아웃 (시작일, 종료일)
        form = QtWidgets.QFormLayout()

        # 오늘 날짜 기준
        now = QtCore.QDate.currentDate()
        first_day = QtCore.QDate(now.year(), now.month(), 1)

        # 시작일 위젯 (달력 팝업 활성화)
        self.de_start = QtWidgets.QDateEdit()
        self.de_start.setCalendarPopup(True)  # ★ 핵심: 달력 팝업 켜기
        self.de_start.setDisplayFormat("yyyy-MM-dd")
        self.de_start.setDate(first_day)  # 이번달 1일 기본값

        # 종료일 위젯
        self.de_end = QtWidgets.QDateEdit()
        self.de_end.setCalendarPopup(True)  # ★ 핵심
        self.de_end.setDisplayFormat("yyyy-MM-dd")
        self.de_end.setDate(now)  # 오늘 날짜 기본값

        form.addRow("시작일:", self.de_start)
        form.addRow("종료일:", self.de_end)

        layout.addLayout(form)

        # 버튼 (확인/취소)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.setLayout(layout)

    def get_range(self):
        # 문자열(YYYY-MM-DD) 형태로 반환
        s = self.de_start.date().toString("yyyy-MM-dd")
        e = self.de_end.date().toString("yyyy-MM-dd")
        return s, e


class ConfirmPasswordDialog(QtWidgets.QDialog):
    """개인정보 변경 진입 전, 현재 비밀번호 재확인."""
    def __init__(self, parent=None, title: str = "비밀번호 확인", message: str = "현재 비밀번호를 입력해 주세요."):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(380, 170)

        self._pw = ""

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)

        lb = QtWidgets.QLabel(message)
        lb.setWordWrap(True)
        lb.setStyleSheet("font-size:13px; color:#333;")
        v.addWidget(lb)

        self.le_pw = QtWidgets.QLineEdit()
        self.le_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.le_pw.setPlaceholderText("현재 비밀번호")
        self.le_pw.setStyleSheet(
            "QLineEdit{border:1px solid #ddd; border-radius:10px; padding:10px 12px; font-size:13px;}"
            "QLineEdit:focus{border-color:#7aa7ff;}"
        )
        v.addWidget(self.le_pw)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch()

        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_ok = QtWidgets.QPushButton("확인")
        for b in (self.btn_cancel, self.btn_ok):
            # noinspection PyUnresolvedReferences
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setMinimumHeight(34)
            b.setStyleSheet(
                "QPushButton{border:1px solid #ddd; border-radius:10px; padding:7px 14px; background:#fafafa;}"
                "QPushButton:hover{background:#f0f0f0;}"
            )
        self.btn_ok.setStyleSheet(
            "QPushButton{border:1px solid #ffe066; border-radius:10px; padding:7px 14px; background:#FEE500; font-weight:bold;}"
            "QPushButton:hover{background:#ffe45c;}"
        )

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._accept)

        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        v.addLayout(btns)

        self.le_pw.returnPressed.connect(self._accept)

    def _accept(self):
        self._pw = self.le_pw.text().strip()
        if not self._pw:
            QtWidgets.QMessageBox.warning(self, "확인", "비밀번호를 입력해 주세요.")
            return
        self.accept()

    def password(self) -> str:
        return self._pw


class ProfileEditDialog(QtWidgets.QDialog):
    """아이디 제외 개인 정보 변경(기본: 이름/연락처/생년월일)."""
    saved = QtCore.pyqtSignal()

    def __init__(self, db, user_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.user_id = user_id

        self.setWindowTitle("개인정보 변경")
        self.setModal(True)
        self.resize(460, 360)

        u = None
        try:
            u = self.db.get_user_by_id(user_id)
        except Exception:
            u = None

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)

        title = QtWidgets.QLabel("개인정보 변경")
        title.setStyleSheet("font-size:18px; font-weight:800; color:#222;")
        v.addWidget(title)

        sub = QtWidgets.QLabel("아이디는 변경할 수 없습니다. 변경 후 저장을 눌러주세요.")
        sub.setStyleSheet("font-size:12px; color:#666;")
        v.addWidget(sub)

        form = QtWidgets.QFormLayout()
        # noinspection PyUnresolvedReferences
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        # noinspection PyUnresolvedReferences
        form.setFormAlignment(QtCore.Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        def mk_le(placeholder: str):
            le = QtWidgets.QLineEdit()
            le.setPlaceholderText(placeholder)
            le.setStyleSheet(
                "QLineEdit{border:1px solid #ddd; border-radius:12px; padding:10px 12px; font-size:13px;}"
                "QLineEdit:focus{border-color:#7aa7ff;}"
            )
            return le

        self.le_username = mk_le("아이디")
        self.le_username.setReadOnly(True)
        self.le_username.setStyleSheet(
            "QLineEdit{border:1px solid #e6e6e6; border-radius:12px; padding:10px 12px; font-size:13px; background:#f7f7f7; color:#777;}"
        )

        self.le_name = mk_le("예: 홍길동")
        self.le_phone = mk_le("예: 010-1234-5678")
        self.le_birth = mk_le("예: 1990-01-31 (YYYY-MM-DD)")

        if u:
            self.le_username.setText(str(u.get("username", "") or ""))
            self.le_name.setText(str(u.get("name", "") or ""))
            self.le_phone.setText(str(u.get("phone", "") or ""))
            self.le_birth.setText(str(u.get("birthdate", "") or ""))

        form.addRow("아이디", self.le_username)
        form.addRow("이름", self.le_name)
        form.addRow("연락처", self.le_phone)
        form.addRow("생년월일", self.le_birth)

        v.addLayout(form)
        v.addStretch()

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch()

        self.btn_cancel = QtWidgets.QPushButton("닫기")
        self.btn_save = QtWidgets.QPushButton("저장")

        for b in (self.btn_cancel, self.btn_save):
            # noinspection PyUnresolvedReferences
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setMinimumHeight(38)
            b.setStyleSheet(
                "QPushButton{border:1px solid #ddd; border-radius:12px; padding:8px 16px; background:#fafafa;}"
                "QPushButton:hover{background:#f0f0f0;}"
            )
        self.btn_save.setStyleSheet(
            "QPushButton{border:1px solid #ffe066; border-radius:12px; padding:8px 16px; background:#FEE500; font-weight:800;}"
            "QPushButton:hover{background:#ffe45c;}"
        )

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_save.clicked.connect(self._save)

        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_save)
        v.addLayout(btns)

    def _save(self):
        name = self.le_name.text().strip()
        phone = self.le_phone.text().strip()
        birth = self.le_birth.text().strip()

        if birth and not QtCore.QRegExp(r"^\d{4}-\d{2}-\d{2}$").exactMatch(birth):
            QtWidgets.QMessageBox.warning(self, "형식 오류", "생년월일은 YYYY-MM-DD 형식으로 입력해 주세요.")
            return

        try:
            self.db.update_user_profile(self.user_id, name=name, phone=phone, birthdate=birth)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "오류", f"저장 실패: {e}")
            return

        QtWidgets.QMessageBox.information(self, "완료", "개인정보가 저장되었습니다.")
        self.saved.emit()
        self.accept()




class PersonalInfoDialog(QtWidgets.QDialog):
    """
    개인정보 변경 다이얼로그
    - 현재 비밀번호 재확인 필수
    - username(id)는 수정 불가
    - (옵션) 한 화면에서 비밀번호 변경도 가능: 새 비밀번호 입력 시에만 변경 처리
    """

    def __init__(self, db, user_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.user_id = int(user_id)

        self.setWindowTitle("개인정보 변경")
        self.setModal(True)
        self.resize(520, 520)

        prof = self.db.get_user_profile(self.user_id) or {}
        self._orig = prof

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QtWidgets.QLabel("개인정보 변경")
        title.setStyleSheet("font-size:18px; font-weight:800;")
        root.addWidget(title)

        desc = QtWidgets.QLabel("보안을 위해 현재 비밀번호를 먼저 확인합니다.")
        desc.setStyleSheet("color:#666;")
        root.addWidget(desc)

        # ---- 현재 비밀번호 확인 ----
        pw_box = QtWidgets.QGroupBox("현재 비밀번호 확인 (필수)")
        pw_lay = QtWidgets.QFormLayout(pw_box)
        # noinspection PyUnresolvedReferences
        pw_lay.setLabelAlignment(QtCore.Qt.AlignLeft)
        # noinspection PyUnresolvedReferences
        pw_lay.setFormAlignment(QtCore.Qt.AlignTop)
        pw_lay.setHorizontalSpacing(12)
        pw_lay.setVerticalSpacing(10)

        self.ed_cur_pw = QtWidgets.QLineEdit()
        self.ed_cur_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_cur_pw.setPlaceholderText("현재 비밀번호를 입력하세요")
        self.ed_cur_pw.setMinimumHeight(34)
        pw_lay.addRow("현재 비밀번호", self.ed_cur_pw)

        root.addWidget(pw_box)

        # ---- 개인정보 ----
        info_box = QtWidgets.QGroupBox("개인정보")
        form = QtWidgets.QFormLayout(info_box)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.ed_username = QtWidgets.QLineEdit(prof.get("username", "") or "")
        self.ed_username.setReadOnly(True)
        self.ed_username.setMinimumHeight(34)
        self.ed_username.setStyleSheet("background:#f3f3f3; color:#666;")
        form.addRow("아이디(ID)", self.ed_username)

        self.ed_name = QtWidgets.QLineEdit(prof.get("name", "") or "")
        self.ed_name.setMinimumHeight(34)
        form.addRow("성명", self.ed_name)

        self.ed_phone = QtWidgets.QLineEdit(prof.get("phone", "") or "")
        self.ed_phone.setMinimumHeight(34)
        self.ed_phone.setPlaceholderText("숫자만 또는 010-0000-0000")
        form.addRow("전화번호", self.ed_phone)

        self.ed_birth = QtWidgets.QLineEdit(prof.get("birthdate", "") or "")
        self.ed_birth.setMinimumHeight(34)
        self.ed_birth.setPlaceholderText("YYYY-MM-DD")
        form.addRow("생년월일", self.ed_birth)

        self.ed_email = QtWidgets.QLineEdit(prof.get("email", "") or "")
        self.ed_email.setMinimumHeight(34)
        form.addRow("이메일", self.ed_email)

        self.ed_account = QtWidgets.QLineEdit(prof.get("account", "") or "")
        self.ed_account.setMinimumHeight(34)
        form.addRow("계좌정보", self.ed_account)

        self.ed_address = QtWidgets.QLineEdit(prof.get("address", "") or "")
        self.ed_address.setMinimumHeight(34)
        form.addRow("주소", self.ed_address)

        root.addWidget(info_box)

        # ---- (확장) 비밀번호 변경(선택) ----
        pw2_box = QtWidgets.QGroupBox("비밀번호 변경 (선택)")
        pw2 = QtWidgets.QFormLayout(pw2_box)
        pw2.setHorizontalSpacing(12)
        pw2.setVerticalSpacing(10)

        self.ed_new_pw = QtWidgets.QLineEdit()
        self.ed_new_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_new_pw.setMinimumHeight(34)
        self.ed_new_pw.setPlaceholderText("새 비밀번호(입력 시 변경)")
        pw2.addRow("새 비밀번호", self.ed_new_pw)

        self.ed_new_pw2 = QtWidgets.QLineEdit()
        self.ed_new_pw2.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_new_pw2.setMinimumHeight(34)
        self.ed_new_pw2.setPlaceholderText("새 비밀번호 확인")
        pw2.addRow("새 비밀번호 확인", self.ed_new_pw2)

        root.addWidget(pw2_box)

        # ---- 버튼 ----
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)

        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_cancel.setMinimumHeight(36)
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QtWidgets.QPushButton("저장")
        self.btn_save.setMinimumHeight(36)
        self.btn_save.clicked.connect(self._on_save)

        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_save)
        root.addLayout(btns)

    def _on_save(self):
        cur_pw = (self.ed_cur_pw.text() or "").strip()
        if not cur_pw:
            Message.err(self, "확인", "현재 비밀번호를 입력해주세요.")
            return

        if not self.db.verify_user_password(self.user_id, cur_pw):
            Message.err(self, "확인", "현재 비밀번호가 올바르지 않습니다.")
            return

        # 비밀번호 변경(선택)
        new_pw = (self.ed_new_pw.text() or "")
        new_pw2 = (self.ed_new_pw2.text() or "")
        if new_pw or new_pw2:
            if len(new_pw) < 6:
                Message.err(self, "비밀번호 변경", "새 비밀번호는 6자 이상이어야 합니다.")
                return
            if new_pw != new_pw2:
                Message.err(self, "비밀번호 변경", "새 비밀번호가 서로 일치하지 않습니다.")
                return

        # 개인정보 저장
        name = (self.ed_name.text() or "").strip()
        phone = (self.ed_phone.text() or "").strip()
        birth = (self.ed_birth.text() or "").strip()
        email = (self.ed_email.text() or "").strip()
        account = (self.ed_account.text() or "").strip()
        address = (self.ed_address.text() or "").strip()

        try:
            self.db.update_user_profile(
                self.user_id,
                name=name or None,
                phone=phone or None,
                birthdate=birth or None,
                email=email or None,
                account=account or None,
                address=address or None,
            )
            if new_pw:
                self.db.change_password(self.user_id, new_pw)

        except Exception as e:
            Message.err(self, "저장 실패", str(e))
            return

        Message.info(self, "완료", "개인정보가 저장되었습니다.")
        self.accept()

