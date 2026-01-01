# timeclock/ui/dialogs.py
# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets, QtCore
import sqlite3
import logging
import os
from timeclock.utils import Message
from timeclock import sync_manager
from ui.async_helper import run_job_with_progress_async
# 기존 임포트 코드 아래에 추가합니다.
from timeclock.settings import _MIN_CALL_INTERVAL_SEC

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


# timeclock/ui/dialogs.py 내 ChangePasswordDialog 클래스 전체

class ChangePasswordDialog(QtWidgets.QDialog):
    """더 예쁘고 친절한 비밀번호 변경 창"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("비밀번호 변경")
        self.setModal(True)
        self.resize(400, 280)  # 안내 문구를 위해 높이를 조금 키움
        self.setStyleSheet("background-color: white;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(15)

        # 1. 상단 타이틀 및 안내
        title_label = QtWidgets.QLabel("새로운 비밀번호 설정")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #333;")
        layout.addWidget(title_label)

        desc_label = QtWidgets.QLabel("보안을 위해 6자리 이상의 비밀번호를 입력해 주세요.")
        desc_label.setStyleSheet("font-size: 12px; color: #d9534f; font-weight: bold;")  # 빨간색 강조
        layout.addWidget(desc_label)

        # 2. 입력 폼 레이아웃
        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(12)
        form.setLabelAlignment(QtCore.Qt.AlignLeft)

        input_style = """
            QLineEdit {
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 10px;
                font-size: 14px;
                background-color: #f9f9f9;
            }
            QLineEdit:focus {
                border: 1px solid #6D4C41;
                background-color: white;
            }
        """

        self.le_new = QtWidgets.QLineEdit()
        self.le_new.setEchoMode(QtWidgets.QLineEdit.Password)
        self.le_new.setPlaceholderText("새 비밀번호 입력")
        self.le_new.setStyleSheet(input_style)

        self.le_new2 = QtWidgets.QLineEdit()
        self.le_new2.setEchoMode(QtWidgets.QLineEdit.Password)
        self.le_new2.setPlaceholderText("비밀번호 확인 입력")
        self.le_new2.setStyleSheet(input_style)

        form.addRow("새 비밀번호", self.le_new)
        form.addRow("비밀번호 확인", self.le_new2)
        layout.addLayout(form)

        layout.addStretch(1)

        # 3. 버튼 레이아웃
        btns = QtWidgets.QHBoxLayout()
        btns.setSpacing(10)

        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_cancel.setFixedHeight(40)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f5; color: #666; border: 1px solid #ddd; border-radius: 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: #eee; }
        """)

        self.btn_ok = QtWidgets.QPushButton("비밀번호 변경")
        self.btn_ok.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_ok.setFixedHeight(40)
        self.btn_ok.setStyleSheet("""
            QPushButton {
                background-color: #6D4C41; color: white; border: none; border-radius: 8px; font-weight: bold;
            }
            QPushButton:hover { background-color: #5d4036; }
        """)

        self.btn_ok.clicked.connect(self._on_accept)
        self.btn_cancel.clicked.connect(self.reject)

        btns.addWidget(self.btn_cancel, 1)
        btns.addWidget(self.btn_ok, 2)
        layout.addLayout(btns)

    def _on_accept(self):
        p1 = self.le_new.text().strip()
        p2 = self.le_new2.text().strip()

        if not p1:
            QtWidgets.QMessageBox.warning(self, "알림", "새 비밀번호를 입력하세요.")
            return
        if len(p1) < 6:
            QtWidgets.QMessageBox.warning(self, "알림", "비밀번호는 반드시 6자리 이상이어야 합니다.")
            return
        if p1 != p2:
            QtWidgets.QMessageBox.warning(self, "알림", "비밀번호가 서로 일치하지 않습니다.")
            return

        self.accept()

    def get_password(self):
        return self.le_new.text().strip()


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

        # ✅ 호환성: 예전 코드/다른 파일에서 self.input 을 참조해도 안 터지게 alias 제공
        self.input = self.le_input

        self.btn_send = QtWidgets.QPushButton("전송")
        # noinspection PyUnresolvedReferences
        self.btn_send.setCursor(QtCore.Qt.PointingHandCursor)

        # ✅ [스타일 수정] :disabled 상태일 때 배경색을 회색(#ccc)으로, 글자색을 #888로 변경합니다.
        self.btn_send.setStyleSheet("""
            QPushButton {
                background-color: #fef01b; color: #3c1e1e; border: none;
                border-radius: 4px; padding: 0 15px; font-weight: bold; height: 35px;
            }
            QPushButton:hover { background-color: #e5d817; }
            QPushButton:disabled { background-color: #cccccc; color: #888888; }
        """)
        self.btn_send.clicked.connect(self.send_message)
        input_layout.addWidget(self.btn_send)

        layout.addWidget(input_container)
        self.setLayout(layout)

        # 최초 표시
        self.refresh_timeline()

        # ✅ 폴링(다운로드/DB교체) 간격 관리
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(2000)  # 2초
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
            # ✅ 성공 여부와 관계없이 작업이 끝났으므로 플래그를 해제합니다.
            self._uploading = False
            ok, err = result
            if ok:
                self.lbl_sync.setText("") # 성공 시 라벨 비움
            elif err:
                # 조용히 실패 로그만 남기고, UX는 유지
                try:
                    import logging
                    logging.exception("[DisputeChat] silent upload failed: %s", err)
                except Exception:
                    pass
                self.lbl_sync.setText("동기화 대기중…")

        self._run_silent(_do, _done)

    def _run_silent(self, work_fn, done_fn):
        """
        dialogs.py 내부 전용: 조용히(로딩창 없이) 백그라운드 작업 실행
        - PyInstaller 환경에서도 깨지지 않도록 외부 SilentWorker 의존 제거
        - Qt 이벤트 루프를 통해 done_fn은 항상 메인스레드에서 호출
        """
        thread = QtCore.QThread(self)

        worker = _SilentWorker(lambda: work_fn(), parent=None)
        worker.moveToThread(thread)

        def _finish(ok: bool, err: str):
            try:
                # work_fn이 (ok, err) 튜플을 반환하는 형태도 있으니 그대로 전달
                # _SilentWorker는 bool(fn())만 보는데, 여기서는 work_fn을 래핑해서 예외만 잡는다.
                pass
            finally:
                thread.quit()
                thread.wait(1500)
                worker.deleteLater()
                thread.deleteLater()

        def _on_started():
            # work_fn 결과를 그대로 done_fn으로 전달
            try:
                res = work_fn()
            except Exception as e:
                res = (False, str(e))
            QtCore.QTimer.singleShot(0, lambda: done_fn(res))
            _finish(True, "")

        thread.started.connect(_on_started)
        thread.start()

        # GC 방지
        if not hasattr(self, "_silent_threads"):
            self._silent_threads = []
        self._silent_threads.append(thread)

    def send_message(self):
        msg = self.le_input.text().strip()
        if not msg:
            return

        # ✅ [추가] 종료된 이의제기인지 체크하여 경고창 표시
        if self.current_status == "RESOLVED":
            QtWidgets.QMessageBox.warning(self, "알림", "완료된 이의제기 입니다.")
            return
        if self.current_status == "REJECTED":
            QtWidgets.QMessageBox.warning(self, "알림", "기각된 이의제기 입니다.")
            return

        # 1) 전송 시작 시 입력창 비움 및 버튼 즉시 비활성화
        self.le_input.clear()
        self.btn_send.setEnabled(False)

        # 2) UI 즉시 반영 (로컬 에코)
        self._append_local_echo(msg)

        # 3) 저장/업로드 백그라운드 처리 (Fetch-before-Write 로직 적용)
        def _work():
            try:
                # 전송 전 강제 병합 실행
                self.db.sync_dispute_thread_from_cloud(self.dispute_id)

                if self.my_role == "owner":
                    new_status = self.cb_status.currentData() if self.cb_status else self.current_status
                    self.db.resolve_dispute(self.dispute_id, self.user_id, new_status, msg)
                    return True, new_status
                else:
                    self.db.add_dispute_message(
                        self.dispute_id,
                        sender_user_id=self.user_id,
                        sender_role="worker",
                        message=msg
                    )

                # 최신 DB 업로드
                from timeclock import sync_manager
                ok_up = sync_manager.upload_current_db()

                return True, ok_up
            except Exception as e:
                return False, str(e)

        def _done(res):
            try:
                ok = bool(res[0]) if isinstance(res, (tuple, list)) and len(res) >= 1 else False
                if ok:
                    self.refresh_timeline()
                else:
                    err = res[1] if len(res) > 1 else "Unknown error"
                    QtWidgets.QMessageBox.critical(self, "오류", f"전송 실패: {err}")
                    self.le_input.setText(msg)

            finally:
                # settings.py의 _MIN_CALL_INTERVAL_SEC(1.0초) 기반으로 버튼 재활성화
                from timeclock.settings import _MIN_CALL_INTERVAL_SEC
                lock_ms = int(_MIN_CALL_INTERVAL_SEC * 1000) + 100
                QtCore.QTimer.singleShot(lock_ms, lambda: self.btn_send.setEnabled(True))

        self._run_silent(_work, _done)

    def _silent_poll_refresh(self):
        """
        2초마다 호출:
        - 클라우드 스냅샷을 받아서 로컬에 병합
        - 변경이 있으면 refresh_timeline() 호출하여 배너 및 차단 상태 업데이트
        """
        try:
            if self.le_input.hasFocus() or (self.le_input.text().strip() != ""):
                return
        except Exception:
            pass

        if getattr(self, "_sync_in_progress", False):
            return

        self._sync_in_progress = True

        def _work():
            try:
                # DB에서 원격 데이터를 병합합니다.
                changed = self.db.sync_dispute_thread_from_cloud(self.dispute_id)
                return True, bool(changed)
            except Exception as e:
                return False, str(e)

        def _done(res):
            try:
                ok = bool(res[0]) if isinstance(res, (tuple, list)) and len(res) >= 1 else False
                payload = res[1] if isinstance(res, (tuple, list)) and len(res) >= 2 else None

                if ok and payload:
                    # ✅ 데이터 변경이 있다면 타임라인을 새로 고침하여 종료 배너 등을 표시합니다.
                    self.refresh_timeline()
            finally:
                self._sync_in_progress = False

        self._run_silent(_work, _done)

    def _merge_remote_messages_from_temp_db(self, temp_db_path: str) -> int:
        """
        temp DB에서 dispute_messages를 읽어서,
        로컬 DB의 dispute_messages에 INSERT OR IGNORE로 병합한다.

        return: 병합된(삽입 시도된) row 수(대략치)
        """
        import sqlite3

        if not temp_db_path or not os.path.exists(temp_db_path):
            return 0

        # 로컬 커넥션이 없으면 불가
        if getattr(self.db, "conn", None) is None:
            return 0

        # 로컬 최신 created_at을 기준으로 필터(없으면 전체)
        try:
            cur = self.db.conn.execute(
                "SELECT COALESCE(MAX(created_at), '') FROM dispute_messages WHERE dispute_id=?",
                (self.dispute_id,)
            )
            local_max_at = cur.fetchone()[0] or ""
        except Exception:
            local_max_at = ""

        remote = sqlite3.connect(temp_db_path)
        remote.row_factory = sqlite3.Row

        inserted = 0
        try:
            # 원격에서 해당 dispute_id 메시지 가져오기
            # created_at이 local_max_at보다 큰 것만 우선(빠름)
            if local_max_at:
                rows = remote.execute(
                    """
                    SELECT id, dispute_id, sender_user_id, sender_role, message, created_at, status_code
                    FROM dispute_messages
                    WHERE dispute_id=? AND created_at > ?
                    ORDER BY created_at ASC
                    """,
                    (self.dispute_id, local_max_at)
                ).fetchall()
            else:
                rows = remote.execute(
                    """
                    SELECT id, dispute_id, sender_user_id, sender_role, message, created_at, status_code
                    FROM dispute_messages
                    WHERE dispute_id=?
                    ORDER BY created_at ASC
                    """,
                    (self.dispute_id,)
                ).fetchall()

            if not rows:
                return 0

            # 로컬에 병합
            # (PK 충돌/중복은 IGNORE)
            for r in rows:
                try:
                    self.db.conn.execute(
                        """
                        INSERT OR IGNORE INTO dispute_messages
                        (id, dispute_id, sender_user_id, sender_role, message, created_at, status_code)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            r["id"], r["dispute_id"], r["sender_user_id"], r["sender_role"],
                            r["message"], r["created_at"], r["status_code"]
                        )
                    )
                    inserted += 1
                except Exception:
                    # 한 줄 실패해도 전체는 계속
                    continue

            self.db.conn.commit()
            return inserted

        finally:
            try:
                remote.close()
            except Exception:
                pass

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
        """
        이의제기 대화 타임라인 렌더링 (로컬 DB 기반)
        - 상태가 완료/기각이면 하단에 안내 문구를 표시하고 입력을 차단합니다.
        """
        # 최신 상태(current_status) 반영을 위해 데이터 재로드
        self._load_data()

        # 1) 타임라인 로드 (로컬)
        try:
            events = self.db.get_dispute_timeline(self.dispute_id) or []
        except Exception:
            return

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
            ts = str(ts)
            return ts[:10]

        def time_only(ts: str) -> str:
            if not ts:
                return ""
            ts = str(ts)
            if len(ts) >= 16 and " " in ts:
                return ts[11:16]
            return ts

        BG = "#B2C7D9"
        MY = "#FEE500"
        OTHER = "#FFFFFF"
        TIME = "#666666"

        html = f"""
        <html><head><meta charset="utf-8"></head>
        <body style="margin:0; padding:0;">
          <div style="background:{BG}; padding:12px; font-family:'Malgun Gothic'; font-size:13px;">
        """

        last_date = None

        for ev in events:
            who = (ev.get("who") or "").strip()
            username = (ev.get("username") or who or "").strip()
            msg = (ev.get("comment") or "").strip()
            ts = (ev.get("at") or "").strip()

            if not msg and not ts and not username:
                continue

            d = date_only(ts)
            if d and d != last_date:
                last_date = d
                html += f"""
                <div style="text-align:center; margin:10px 0;">
                  <span style="background:rgba(0,0,0,0.18); color:#fff; padding:4px 10px; border-radius:12px; font-size:12px;">
                    {esc(d)}
                  </span>
                </div>
                """

            if self.my_role == "owner":
                is_me = (who == "owner")
            else:
                is_me = (who == "worker")

            name_disp = username or who
            t_disp = time_only(ts)

            bubble_bg = MY if is_me else OTHER
            align = "right" if is_me else "left"

            html += f"""
            <table width="100%" cellspacing="0" cellpadding="0" style="margin:6px 0;">
              <tr>
                <td align="{align}" valign="bottom">
                  <div style="margin:0; padding:0;">
                    <div style="font-size:12px; color:#222; margin:0 0 2px 2px; text-align:{align};">
                      {esc(name_disp)}
                    </div>

                    <table cellspacing="0" cellpadding="8" style="display:inline-table; max-width:72%;">
                      <tr>
                        <td bgcolor="{bubble_bg}">
                          <span style="font-size:13px; line-height:1.45;">
                            {esc(msg)}
                          </span>
                        </td>
                      </tr>
                    </table>

                    <div style="font-size:11px; color:{TIME}; margin-top:2px; text-align:{align};">
                      {esc(t_disp)}
                    </div>
                  </div>
                </td>
              </tr>
            </table>
            """

        # ✅ [복구] 상태가 완료/기각인 경우 하단 중앙에 안내 배너 추가
        if self.current_status in ["RESOLVED", "REJECTED"]:
            status_text = "처리 완료된 이의제기입니다." if self.current_status == "RESOLVED" else "기각된 이의제기입니다."
            html += f"""
            <div style="text-align:center; margin:20px 0;">
              <span style="background:#f0f0f0; color:#555; padding:6px 15px; border-radius:15px; font-size:12px; border:1px solid #ddd; font-weight:bold;">
                {status_text}
              </span>
            </div>
            """
            # 근로자라면 입력창과 버튼을 완전히 비활성화
            if self.my_role == "worker":
                self.le_input.setEnabled(False)
                self.le_input.setPlaceholderText("종료된 대화입니다.")
                self.btn_send.setEnabled(False)

        html += """
          </div>
        </body></html>
        """

        self.browser.setHtml(html)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        """
        QTextBrowser는 setHtml 직후 스크롤바 최대값이 늦게 반영되는 경우가 많아
        QTimer.singleShot(0)로 한 템포 늦춰 하단 고정한다.
        """

        def _do():
            try:
                sb = self.browser.verticalScrollBar()
                sb.setValue(sb.maximum())
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, _do)

    def _append_local_echo(self, msg: str):
        """
        전송 버튼 누른 즉시(서버/DB 기다리지 않고) 화면에 말풍선을 추가한다.
        - 나중에 백그라운드 저장/업로드가 끝나면 refresh_timeline()로 정합성을 맞춘다.
        """
        try:
            msg = (msg or "").strip()
            if not msg:
                return

            # 시간 표시(간단)
            t_disp = QtCore.QDateTime.currentDateTime().toString("HH:mm")

            # 내/상대 말풍선 색상
            MY = "#FEE500"
            OTHER = "#FFFFFF"
            TIME = "#666666"

            is_me = True  # 이 함수는 "내가 보낸 것"의 즉시 반영용

            bubble_bg = MY if is_me else OTHER
            align = "right" if is_me else "left"

            def esc(s: str) -> str:
                s = "" if s is None else str(s)
                s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                s = s.replace("\n", "<br>")
                return s

            html = f"""
            <table width="100%" cellspacing="0" cellpadding="0" style="margin:6px 0;">
              <tr>
                <td align="{align}" valign="bottom">
                  <div style="margin:0; padding:0;">
                    <table cellspacing="0" cellpadding="10" style="display:inline-table; max-width:72%; border-radius:16px;">
                      <tr>
                        <td bgcolor="{bubble_bg}" style="border-radius:16px;">
                          <span style="font-size:13px; line-height:1.45;">
                            {esc(msg)}
                          </span>
                        </td>
                      </tr>
                    </table>

                    <div style="font-size:11px; color:{TIME}; margin-top:2px; text-align:{align};">
                      {esc(t_disp)}
                    </div>
                  </div>
                </td>
              </tr>
            </table>
            """

            # QTextBrowser 끝에 append (setHtml을 다시 하지 않음)
            cur = self.browser.textCursor()
            cur.movePosition(cur.End)
            cur.insertHtml(html)
            cur.insertBlock()
            self.browser.setTextCursor(cur)

            self._scroll_to_bottom()

        except Exception:
            # 즉시 반영 실패는 UI를 깨지 않도록 조용히 무시
            pass


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
    """아이디 제외 개인 정보 및 비밀번호 변경."""
    saved = QtCore.pyqtSignal()

    def __init__(self, db, user_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.user_id = user_id

        self.setWindowTitle("개인정보 변경")
        self.setModal(True)
        self.resize(460, 500)  # 높이를 조금 더 키움

        u = None
        try:
            u = self.db.get_user_by_id(user_id)
        except Exception:
            u = None

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)

        title = QtWidgets.QLabel("개인정보 및 비밀번호 변경")
        title.setStyleSheet("font-size:18px; font-weight:800; color:#222;")
        v.addWidget(title)

        sub = QtWidgets.QLabel("아이디는 변경할 수 없습니다. 비밀번호 입력 시에만 변경 처리됩니다.")
        sub.setStyleSheet("font-size:12px; color:#666;")
        v.addWidget(sub)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        def mk_le(placeholder: str, is_pw=False):
            le = QtWidgets.QLineEdit()
            le.setPlaceholderText(placeholder)
            if is_pw:
                le.setEchoMode(QtWidgets.QLineEdit.Password)
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

        # ✅ 비밀번호 입력 필드 추가
        self.le_pw = mk_le("새 비밀번호 (6자 이상)", is_pw=True)
        self.le_pw2 = mk_le("새 비밀번호 확인", is_pw=True)

        if u:
            self.le_username.setText(str(u.get("username", "") or ""))
            self.le_name.setText(str(u.get("name", "") or ""))
            self.le_phone.setText(str(u.get("phone", "") or ""))
            self.le_birth.setText(str(u.get("birthdate", "") or ""))

        form.addRow("아이디", self.le_username)
        form.addRow("이름", self.le_name)
        form.addRow("연락처", self.le_phone)
        form.addRow("생년월일", self.le_birth)
        form.addRow("새 비밀번호", self.le_pw)
        form.addRow("비밀번호 확인", self.le_pw2)

        v.addLayout(form)
        v.addStretch()

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch()

        self.btn_cancel = QtWidgets.QPushButton("닫기")
        self.btn_save = QtWidgets.QPushButton("저장")

        for b in (self.btn_cancel, self.btn_save):
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

        # ✅ 비밀번호 변경 로직
        new_pw = self.le_pw.text()
        new_pw2 = self.le_pw2.text()

        if birth and not QtCore.QRegExp(r"^\d{4}-\d{2}-\d{2}$").exactMatch(birth):
            QtWidgets.QMessageBox.warning(self, "형식 오류", "생년월일은 YYYY-MM-DD 형식으로 입력해 주세요.")
            return

        if new_pw or new_pw2:
            if len(new_pw) < 6:
                QtWidgets.QMessageBox.warning(self, "비밀번호 변경", "새 비밀번호는 6자 이상이어야 합니다.")
                return
            if new_pw != new_pw2:
                QtWidgets.QMessageBox.warning(self, "비밀번호 변경", "새 비밀번호가 서로 일치하지 않습니다.")
                return

        try:
            # 1. 개인정보 업데이트
            self.db.update_user_profile(self.user_id, name=name, phone=phone, birthdate=birth)

            # 2. 비밀번호 업데이트 (입력된 경우에만)
            if new_pw:
                self.db.change_password(self.user_id, new_pw)

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

        # timeclock/ui/dialogs.py 내 PersonalInfoDialog._on_save 함수 전체

        def _on_save(self):
            # 1. 입력값 검증 (UI 스레드에서 즉시 수행)
            cur_pw = (self.ed_cur_pw.text() or "").strip()
            if not cur_pw:
                Message.err(self, "확인", "현재 비밀번호를 입력해주세요.")
                return

            # 비밀번호 확인 로직
            if not self.db.verify_user_password(self.user_id, cur_pw):
                Message.err(self, "확인", "현재 비밀번호가 올바르지 않습니다.")
                return

            # 새 비밀번호 검증
            new_pw = (self.ed_new_pw.text() or "")
            new_pw2 = (self.ed_new_pw2.text() or "")
            if new_pw or new_pw2:
                if len(new_pw) < 6:
                    Message.err(self, "비밀번호 변경", "새 비밀번호는 6자 이상이어야 합니다.")
                    return
                if new_pw != new_pw2:
                    Message.err(self, "비밀번호 변경", "새 비밀번호가 서로 일치하지 않습니다.")
                    return

            # 저장할 데이터 정리
            name = (self.ed_name.text() or "").strip()
            phone = (self.ed_phone.text() or "").strip()
            birth = (self.ed_birth.text() or "").strip()
            email = (self.ed_email.text() or "").strip()
            account = (self.ed_account.text() or "").strip()
            address = (self.ed_address.text() or "").strip()

            # ✅ 2. 비동기 작업 정의 (Fetch -> Write -> Push)
            def job_fn(progress_callback):
                try:
                    # DB 잠금 방지를 위해 연결 해제 후 최신본 다운로드
                    progress_callback({"msg": "☁️ 서버 데이터와 대조 중..."})
                    self.db.close_connection()
                    from timeclock import sync_manager
                    sync_manager.download_latest_db()
                    self.db.reconnect()

                    # 데이터 업데이트
                    progress_callback({"msg": "💾 개인정보를 저장하는 중..."})
                    self.db.update_user_profile(
                        self.user_id,
                        name=name or None,
                        phone=phone or None,
                        birthdate=birth or None,
                        email=email or None,
                        account=account or None,
                        address=address or None,
                    )

                    # 비밀번호 변경이 있는 경우
                    if new_pw:
                        progress_callback({"msg": "🔐 비밀번호 보안 업데이트 중..."})
                        self.db.change_password(self.user_id, new_pw)

                    # 서버 업로드
                    progress_callback({"msg": "🚀 변경 사항을 서버에 반영 중..."})
                    ok_up = sync_manager.upload_current_db()
                    return ok_up, None
                except Exception as e:
                    return False, str(e)

            # 3. 작업 완료 후 콜백
            def on_done(ok, res, err):
                if ok:
                    Message.info(self, "완료", "개인정보와 비밀번호가 안전하게 변경되었습니다.")
                    self.accept()
                else:
                    error_msg = res if isinstance(res, str) else err
                    Message.err(self, "저장 실패", f"오류가 발생했습니다: {error_msg}")

            # ✅ 4. 비동기 실행 (로딩창 표시)
            run_job_with_progress_async(
                self,
                "개인정보 변경 처리",
                job_fn,
                on_done=on_done
            )

