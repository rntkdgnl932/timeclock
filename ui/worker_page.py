# timeclock/ui/worker_page.py
# -*- coding: utf-8 -*-
import logging
from PyQt5 import QtWidgets, QtCore, QtGui

from timeclock.utils import Message, now_str
from ui.widgets import DateRangeBar, Table
from ui.dialogs import DisputeTimelineDialog  # ★ 채팅창 연결


class WorkerPage(QtWidgets.QWidget):
    logout_requested = QtCore.pyqtSignal()

    def __init__(self, db, session, parent=None):
        super().__init__(parent)
        self.db = db
        self.session = session

        # 이의제기 원본 데이터 보관용
        self._my_dispute_rows = []

        # 헤더 설정
        header = QtWidgets.QLabel(f"근로자 화면 - {session.username}")
        f = header.font()
        f.setPointSize(14)
        f.setBold(True)
        header.setFont(f)

        # ----------------------------------------------------
        # 1. 상단 컨트롤 (조회기간, 출/퇴근 버튼, 로그아웃)
        # ----------------------------------------------------
        self.filter = DateRangeBar(label="근무 조회기간")
        self.filter.applied.connect(lambda *_: self.refresh())

        # ★ 스마트 출퇴근 버튼 (하나의 버튼으로 상태에 따라 변함)
        self.btn_action = QtWidgets.QPushButton("출근하기")
        self.btn_action.setMinimumHeight(40)
        self.btn_action.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.btn_action.clicked.connect(self.on_work_action)

        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_refresh.clicked.connect(self.refresh)

        self.btn_logout = QtWidgets.QPushButton("로그아웃")
        self.btn_logout.clicked.connect(self.logout_requested.emit)

        # 상단 레이아웃
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addWidget(self.btn_action)  # 제일 중요하니까 크게
        top_layout.addSpacing(20)
        top_layout.addWidget(self.btn_refresh)
        top_layout.addStretch(1)
        top_layout.addWidget(self.btn_logout)

        # ----------------------------------------------------
        # 2. 근무 기록 테이블 (통합 뷰)
        # ----------------------------------------------------
        # 컬럼: ID(숨김), 일자, 출근, 퇴근, 상태, 확정출근, 확정퇴근, 비고
        self.work_table = Table([
            "ID", "일자", "출근(요청)", "퇴근(요청)", "상태",
            "확정 출근", "확정 퇴근", "사업주 비고"
        ])
        # ID 컬럼 숨기기 (0번)
        self.work_table.setColumnWidth(0, 0)
        # 클릭하면 '이의제기' 탭에 해당 날짜가 선택되게 할 수도 있음 (선택 사항)

        # ----------------------------------------------------
        # 3. 내 이의 제기 목록
        # ----------------------------------------------------
        self.filter_disputes = DateRangeBar(label="이의제기 기간")
        self.filter_disputes.applied.connect(lambda *_: self.refresh_my_disputes())

        # 상태 필터 (진행중 / 종료)
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

        # 이의제기 테이블
        # 컬럼: 이의ID, 날짜, 상태, 최근대화, 대화시각
        self.dispute_table = Table([
            "이의ID", "근무일자", "이의유형", "진행상태", "최근 메시지", "최근 시각"
        ])
        self.dispute_table.setColumnWidth(0, 0)  # ID 숨김

        # 더블클릭 시 채팅방 열기
        QtCore.QTimer.singleShot(0, self._wire_double_click)

        # 이의제기 필터 레이아웃
        disp_filter_layout = QtWidgets.QHBoxLayout()
        disp_filter_layout.addWidget(self.filter_disputes)
        disp_filter_layout.addWidget(self.cb_dispute_filter)
        disp_filter_layout.addWidget(self.btn_disp_refresh)
        disp_filter_layout.addStretch(1)

        # ----------------------------------------------------
        # 4. 전체 레이아웃 조립
        # ----------------------------------------------------
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

        # 초기 로드
        self.refresh()
        self.refresh_my_disputes()
        self._update_action_button()  # 버튼 상태 갱신

    # ==========================================================
    # 로직 메서드
    # ==========================================================

    def _update_action_button(self):
        """현재 근무 상태에 따라 버튼(출근/퇴근)을 바꿈"""
        # 오늘 날짜 기록 조회
        today_log = self.db.get_today_work_log(self.session.user_id)

        if not today_log:
            # 기록이 없으면 -> [출근하기]
            self.btn_action.setText("출근하기 (Clock In)")
            self.btn_action.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; font-size: 14px;")
            self.btn_action.setProperty("mode", "IN")
        elif today_log["status"] == "WORKING":
            # 근무 중이면 -> [퇴근하기]
            self.btn_action.setText("퇴근하기 (Clock Out)")
            self.btn_action.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; font-size: 14px;")
            self.btn_action.setProperty("mode", "OUT")
        else:
            # 이미 퇴근했거나 승인대기 상태 -> 버튼 비활성화 (하루 1회만 가능하게 하거나, 필요시 로직 변경)
            self.btn_action.setText("금일 근무 종료")
            self.btn_action.setStyleSheet("background-color: #9e9e9e; color: white;")
            self.btn_action.setProperty("mode", "DONE")
            self.btn_action.setEnabled(False)
            return

        self.btn_action.setEnabled(True)

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
        """근무 기록 테이블 새로고침"""
        d1, d2 = self.filter.get_range()
        rows = self.db.list_work_logs(self.session.user_id, d1, d2)

        out = []
        for r in rows:
            rr = dict(r)
            # 상태 한글화
            st = rr["status"]
            status_map = {
                "WORKING": "근무중", "PENDING": "승인대기",
                "APPROVED": "확정(승인)", "REJECTED": "반려"
            }
            status_str = status_map.get(st, st)

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
        """이의제기 목록 새로고침"""
        d1, d2 = self.filter_disputes.get_range()
        filter_type = self.cb_dispute_filter.currentData()

        rows = self.db.list_my_disputes(self.session.user_id, d1, d2, filter_type)
        self._my_dispute_rows = rows  # 데이터 보관

        out = []
        for r in rows:
            rr = dict(r)
            # 최근 메시지 (없으면 원문 코멘트라도 표시)
            # 쿼리에서 가져온 값 활용 (list_my_disputes 쿼리 확인 필요 - 여기선 쿼리가 바뀌었으므로 적절히 매핑)
            # DB.list_my_disputes 에서 d.* 를 가져오므로 comment, resolution_comment 등을 확인

            # 상태 한글화
            d_st = rr["status"]
            st_map = {"PENDING": "미처리", "IN_REVIEW": "검토중", "RESOLVED": "완료", "REJECTED": "기각"}
            d_st_str = st_map.get(d_st, d_st)

            # 표시용 요약 텍스트 (최근 대화 내용 등은 복잡하므로 간단히 유형과 상태 위주로)
            # 필요하면 DB 쿼리에 latest_message 컬럼을 추가해야 함. 지금은 comment(원문) 사용
            summary = (rr["comment"] or "").replace("\n", " ")
            if len(summary) > 30: summary = summary[:30] + "..."

            out.append([
                str(rr["id"]),
                rr["work_date"],  # work_logs 조인으로 가져온 날짜
                rr["dispute_type"],
                d_st_str,
                summary,
                rr["created_at"]  # 혹은 resolved_at
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
        """선택된 이의제기 건에 대해 채팅방 열기"""
        row = self.dispute_table.selected_first_row_index()

        # 1. 이의제기 목록에서 선택했다면 -> 해당 이의제기 채팅방 오픈
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

        # 2. 근무기록 테이블에서 선택했다면 -> '새 이의제기' 생성 팝업 -> 채팅방
        w_row = self.work_table.selected_first_row_index()
        if w_row >= 0:
            # 근무기록 ID 가져오기
            try:
                # 0번째 컬럼이 ID (숨김처리 됨)
                work_log_id_str = self.work_table.get_cell(w_row, 0)
                work_log_id = int(work_log_id_str)
            except:
                return

            # 입력 다이얼로그 (간단히 사유 입력)
            # 여기서는 채팅방을 바로 열기 위해, DB에 빈 이의제기 레코드를 먼저 만들거나,
            # 혹은 "이의제기 유형"을 묻는 팝업을 먼저 띄워야 함.

            # 간단하게 유형 입력받기
            items = ["출/퇴근 시간 정정 요청", "근무일자 오류", "기타 문의"]
            item, ok = QtWidgets.QInputDialog.getItem(self, "이의 제기", "문의 유형을 선택하세요:", items, 0, False)
            if ok and item:
                # DB에 이의제기방 생성 (create_dispute)
                # 초기 코멘트는 "대화 시작" 등으로 자동 설정하거나 입력받음

                # 상세 내용 입력
                text, ok2 = QtWidgets.QInputDialog.getText(self, "이의 제기", "첫 메시지를 입력하세요:")
                if ok2 and text:
                    dispute_id = self.db.create_dispute(work_log_id, self.session.user_id, item, text)

                    # 채팅방 열기
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