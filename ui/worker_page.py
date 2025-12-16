# timeclock/ui/worker_page.py
# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets, QtCore
# timeclock/ui/worker_page.py 상단

from datetime import datetime  # [추가]
from timeclock.salary import SalaryCalculator  # [추가]
from timeclock import backup_manager

from timeclock.utils import Message
from timeclock.settings import WORK_STATUS  # ★ [수정] 설정 파일에서 상태값 가져옴
from ui.widgets import DateRangeBar, Table
from ui.dialogs import DisputeTimelineDialog, DateRangeDialog


class WorkerPage(QtWidgets.QWidget):
    logout_requested = QtCore.pyqtSignal()

    def __init__(self, db, session, parent=None):
        super().__init__(parent)
        self.db = db
        self.session = session
        self._my_dispute_rows = []

        header = QtWidgets.QLabel(f"근로자 화면 - {session.username}")
        f = header.font()
        f.setPointSize(14)
        f.setBold(True)
        header.setFont(f)

        # ----------------------------------------------------
        # 1. 상단 컨트롤
        # ----------------------------------------------------
        self.filter = DateRangeBar(label="근무 조회기간")
        self.filter.applied.connect(lambda *_: self.refresh())

        self.btn_action = QtWidgets.QPushButton("출근하기")
        self.btn_action.setMinimumHeight(40)
        self.btn_action.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.btn_action.clicked.connect(self.on_work_action)

        self.btn_calc = QtWidgets.QPushButton("내 급여 조회")
        self.btn_calc.setStyleSheet("background-color: #fff3e0; color: #e65100; font-weight: bold;")
        self.btn_calc.clicked.connect(self.calculate_my_salary)

        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_refresh.clicked.connect(self.refresh)

        self.btn_logout = QtWidgets.QPushButton("로그아웃")
        self.btn_logout.clicked.connect(self.logout_requested.emit)

        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addWidget(self.btn_action)
        top_layout.addSpacing(10)
        top_layout.addWidget(self.btn_calc)  # [추가] 레이아웃에 버튼 넣기
        top_layout.addSpacing(10)
        top_layout.addWidget(self.btn_refresh)
        top_layout.addStretch(1)
        top_layout.addWidget(self.btn_logout)

        # ----------------------------------------------------
        # 2. 근무 기록 테이블
        # ----------------------------------------------------
        self.work_table = Table([
            "ID", "일자", "출근(요청)", "퇴근(요청)", "상태",
            "확정 출근", "확정 퇴근", "사업주 비고"
        ])
        self.work_table.setColumnWidth(0, 0)  # ID 숨김

        # ----------------------------------------------------
        # 3. 이의 제기
        # ----------------------------------------------------
        self.filter_disputes = DateRangeBar(label="이의제기 기간")
        self.filter_disputes.applied.connect(lambda *_: self.refresh_my_disputes())

        self.cb_dispute_filter = QtWidgets.QComboBox()
        self.cb_dispute_filter.addItem("진행 중 (검토/미처리)", "ACTIVE")
        self.cb_dispute_filter.addItem("종료 (완료/기각)", "CLOSED")
        self.cb_dispute_filter.currentIndexChanged.connect(lambda *_: self.refresh_my_disputes())

        self.btn_disp_refresh = QtWidgets.QPushButton("조회")
        self.btn_disp_refresh.clicked.connect(self.refresh_my_disputes)

        self.btn_open_chat = QtWidgets.QPushButton("선택 건 이의제기/채팅 열기")
        self.btn_open_chat.setMinimumHeight(35)
        self.btn_open_chat.setStyleSheet("background-color: #fef01b; color: #3c1e1e; font-weight: bold;")
        self.btn_open_chat.clicked.connect(self.open_dispute_chat)

        self.dispute_table = Table([
            "이의ID", "근무일자", "이의유형", "진행상태", "최근 메시지", "최근 시각"
        ])
        self.dispute_table.setColumnWidth(0, 0)  # ID 숨김

        QtCore.QTimer.singleShot(0, self._wire_double_click)

        disp_filter_layout = QtWidgets.QHBoxLayout()
        disp_filter_layout.addWidget(self.filter_disputes)
        disp_filter_layout.addWidget(self.cb_dispute_filter)
        disp_filter_layout.addWidget(self.btn_disp_refresh)
        disp_filter_layout.addStretch(1)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(header)
        layout.addLayout(top_layout)

        layout.addWidget(QtWidgets.QLabel("<b>[나의 근무 기록]</b>"))
        layout.addWidget(self.filter)
        layout.addWidget(self.work_table)

        layout.addSpacing(20)
        layout.addWidget(QtWidgets.QLabel("<b>[이의 제기 내역]</b>"))
        layout.addLayout(disp_filter_layout)
        layout.addWidget(self.dispute_table)
        layout.addWidget(self.btn_open_chat)

        self.setLayout(layout)

        self.refresh()
        self.refresh_my_disputes()
        self._update_action_button()

    def _update_action_button(self):
        today_log = self.db.get_today_work_log(self.session.user_id)

        if not today_log:
            self.btn_action.setText("출근하기 (Clock In)")
            self.btn_action.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; font-size: 14px;")
            self.btn_action.setProperty("mode", "IN")
            self.btn_action.setEnabled(True)
        elif today_log["status"] == "WORKING":
            self.btn_action.setText("퇴근하기 (Clock Out)")
            self.btn_action.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; font-size: 14px;")
            self.btn_action.setProperty("mode", "OUT")
            self.btn_action.setEnabled(True)
        else:
            self.btn_action.setText("금일 근무 종료")
            self.btn_action.setStyleSheet("background-color: #9e9e9e; color: white;")
            self.btn_action.setProperty("mode", "DONE")
            self.btn_action.setEnabled(False)

    def on_work_action(self):
        mode = self.btn_action.property("mode")
        try:
            if mode == "IN":
                if Message.confirm(self, "출근", "지금 출근하시겠습니까?"):
                    self.db.start_work(self.session.user_id)

                    # ▼ [추가됨] 출근 성공 시 자동 백업
                    backup_manager.run_backup("request_in")

                    Message.info(self, "완료", "출근 처리되었습니다.")
            elif mode == "OUT":
                if Message.confirm(self, "퇴근", "지금 퇴근하시겠습니까?"):
                    self.db.end_work(self.session.user_id)

                    # ▼ [추가됨] 퇴근 성공 시 자동 백업
                    backup_manager.run_backup("request_out")

                    Message.info(self, "완료", "퇴근 처리되었습니다.")

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
            # ★ [수정] settings.py 의 WORK_STATUS 사용 (중복 코드 제거됨)
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
        # 1. 내 시급 정보 가져오기 (DB에서 최신 정보 조회)
        user_info = self.db.get_user_by_username(self.session.username)
        if not user_info:
            Message.err(self, "오류", "사용자 정보를 찾을 수 없습니다.")
            return

        hourly_wage = user_info.get('hourly_wage', 0)

        dlg = DateRangeDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        d1, d2 = dlg.get_range()

        # 2. 기간 입력 받기
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

        # 3. '내' 근무 기록 중 '확정(APPROVED)'된 것만 조회
        #    (list_all_work_logs 함수를 재사용하되 user_id 필터 적용)
        logs = self.db.list_all_work_logs(self.session.user_id, d1, d2, status_filter='APPROVED')

        if not logs:
            Message.info(self, "조회 결과", "해당 기간에 확정(승인)된 근무 기록이 없습니다.\n(아직 승인 대기 중인 기록은 계산에 포함되지 않습니다.)")
            return

        # 4. 계산기 가동
        log_dicts = [dict(r) for r in logs]
        calc = SalaryCalculator(wage_per_hour=hourly_wage)
        res = calc.calculate_period(log_dicts)

        if not res:
            Message.info(self, "결과", "계산할 데이터가 없습니다.")
            return

        # 5. 결과 보여주기 (주휴수당 상세 내역 포함)
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





