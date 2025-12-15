# timeclock/ui/owner_page.py
# -*- coding: utf-8 -*-
import logging
from pathlib import Path
from PyQt5 import QtWidgets, QtCore

from timeclock.utils import Message
from ui.widgets import DateRangeBar, Table
from ui.dialogs import ApproveDialog, ChangePasswordDialog  # RejectSignupDialog는 dialogs.py에 추가되었다고 가정

from timeclock.settings import (
    REQ_TYPES,
    REQ_STATUS,
    REASON_CODES,
    DISPUTE_STATUS,
    DISPUTE_STATUS_ITEMS,
    SIGNUP_STATUS,  # STEP 4 상수
)


class OwnerPage(QtWidgets.QWidget):
    logout_requested = QtCore.pyqtSignal()

    def __init__(self, db, session, parent=None):
        super().__init__(parent)
        self.db = db
        self.session = session

        header = QtWidgets.QLabel(f"사업주 화면 - {session.username}")
        f = header.font()
        f.setPointSize(13)
        f.setBold(True)
        header.setFont(f)

        # ----------------- 공통 버튼 -----------------
        self.btn_change_pw = QtWidgets.QPushButton("비밀번호 변경")
        self.btn_logout = QtWidgets.QPushButton("로그아웃")
        self.btn_change_pw.clicked.connect(self.change_password)
        self.btn_logout.clicked.connect(self.logout_requested.emit)

        # ----------------- 탭 위젯 정의 및 초기화 -----------------
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_pending_tab(), "미처리 근태 요청")
        self.tabs.addTab(self._build_dispute_tab(), "이의 제기 관리")
        self.tabs.addTab(self._build_signup_tab(), "가입 신청 관리")

        # 근로자 기록 조회 패널
        worker_panel = self._build_worker_records_panel()

        # ----------------- 메인 레이아웃 -----------------
        top_buttons = QtWidgets.QHBoxLayout()
        top_buttons.addStretch(1)
        top_buttons.addWidget(self.btn_change_pw)
        top_buttons.addWidget(self.btn_logout)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(header)
        layout.addLayout(top_buttons)
        layout.addWidget(self.tabs)
        layout.addWidget(worker_panel)

        self.setLayout(layout)

        # ----------------- 초기 데이터 로드 -----------------
        self.refresh()
        self.refresh_disputes()

        self.refresh_signup_requests()

        self.refresh_worker_records()

        # ==========================================================

    # UI 빌더 메서드 (탭)
    # ==========================================================

    def _build_pending_tab(self):
        """미처리 근태 요청 탭을 구축합니다."""

        self.filter_pending = DateRangeBar(label="미처리 요청 조회기간")
        self.filter_pending.applied.connect(lambda *_: self.refresh())

        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_approve = QtWidgets.QPushButton("선택 요청 승인")
        self.btn_export = QtWidgets.QPushButton("CSV 내보내기(승인 기록)")

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_approve.clicked.connect(self.approve_selected)
        self.btn_export.clicked.connect(self.export_csv)

        self.pending_table = Table(["요청ID", "근로자", "유형", "요청시각", "상태"])

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_approve)
        top.addWidget(self.btn_export)
        top.addStretch(1)

        note = QtWidgets.QLabel(
            "원칙: 요청 기록은 원본 보존, 승인(확정 시각)은 추가 기록으로 생성됩니다.\n"
            "정정이 필요한 경우, 승인 다이얼로그에서 실제 근로 제공 시각을 입력하고 사유/코멘트를 남기세요."
        )
        note.setWordWrap(True)

        l = QtWidgets.QVBoxLayout()
        l.addWidget(self.filter_pending)
        l.addLayout(top)
        l.addWidget(note)
        l.addWidget(QtWidgets.QLabel("미처리 요청(Inbox)"))
        l.addWidget(self.pending_table)

        widget = QtWidgets.QWidget()
        widget.setLayout(l)
        return widget

    def _build_dispute_tab(self):
        """이의 제기 탭을 구축합니다."""

        self.filter_disputes = DateRangeBar(label="이의제기 조회기간")
        self.filter_disputes.applied.connect(lambda *_: self.refresh_disputes())

        self.btn_resolve_dispute = QtWidgets.QPushButton("선택 이의 처리")
        self.btn_disputes = QtWidgets.QPushButton("이의 제기 새로고침")

        self.btn_resolve_dispute.clicked.connect(self.resolve_selected_dispute)
        self.btn_disputes.clicked.connect(self.refresh_disputes)

        self.dispute_table = Table([
            "이의ID", "근로자", "요청ID", "유형", "요청시각", "승인시각",
            "이의유형", "이의내용", "등록시각",
            "처리상태", "처리코멘트", "처리시각"
        ])

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.btn_disputes)
        top.addWidget(self.btn_resolve_dispute)
        top.addStretch(1)

        l = QtWidgets.QVBoxLayout()
        l.addWidget(self.filter_disputes)
        l.addLayout(top)
        l.addWidget(QtWidgets.QLabel("이의 제기(Disputes)"))
        l.addWidget(self.dispute_table)

        widget = QtWidgets.QWidget()
        widget.setLayout(l)
        return widget

    def _build_signup_tab(self):
        """가입 신청 관리 탭을 구축합니다."""

        self.btn_approve_signup = QtWidgets.QPushButton("선택 가입 승인")
        self.btn_reject_signup = QtWidgets.QPushButton("선택 가입 거절")
        self.btn_refresh_signup = QtWidgets.QPushButton("새로고침")

        self.btn_approve_signup.clicked.connect(self.approve_signup)
        self.btn_reject_signup.clicked.connect(self.reject_signup)
        self.btn_refresh_signup.clicked.connect(self.refresh_signup_requests)

        signup_btn_row = QtWidgets.QHBoxLayout()
        signup_btn_row.addStretch(1)
        signup_btn_row.addWidget(self.btn_approve_signup)
        signup_btn_row.addWidget(self.btn_reject_signup)
        signup_btn_row.addWidget(self.btn_refresh_signup)

        self.signup_table = Table(
            ["DB ID", "신청 ID", "전화번호", "생년월일", "신청일", "상태"]
        )
        self.signup_table.setColumnWidth(0, 0)  # DB ID 숨김

        signup_tab_layout = QtWidgets.QVBoxLayout()
        signup_tab_layout.addLayout(signup_btn_row)
        signup_tab_layout.addWidget(self.signup_table)

        widget = QtWidgets.QWidget()
        widget.setLayout(signup_tab_layout)
        return widget

    def _build_worker_records_panel(self):
        """근로자별 기록 조회 패널을 구축합니다."""
        box = QtWidgets.QGroupBox("근로자 기록 조회(승인/미승인 포함)")
        v = QtWidgets.QVBoxLayout()

        top = QtWidgets.QHBoxLayout()
        self.cb_worker = QtWidgets.QComboBox()
        self.cb_worker.setMinimumWidth(200)

        self._load_worker_list()

        self.worker_filter = DateRangeBar(label="조회기간")
        self.worker_filter.applied.connect(lambda *_: self.refresh_worker_records())

        self.btn_worker_refresh = QtWidgets.QPushButton("조회")
        self.btn_worker_refresh.clicked.connect(self.refresh_worker_records)

        top.addWidget(QtWidgets.QLabel("근로자"))
        top.addWidget(self.cb_worker)
        top.addSpacing(10)
        top.addWidget(self.worker_filter)
        top.addWidget(self.btn_worker_refresh)
        top.addStretch(1)

        self.worker_table = Table(["요청ID", "근로자", "유형", "요청시각", "상태", "승인시각(확정)", "정정사유", "코멘트", "승인자"])

        v.addLayout(top)
        v.addWidget(self.worker_table)
        box.setLayout(v)
        return box

    # ==========================================================
    # 데이터 로드 및 처리 메서드
    # ==========================================================

    def refresh(self):
        """미처리 요청 목록을 새로고침합니다."""
        logging.info("Refreshing pending requests")
        date_from = self.filter_pending.get_date_from()
        date_to = self.filter_pending.get_date_to()

        try:
            rows = self.db.list_pending_requests(date_from, date_to)

            out = []
            for row in rows:
                r = dict(row)
                out.append([
                    str(r["id"]),
                    r["worker_username"],
                    REQ_TYPES.get(r["req_type"], r["req_type"]),
                    r["requested_at"],
                    REQ_STATUS.get(r["status"], r["status"])
                ])

            self.pending_table.set_rows(out)

        except Exception as e:
            logging.exception("Failed to fetch pending requests")
            Message.err(self, "오류", f"미처리 요청 목록 로드 중 오류: {e}")

    def refresh_disputes(self):
        """이의 제기 목록을 새로고침합니다."""
        logging.info("Refreshing disputes")
        date_from = self.filter_disputes.get_date_from()
        date_to = self.filter_disputes.get_date_to()

        try:
            rows = self.db.list_disputes(date_from, date_to)

            out = []
            for row in rows:
                r = dict(row)
                status_label = DISPUTE_STATUS.get(
                    r["status"],  # DB 값 (PENDING 등)
                    r["status"]  # fallback
                )

                out.append([
                    str(r["id"]),
                    r["worker_username"],
                    str(r["request_id"]),
                    REQ_TYPES.get(r["req_type"], r["req_type"]),
                    r["requested_at"],
                    r.get("approved_at", "") or "",
                    r["dispute_type"],
                    (r.get("comment", "") or "").replace("\n", " "),
                    r["created_at"],
                    status_label,
                    r.get("resolution_comment", "") or "",
                    r.get("resolved_at", "") or "",
                ])

            self.dispute_table.set_rows(out)

        except Exception as e:
            logging.exception("Failed to fetch disputes")
            Message.err(self, "오류", f"이의 제기 목록 로드 중 오류: {e}")

    def refresh_worker_records(self):
        """근로자 기록 조회 패널을 새로고침합니다."""

        # 🚨🚨🚨 수정: worker_id 로드 전에 목록이 비어있는지 확인 🚨🚨🚨
        if self.cb_worker.count() <= 0:
            self._load_worker_list()

        worker_id = self.cb_worker.currentData()

        if worker_id is None or worker_id == -1:
            self.worker_table.set_rows([])
            return

        d1, d2 = self.worker_filter.get_range()

        try:
            rows = self.db.list_requests_for_any_user(worker_id, d1, d2)

            out = []
            for row in rows:
                r = dict(row)
                req_type_label = dict(REQ_TYPES).get(r["req_type"], r["req_type"])
                status_label = REQ_STATUS.get(r["status"], r["status"])
                reason_label = REASON_CODES.get(r.get("reason_code", "") or "", r.get("reason_code", "") or "")

                out.append([
                    str(r["id"]),
                    r["worker_username"],
                    req_type_label,
                    r["requested_at"],
                    status_label,
                    r.get("approved_at", "") or "",
                    reason_label,
                    r.get("approval_comment", "") or "",
                    r.get("owner_username", "") or "",
                ])

            self.worker_table.set_rows(out)
        except Exception as e:
            logging.exception("refresh_worker_records failed")
            Message.err(self, "오류", f"근로자 기록 조회 중 오류: {e}")

    def refresh_signup_requests(self):
        """가입 신청 목록을 새로고침합니다."""
        logging.info("Refreshing signup requests")
        try:
            rows = self.db.list_pending_signup_requests()

            data = []
            for row in rows:
                r = dict(row)

                phone = r.get("phone", "")
                phone_masked = f"{phone[:3]}-****-{phone[-4:]}"
                birth = r.get("birthdate", "")
                birth_masked = f"{birth[:4]}-**-**"

                data.append([
                    r["id"],
                    r["username"],
                    phone_masked,
                    birth_masked,
                    r["created_at"],
                    SIGNUP_STATUS.get(r["status"], r["status"])
                ])

            self.signup_table.set_rows(data)

        except Exception as e:
            logging.exception("Failed to fetch signup requests")
            Message.err(self, "오류", f"가입 신청 목록 로드 중 오류: {e}")

    def approve_selected(self):
        """선택된 근태 요청을 승인합니다."""
        row_idx = self.pending_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "승인", "미처리 요청 테이블에서 항목을 선택하세요.")
            return

        # 🚨🚨🚨 수정: 안전한 ID 변환 및 데이터 로드 🚨🚨🚨
        req_id_str = self.pending_table.get_cell(row_idx, 0)
        username = self.pending_table.get_cell(row_idx, 1)

        try:
            req_id = int(req_id_str)
        except ValueError:
            logging.error(f"Invalid request ID found in table: {req_id_str}")
            Message.err(self, "오류", "테이블에서 유효하지 않은 요청 ID를 읽었습니다.")
            return

        request_detail = None
        try:
            # 원본 요청 상세 정보 조회 (DB 충돌의 가장 흔한 지점)
            request_detail = self.db.get_request_with_details(req_id)
        except Exception as e:
            # 🚨 DB 오류 발생 시, 프로그램이 꺼지는 대신 명확한 메시지를 띄우게 함
            logging.exception("Failed to get request detail from DB")
            Message.err(self, "오류", f"요청 상세 정보 로드 중 치명적인 DB 오류: {e}")
            return  # 여기서 return 하여 강제 종료 방지

        if not request_detail:
            Message.err(self, "승인", f"요청 ID {req_id} 정보를 DB에서 불러올 수 없습니다.")
            return

        # 승인 다이얼로그 호출
        try:
            dlg = ApproveDialog(parent=self, request_row=request_detail)
            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                return
        except Exception as e:
            logging.exception("ApproveDialog failed")
            Message.err(self, "오류", f"승인 다이얼로그 생성 중 오류: {e}")
            return

        # ApproveDialog의 get_values() 메서드 호출
        approved_at_str, reason_code, comment = dlg.get_values()

        if not approved_at_str:
            Message.warn(self, "승인", "확정 시각을 입력해야 합니다.")
            return

        if not reason_code:
            Message.warn(self, "승인", "정정 사유를 선택해야 합니다.")
            return

        try:
            self.db.approve_request(
                request_id=req_id,
                owner_id=self.session.user_id,
                approved_at=approved_at_str,
                reason_code=reason_code,
                comment=comment,
            )
            Message.info(self, "승인 완료", f"'{username}'님의 요청(ID: {req_id})이 승인되었습니다.")
            self.refresh()
        except Exception as e:
            # 🚨 DB 쓰기/승인 로직 실패 시 명확한 오류 메시지
            logging.exception("Request approval failed during DB write")
            Message.err(self, "승인 실패", f"요청 승인 처리 중 오류: {e}")

    def approve_signup(self):
        """STEP 4: 선택된 가입 신청을 승인하고 계정을 생성합니다."""
        row_idx = self.signup_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "승인", "승인할 항목을 선택해주세요.")
            return

        signup_id = int(self.signup_table.get_cell(row_idx, 0))
        username = self.signup_table.get_cell(row_idx, 1)

        if not Message.confirm(self, "가입 승인", f"'{username}'님의 가입을 승인하고 계정을 생성하시겠습니까?\n(최초 로그인 시 비밀번호 변경이 강제됩니다.)"):
            return

        try:
            self.db.approve_signup_request(
                signup_id,
                self.session.user_id,
                f"[{username}] 계정 생성 승인"
            )
            self._load_worker_list()  # 근로자 목록 업데이트
            Message.info(self, "승인 완료", f"'{username}'님의 계정이 생성되었습니다.\n(최초 로그인 시 비밀번호 변경이 필요합니다.)")
            self.refresh_signup_requests()

        except Exception as e:
            logging.exception("Signup approval failed")
            Message.err(self, "승인 실패", f"가입 승인 처리 중 오류 발생: {e}")

    def reject_signup(self):
        """STEP 4: 선택된 가입 신청을 거절하고 상태를 업데이트합니다."""
        row_idx = self.signup_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "거절", "거절할 항목을 선택해주세요.")
            return

        signup_id = int(self.signup_table.get_cell(row_idx, 0))
        username = self.signup_table.get_cell(row_idx, 1)

        # 거절 사유를 입력받는 다이얼로그 (QInputDialog 사용)
        comment, ok = QtWidgets.QInputDialog.getMultiLineText(
            self,
            "가입 거절 사유",
            f"'{username}'님의 가입을 거절하는 사유를 입력하세요:",
            ""
        )

        if not ok or not comment.strip():
            Message.warn(self, "거절", "거절을 취소하거나 사유를 입력해야 합니다.")
            return

        if not Message.confirm(self, "가입 거절", f"'{username}'님의 가입 신청을 거절하고 거절 사유를 기록하시겠습니까?"):
            return

        try:
            self.db.reject_signup_request(
                signup_id,
                self.session.user_id,
                comment.strip()
            )
            Message.info(self, "거절 완료", f"'{username}'님의 가입 신청이 거절되었습니다.")
            self.refresh_signup_requests()

        except Exception as e:
            logging.exception("Signup rejection failed")
            Message.err(self, "거절 실패", f"가입 거절 처리 중 오류 발생: {e}")

    def resolve_selected_dispute(self):
        """선택된 이의 제기를 처리합니다."""
        row_idx = self.dispute_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "이의 처리", "처리할 이의 제기 항목을 선택해주세요.")
            return

        dispute_id = int(self.dispute_table.get_cell(row_idx, 0))

        labels = [label for _, label in DISPUTE_STATUS_ITEMS]

        selected_label, ok = QtWidgets.QInputDialog.getItem(
            self,
            "이의 처리",
            "처리 상태를 선택하세요",
            labels,
            0,
            False
        )
        if not ok:
            return

        status_code = None
        for code, label in DISPUTE_STATUS_ITEMS:
            if label == selected_label:
                status_code = code
                break

        if not status_code:
            Message.err(self, "오류", "처리 상태 변환에 실패했습니다.")
            return

        comment, ok = QtWidgets.QInputDialog.getMultiLineText(
            self,
            "처리 코멘트",
            "처리 코멘트를 입력하세요(권장):",
            ""
        )
        if not ok:
            return

        try:
            self.db.resolve_dispute(
                dispute_id,
                self.session.user_id,
                status_code,
                (comment or "").strip()
            )
            Message.info(
                self,
                "처리 완료",
                f"이의ID {dispute_id}에 대한 처리가 완료되었습니다."
            )
            self.refresh_disputes()
        except Exception as e:
            logging.exception("Dispute resolution failed")
            Message.err(self, "처리 실패", f"이의 처리 중 오류 발생: {e}")

    def _load_worker_list(self):
        """근로자 목록 콤보박스를 업데이트합니다."""
        try:
            # DB 함수명: list_workers
            workers = self.db.list_workers()

            current_idx = self.cb_worker.currentIndex()
            current_data = self.cb_worker.itemData(current_idx)

            self.cb_worker.clear()
            self.cb_worker.addItem("--- 근로자 선택 ---", -1)

            new_index = 0
            for row in workers:
                w = dict(row)
                self.cb_worker.addItem(w["username"], w["id"])
                if w["id"] == current_data:
                    new_index = self.cb_worker.count() - 1

            self.cb_worker.setCurrentIndex(new_index)

        except Exception as e:
            logging.exception("Failed to load worker list")

    def export_csv(self):
        """CSV 내보내기 기능 (MainWindow의 기능 위임)"""
        Message.warn(self, "기능 미구현", "CSV 내보내기 기능은 [파일] 메뉴에서 실행해야 합니다.")

    def change_password(self):
        """비밀번호 변경 다이얼로그를 띄웁니다."""
        dlg = ChangePasswordDialog(parent=self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        new_pw = dlg.get_password()  # get_password()는 dialogs.py의 ChangePasswordDialog에 있다고 가정

        if not new_pw:
            Message.warn(self, "비밀번호 변경", "비밀번호는 8자 이상이며, 확인 값이 일치해야 합니다.")
            return

        try:
            self.db.change_password(self.session.user_id, new_pw)
            Message.info(self, "성공", "비밀번호가 성공적으로 변경되었습니다.")
        except Exception as e:
            logging.exception("Password change failed")
            Message.err(self, "오류", f"비밀번호 변경 중 오류: {e}")