# timeclock/ui/worker_page.py
# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets, QtCore
from datetime import datetime
from timeclock.salary import SalaryCalculator
from timeclock import backup_manager

from timeclock.utils import Message
from timeclock.settings import WORK_STATUS
from ui.widgets import DateRangeBar, Table
from ui.dialogs import DisputeTimelineDialog, DateRangeDialog


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
        header_layout.addWidget(self.btn_logout)

        # 메인 액션 버튼 (출퇴근 전용)
        self.btn_action = QtWidgets.QPushButton("작업 시작")
        self.btn_action.setFixedHeight(60)
        self.btn_action.setCursor(QtCore.Qt.PointingHandCursor)

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
        self.btn_refresh.clicked.connect(self.refresh)

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



    def on_work_action(self):
        mode = self.btn_action.property("mode")
        try:
            if mode == "IN":
                # 커스텀 알림창 생성
                msg_box = QtWidgets.QMessageBox(self)
                msg_box.setWindowTitle("작업 시작 확인")
                msg_box.setIcon(QtWidgets.QMessageBox.Warning)
                msg_box.setText("반드시 작업 시작시 작업 시작 요청을 해야합니다.\n\n작업 준비 시간은 실제 근무시간에 포함되지 않습니다.")

                # 버튼 추가 (이해했습니다 / 준비하러갈게요)
                btn_yes = msg_box.addButton("이해했습니다", QtWidgets.QMessageBox.YesRole)
                btn_no = msg_box.addButton("준비하러갈게요", QtWidgets.QMessageBox.NoRole)

                msg_box.exec_()

                if msg_box.clickedButton() == btn_yes:
                    # DB에 시작 요청 기록 (PENDING 상태)
                    self.db.start_work(self.session.user_id)
                    backup_manager.run_backup("request_in")
                    Message.info(self, "요청 완료", "관리자에게 승인 요청을 보냈습니다.")
                else:
                    # 취소 시 아무 동작 안함
                    return

            elif mode == "OUT":
                # 퇴근 요청 (추가 확인 없이 바로, 혹은 간단한 확인 후)
                if Message.confirm(self, "퇴근 요청", "작업을 모두 마치고 퇴근 승인을 요청하시겠습니까?"):
                    self.db.end_work(self.session.user_id)
                    backup_manager.run_backup("request_out")

                    # 3초 후 자동 닫히는 알림창
                    auto_close_dlg = QtWidgets.QMessageBox(self)
                    auto_close_dlg.setWindowTitle("퇴근")
                    auto_close_dlg.setText("수고하셨습니다.")
                    auto_close_dlg.setStandardButtons(QtWidgets.QMessageBox.NoButton)  # 버튼 없음

                    # 3초(3000ms) 뒤에 자동으로 닫힘
                    QtCore.QTimer.singleShot(3000, auto_close_dlg.accept)
                    auto_close_dlg.exec_()

            self.refresh()
            self._update_action_button()
        except Exception as e:
            Message.err(self, "오류", str(e))

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
        row = self.dispute_table.selected_first_row_index()

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
            self.refresh_my_disputes()
            return

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
                    dispute_id = self.db.create_dispute(work_log_id, self.session.user_id, item, text)
                    dlg = DisputeTimelineDialog(
                        parent=self,
                        db=self.db,
                        user_id=self.session.user_id,
                        dispute_id=dispute_id,
                        my_role="worker"
                    )
                    dlg.exec_()
                    self.refresh_my_disputes()
            return

        Message.warn(self, "알림", "이의 제기 내역 또는 근무 기록을 먼저 선택해주세요.")

    def calculate_my_salary(self):
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