# timeclock/ui/worker_page.py
# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets, QtCore
from datetime import datetime
from timeclock.salary import SalaryCalculator
from timeclock import backup_manager
from ui.async_helper import run_job_with_progress_async

from timeclock.utils import Message
from timeclock.settings import WORK_STATUS
from ui.widgets import DateRangeBar, Table
from ui.dialogs import DisputeTimelineDialog, DateRangeDialog, ConfirmPasswordDialog, ProfileEditDialog
from ui.dialogs import PersonalInfoDialog
from timeclock import sync_manager  # [추가] 동기화 모듈 임포트


class WorkerPage(QtWidgets.QWidget):
    logout_requested = QtCore.pyqtSignal()

    def __init__(self, db, session, parent=None):
        super().__init__(parent)
        self.db = db
        self.session = session
        self._my_dispute_rows = []
        self.setStyleSheet("background-color: #fcfaf5;")

        # 상단 헤더 패널
        header_card = QtWidgets.QFrame()
        header_card.setStyleSheet("background-color: white; border-radius: 15px; border: 1px solid #eee;")
        header_layout = QtWidgets.QHBoxLayout(header_card)
        header_layout.setContentsMargins(25, 20, 25, 20)

        title_info = QtWidgets.QVBoxLayout()
        header_title = QtWidgets.QLabel("HobbyBrown")
        header_title.setStyleSheet("font-family: 'Arial Rounded MT Bold'; font-size: 22px; color: #5d4037;")
        user_label = QtWidgets.QLabel(f"{session.username} 근로자님, 오늘도 힘찬 하루 되세요!")
        user_label.setStyleSheet("font-size: 13px; color: #888;")
        title_info.addWidget(header_title)
        title_info.addWidget(user_label)
        header_layout.addLayout(title_info)

        header_layout.addStretch()

        self.btn_logout = QtWidgets.QPushButton("로그아웃")
        self.btn_logout.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_logout.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f5; border-radius: 8px; padding: 8px 15px; color: #666;
            }
            QPushButton:hover { background-color: #eee; }
        """)
        self.btn_logout.clicked.connect(self.logout_requested.emit)

        self.btn_profile = QtWidgets.QPushButton("개인정보 변경")
        self.btn_profile.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_profile.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f5; border-radius: 8px; padding: 8px 15px;
                border: 1px solid #ddd; font-size: 13px;
            }
            QPushButton:hover { background-color: #eee; }
        """)
        self.btn_profile.clicked.connect(
            self.open_profile_settings)  # 메서드 이름 수정(open_personal_info -> open_profile_settings 연결 통일)

        header_layout.addWidget(self.btn_profile)
        header_layout.addSpacing(8)
        header_layout.addWidget(self.btn_logout)

        # 메인 액션 버튼 (출퇴근 전용)
        self.btn_action = QtWidgets.QPushButton("작업 시작")
        self.btn_action.setFixedHeight(60)
        self.btn_action.setCursor(QtCore.Qt.PointingHandCursor)

        self.btn_action.clicked.connect(self.on_work_action)

        # 중간 컨트롤 바
        ctrl_layout = QtWidgets.QHBoxLayout()
        self.filter = DateRangeBar(label="조회기간")
        self.filter.applied.connect(lambda *_: self.refresh())

        self.btn_calc = QtWidgets.QPushButton("급여 조회")
        self.btn_calc.setStyleSheet("""
            QPushButton {
                background-color: #fff3e0; color: #e65100; font-weight: bold;
                border-radius: 8px; padding: 5px 15px; border: 1px solid #ffe0b2;
            }
            QPushButton:hover { background-color: #ffe0b2; }
        """)
        self.btn_calc.clicked.connect(self.calculate_my_salary)

        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        # self.btn_refresh.clicked.connect(self.refresh)
        self.btn_refresh.clicked.connect(self.sync_and_refresh)

        ctrl_layout.addWidget(self.filter)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.btn_calc)
        ctrl_layout.addSpacing(10)
        ctrl_layout.addWidget(self.btn_refresh)

        # 테이블 스타일은 widgets.py에서 이미 정의됨
        self.work_table = Table([
            "ID", "일자", "작업시작(요청)", "퇴근(요청)", "상태",
            "확정 시작", "확정 종료", "관리자 승인/비고"
        ])
        self.work_table.setColumnWidth(0, 0)

        # 전체 배치
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)

        layout.addWidget(header_card)
        layout.addWidget(self.btn_action)
        layout.addLayout(ctrl_layout)
        layout.addWidget(self.work_table)

        # 하단 이의제기 영역 (요약)
        layout.addSpacing(10)
        layout.addWidget(QtWidgets.QLabel("<b>[이의 제기 내역]</b>"))

        disp_ctrl = QtWidgets.QHBoxLayout()
        self.filter_disputes = DateRangeBar(label="기간")
        self.filter_disputes.applied.connect(lambda *_: self.refresh_my_disputes())
        self.cb_dispute_filter = QtWidgets.QComboBox()
        self.cb_dispute_filter.addItem("진행 중", "ACTIVE")
        self.cb_dispute_filter.addItem("종료", "CLOSED")

        disp_ctrl.addWidget(self.filter_disputes)
        disp_ctrl.addWidget(self.cb_dispute_filter)
        disp_ctrl.addStretch()

        layout.addLayout(disp_ctrl)
        self.dispute_table = Table(["이의ID", "일자", "유형", "상태", "메시지", "시각"])
        self.dispute_table.setColumnWidth(0, 0)
        layout.addWidget(self.dispute_table)

        self.btn_open_chat = QtWidgets.QPushButton("선택 건 대화방 열기")
        self.btn_open_chat.setFixedHeight(40)
        self.btn_open_chat.setStyleSheet(
            "background-color: #fef01b; color: #3c1e1e; font-weight: bold; border-radius: 8px;")
        self.btn_open_chat.clicked.connect(self.open_dispute_chat)
        layout.addWidget(self.btn_open_chat)

        self.refresh()
        self.refresh_my_disputes()
        self._update_action_button()
        QtCore.QTimer.singleShot(0, self._wire_double_click)

    def _update_action_button(self):
        today_log = self.db.get_today_work_log(self.session.user_id)

        # 버튼 공통 기본 스타일
        style_base = "border-radius: 15px; font-size: 18px; font-weight: bold; color: white; border: none"

        if not today_log or today_log["status"] == "REJECTED":
            self.btn_action.setText("오늘의 작업 시작 요청")
            self.btn_action.setStyleSheet(f"{style_base}; background-color: #6d4c41")
            self.btn_action.setProperty("mode", "IN")
            self.btn_action.setEnabled(True)

        elif today_log["status"] == "PENDING":
            self.btn_action.setText("출근 승인 대기 중...")
            self.btn_action.setStyleSheet(f"{style_base}; background-color: #d7ccc8; color: #8d6e63")
            self.btn_action.setProperty("mode", "WAIT")
            self.btn_action.setEnabled(False)

        elif today_log["status"] == "WORKING":
            self.btn_action.setText("오늘의 작업 종료 (퇴근 요청)")
            self.btn_action.setStyleSheet(f"{style_base}; background-color: #a1887f")
            self.btn_action.setProperty("mode", "OUT")
            self.btn_action.setEnabled(True)

        else:
            self.btn_action.setText("오늘의 업무가 모두 종료되었습니다")
            self.btn_action.setStyleSheet(f"{style_base}; background-color: #eee; color: #bbb")
            self.btn_action.setProperty("mode", "DONE")
            self.btn_action.setEnabled(False)

    # timeclock/ui/worker_page.py

    def on_work_action(self):
        mode = self.btn_action.property("mode")

        # [1] 출근 요청 (IN)
        if mode == "IN":
            # 커스텀 알림창 생성
            msg_box = QtWidgets.QMessageBox(self)
            msg_box.setWindowTitle("작업 시작 확인")
            msg_box.setIcon(QtWidgets.QMessageBox.Warning)
            msg_box.setText("반드시 작업 시작시 작업 시작 요청을 해야합니다.\n\n작업 준비 시간은 실제 근무시간에 포함되지 않습니다.")

            btn_yes = msg_box.addButton("이해했습니다", QtWidgets.QMessageBox.YesRole)
            btn_no = msg_box.addButton("준비하러갈게요", QtWidgets.QMessageBox.NoRole)

            msg_box.exec_()

            if msg_box.clickedButton() == btn_yes:

                # 1. [다운로드] 작업 전 최신 DB 가져오기 (충돌 방지)
                self.db.close_connection()
                try:
                    sync_manager.download_latest_db()
                finally:
                    self.db.reconnect()

                # 2. [DB 쓰기] 시작 요청 기록
                try:
                    self.db.start_work(self.session.user_id)
                except Exception as e:
                    Message.err(self, "오류", str(e))
                    return

                # 3. [저장 및 업로드] 공통 함수 한 줄로 해결!
                # (알아서 연결 끊고 -> 업로드 -> 다시 연결해줍니다)
                # -----------------------------------------------------
                # ❌ [삭제] self.db._save_and_sync("request_in")
                # -----------------------------------------------------

                # 4. 완료 알림 및 갱신
                Message.info(self, "요청 완료", "관리자에게 출근 요청을 보냈습니다.")
                self.refresh()
                self._update_action_button()
            else:
                return

        # [2] 퇴근 요청 (OUT)
        elif mode == "OUT":
            if Message.confirm(self, "퇴근 요청", "작업을 모두 마치고 퇴근 승인을 요청하시겠습니까?"):

                # 1. [다운로드]
                self.db.close_connection()
                try:
                    sync_manager.download_latest_db()
                finally:
                    self.db.reconnect()

                # 2. [DB 쓰기] 퇴근 기록
                try:
                    self.db.end_work(self.session.user_id)
                except Exception as e:
                    Message.err(self, "오류", str(e))
                    return

                # 3. [저장 및 업로드] 공통 함수 사용!
                # -----------------------------------------------------
                # ❌ [삭제] self.db._save_and_sync("request_out")
                # -----------------------------------------------------

                # 4. 완료 알림
                auto_close_dlg = QtWidgets.QMessageBox(self)
                auto_close_dlg.setWindowTitle("퇴근")
                auto_close_dlg.setText("수고하셨습니다. (서버 전송 완료)")
                auto_close_dlg.setStandardButtons(QtWidgets.QMessageBox.NoButton)
                QtCore.QTimer.singleShot(2000, auto_close_dlg.accept)
                auto_close_dlg.exec_()

                self.refresh()
                self._update_action_button()

        # [3] 그 외 (이미 퇴근함 등)
        else:
            self.refresh()
            self._update_action_button()

    def sync_and_refresh(self):
        """
        [새로고침 버튼] DB 연결 해제 -> 최신 파일 다운로드 -> DB 재연결 -> 화면 갱신
        """
        print("🔄 근로자 데이터 동기화 시작...")

        # 1. DB 연결 잠시 해제 (파일 잠금 방지)
        self.db.close_connection()

        def job_fn(progress_callback):
            progress_callback({"msg": "☁️ 최신 데이터 가져오는 중..."})
            ok, msg = sync_manager.download_latest_db()
            return ok, msg

        def on_done(ok, res, err):
            # 2. 작업 후 DB 재연결
            print("🔌 DB 재연결...")
            self.db.reconnect()

            if ok:
                # 3. 화면 갱신
                self.refresh()
                self.refresh_my_disputes()
                self._update_action_button()
                # (성공 시 조용히 갱신만 하거나, 필요하면 메시지 띄우기)
            else:
                QtWidgets.QMessageBox.warning(self, "동기화 실패", f"최신 데이터를 가져오지 못했습니다.\n{res}")

        # 비동기 실행
        run_job_with_progress_async(
            self,
            "동기화 중...",
            job_fn,
            on_done=on_done
        )

    def refresh(self):
        d1, d2 = self.filter.get_range()
        rows = self.db.list_work_logs(self.session.user_id, d1, d2)

        out = []
        for r in rows:
            rr = dict(r)
            st = rr["status"]
            status_str = WORK_STATUS.get(st, st)

            out.append([
                str(rr["id"]),
                rr["work_date"],
                rr["start_time"] or "",
                rr["end_time"] or "",
                status_str,
                rr["approved_start"] or "",
                rr["approved_end"] or "",
                rr["owner_comment"] or ""
            ])

        self.work_table.set_rows(out)
        self._update_action_button()

    def refresh_my_disputes(self):
        d1, d2 = self.filter_disputes.get_range()
        filter_type = self.cb_dispute_filter.currentData()

        rows = self.db.list_my_disputes(self.session.user_id, d1, d2, filter_type)
        self._my_dispute_rows = rows

        out = []
        for r in rows:
            rr = dict(r)
            d_st = rr["status"]
            st_map = {"PENDING": "미처리", "IN_REVIEW": "검토중", "RESOLVED": "완료", "REJECTED": "기각"}
            d_st_str = st_map.get(d_st, d_st)

            summary = (rr["comment"] or "").replace("\n", " ")
            if len(summary) > 30: summary = summary[:30] + "..."

            out.append([
                str(rr["id"]),
                rr["work_date"],
                rr["dispute_type"],
                d_st_str,
                summary,
                rr["created_at"]
            ])

        self.dispute_table.set_rows(out)

    def _wire_double_click(self):
        try:
            self.dispute_table.itemDoubleClicked.disconnect()
        except:
            pass
        self.dispute_table.itemDoubleClicked.connect(self.open_dispute_chat_by_item)

    def open_dispute_chat_by_item(self, item):
        self.open_dispute_chat()

    def open_dispute_chat(self):
        # [Sync] 대화방 열기 전 최신 DB 받기 (기본 조회용)
        # (주의: 아래에서 Create 할 때 한 번 더 받게 됨, 안전을 위해 유지)
        self.db.close_connection()
        try:
            sync_manager.download_latest_db()
        except Exception:
            pass
        finally:
            self.db.reconnect()

        row = self.dispute_table.selected_first_row_index()
        dispute_id = None

        # 1. 기존 이의제기 선택 시
        if row >= 0 and row < len(self._my_dispute_rows):
            rr = dict(self._my_dispute_rows[row])
            dispute_id = int(rr["id"])

            dlg = DisputeTimelineDialog(
                parent=self,
                db=self.db,
                user_id=self.session.user_id,
                dispute_id=dispute_id,
                my_role="worker"
            )
            dlg.exec_()

            # [Sync] 대화 종료 후 업로드 (대화방 안에서 메시지를 보냈을 수 있으므로)
            sync_manager.upload_current_db()
            self.refresh_my_disputes()
            return

        # 2. 근무 기록 선택하여 신규 이의제기 생성 시
        w_row = self.work_table.selected_first_row_index()
        if w_row >= 0:
            try:
                work_log_id_str = self.work_table.get_cell(w_row, 0)
                work_log_id = int(work_log_id_str)
            except:
                return

            items = ["출/퇴근 시간 정정 요청", "근무일자 오류", "기타 문의"]
            item, ok = QtWidgets.QInputDialog.getItem(self, "이의 제기", "문의 유형을 선택하세요:", items, 0, False)
            if ok and item:
                text, ok2 = QtWidgets.QInputDialog.getText(self, "이의 제기", "첫 메시지를 입력하세요:")
                if ok2 and text:

                    # [Sync] 1. 신규 생성(Insert) 전 최신 DB 다운로드
                    self.db.close_connection()
                    try:
                        sync_manager.download_latest_db()
                    except Exception as e:
                        print(f"[Sync Error] {e}")
                    finally:
                        self.db.reconnect()

                    # DB Insert
                    dispute_id = self.db.create_dispute(work_log_id, self.session.user_id, item, text)

                    # [Sync] 2. 생성 직후 업로드
                    sync_manager.upload_current_db()

                    dlg = DisputeTimelineDialog(
                        parent=self,
                        db=self.db,
                        user_id=self.session.user_id,
                        dispute_id=dispute_id,
                        my_role="worker"
                    )
                    dlg.exec_()

                    # 대화방 종료 후 최종 동기화
                    sync_manager.upload_current_db()
                    self.refresh_my_disputes()
            return

        Message.warn(self, "알림", "이의 제기 내역 또는 근무 기록을 먼저 선택해주세요.")

    def calculate_my_salary(self):
        # ... (생략 없이 기존 로직 유지) ...
        # 여기는 조회 기능이라 동기화 불필요
        user_info = self.db.get_user_by_username(self.session.username)
        if not user_info:
            Message.err(self, "오류", "사용자 정보를 찾을 수 없습니다.")
            return

        hourly_wage = user_info.get('hourly_wage', 0)

        dlg = DateRangeDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        d1, d2 = dlg.get_range()

        today_str = datetime.now().strftime("%Y-%m-%d")
        first_day = datetime.now().replace(day=1).strftime("%Y-%m-%d")

        text, ok = QtWidgets.QInputDialog.getText(
            self, "급여 조회",
            "조회할 기간을 입력하세요 (YYYY-MM-DD ~ YYYY-MM-DD):",
            text=f"{first_day} ~ {today_str}"
        )

        if not ok: return

        try:
            d1_str, d2_str = text.split("~")
            d1 = d1_str.strip()
            d2 = d2_str.strip()
            datetime.strptime(d1, "%Y-%m-%d")
            datetime.strptime(d2, "%Y-%m-%d")
        except:
            Message.err(self, "오류", "날짜 형식이 올바르지 않습니다.")
            return

        logs = self.db.list_all_work_logs(self.session.user_id, d1, d2, status_filter='APPROVED')

        if not logs:
            Message.info(self, "조회 결과", "해당 기간에 확정(승인)된 근무 기록이 없습니다.\n(아직 승인 대기 중인 기록은 계산에 포함되지 않습니다.)")
            return

        log_dicts = [dict(r) for r in logs]
        calc = SalaryCalculator(wage_per_hour=hourly_wage)
        res = calc.calculate_period(log_dicts)

        if not res:
            Message.info(self, "결과", "계산할 데이터가 없습니다.")
            return

        final_pay = res['grand_total']

        details = res.get('ju_hyu_details', [])
        if details:
            detail_str = " + ".join([f"{x:,}" for x in details])
            ju_hyu_msg = f"주휴수당: {detail_str} = 총 {res['ju_hyu_pay']:,}원"
        else:
            ju_hyu_msg = f"주휴수당: {res['ju_hyu_pay']:,}원"

        msg = (
            f"[{d1} ~ {d2} 나의 급여 조회]\n\n"
            f"총 {res['total_hours']}시간을 일했으며, "
            f"휴게시간 {res['break_hours']}시간을 제외한 "
            f"실제 {res['actual_hours']}시간을 근무하였습니다.\n\n"
            f"• 기본급(시급 {hourly_wage:,}원): {res['base_pay']:,}원\n"
            f"• 가산수당(연장/야간): {res['overtime_pay']:,}원\n"
            f"• {ju_hyu_msg}\n\n"
            f"💰 총 지급액: {final_pay:,}원"
        )

        QtWidgets.QMessageBox.information(self, "예상 급여 내역", msg)

    def open_profile_settings(self):
        # 1. [다운로드] 변경 전 최신 정보 가져오기
        self.db.close_connection()
        try:
            sync_manager.download_latest_db()
        except Exception as e:
            print(f"[Sync Error] {e}")
        finally:
            self.db.reconnect()

        # 2. [다이얼로그 실행]
        # ProfileEditDialog 내부에서 'update_user_profile'을 호출하면
        # DB가 알아서 '저장+업로드'를 수행합니다.
        dlg = ConfirmPasswordDialog(self, title="개인정보 변경", message="개인정보 변경을 위해 현재 비밀번호를 다시 입력해 주세요.")
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        pw = dlg.password()
        try:
            ok = self.db.verify_user_password(self.session.user_id, pw)
        except Exception:
            ok = False

        if not ok:
            Message.warn(self, "실패", "비밀번호가 올바르지 않습니다.")
            return

        edit = ProfileEditDialog(self.db, self.session.user_id, parent=self)
        edit.exec_()

        # ❌ [삭제] sync_manager.upload_current_db() <-- 필요 없음! (중복)

    def open_personal_info(self):
        # 이것은 단순히 조회용 팝업이므로 동기화 불필요하거나,
        # 만약 여기서도 수정을 한다면 위와 같은 패턴 적용
        dlg = PersonalInfoDialog(self.db, self.session.user_id, self)
        dlg.exec_()