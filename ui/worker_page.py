# timeclock/ui/worker_page.py
# -*- coding: utf-8 -*-
import logging
from PyQt5 import QtWidgets, QtCore, QtGui

from timeclock.utils import Message, now_str
from timeclock.settings import WORK_STATUS  # ★ [수정] 설정 파일에서 상태값 가져옴
from ui.widgets import DateRangeBar, Table
from ui.dialogs import DisputeTimelineDialog


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

        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_refresh.clicked.connect(self.refresh)

        self.btn_logout = QtWidgets.QPushButton("로그아웃")
        self.btn_logout.clicked.connect(self.logout_requested.emit)

        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addWidget(self.btn_action)
        top_layout.addSpacing(20)
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
                    Message.info(self, "완료", "출근 처리되었습니다.")
            elif mode == "OUT":
                if Message.confirm(self, "퇴근", "지금 퇴근하시겠습니까?"):
                    self.db.end_work(self.session.user_id)
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