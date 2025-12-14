# timeclock/ui/owner_page.py
# -*- coding: utf-8 -*-
import logging
from pathlib import Path
from PyQt5 import QtWidgets, QtCore

from timeclock.utils import Message
from ui.widgets import DateRangeBar, Table
from ui.dialogs import ApproveDialog, ChangePasswordDialog

from timeclock.settings import (
    REQ_TYPES,
    REQ_STATUS,
    REASON_CODES,
    DISPUTE_STATUS,
    DISPUTE_STATUS_ITEMS,
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

        self.filter_pending = DateRangeBar(label="미처리 요청 조회기간")
        self.filter_pending.applied.connect(lambda *_: self.refresh())

        self.filter_disputes = DateRangeBar(label="이의제기 조회기간")
        self.filter_disputes.applied.connect(lambda *_: self.refresh_disputes())

        self.btn_resolve_dispute = QtWidgets.QPushButton("선택 이의 처리")
        self.btn_resolve_dispute.clicked.connect(self.resolve_selected_dispute)

        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_approve = QtWidgets.QPushButton("선택 요청 승인")
        self.btn_export = QtWidgets.QPushButton("CSV 내보내기(승인 기록)")
        self.btn_disputes = QtWidgets.QPushButton("이의 제기 새로고침")
        self.btn_change_pw = QtWidgets.QPushButton("비밀번호 변경")
        self.btn_logout = QtWidgets.QPushButton("로그아웃")

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_approve.clicked.connect(self.approve_selected)
        self.btn_export.clicked.connect(self.export_csv)
        self.btn_disputes.clicked.connect(self.refresh_disputes)
        self.btn_change_pw.clicked.connect(self.change_password)
        self.btn_logout.clicked.connect(self.logout_requested.emit)

        self.pending_table = Table(["요청ID","근로자","유형","요청시각","상태"])
        self.dispute_table = Table([
            "이의ID", "근로자", "요청ID", "유형", "요청시각", "승인시각",
            "이의유형", "이의내용", "등록시각",
            "처리상태", "처리코멘트", "처리시각"
        ])

        note = QtWidgets.QLabel(
            "원칙: 요청 기록은 원본 보존, 승인(확정 시각)은 추가 기록으로 생성됩니다.\n"
            "정정이 필요한 경우, 승인 다이얼로그에서 실제 근로 제공 시각을 입력하고 사유/코멘트를 남기세요."
        )
        note.setWordWrap(True)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_approve)
        top.addWidget(self.btn_export)
        top.addWidget(self.btn_disputes)
        top.addWidget(self.btn_resolve_dispute)

        top.addStretch(1)
        top.addWidget(self.btn_change_pw)
        top.addWidget(self.btn_logout)

        w1 = QtWidgets.QWidget()
        l1 = QtWidgets.QVBoxLayout()
        l1.addWidget(self.filter_pending)
        l1.addWidget(QtWidgets.QLabel("미처리 요청(Inbox)"))
        l1.addWidget(self.pending_table)
        w1.setLayout(l1)

        w2 = QtWidgets.QWidget()
        l2 = QtWidgets.QVBoxLayout()
        l2.addWidget(self.filter_disputes)
        l2.addWidget(QtWidgets.QLabel("이의 제기(Disputes)"))
        l2.addWidget(self.dispute_table)
        w2.setLayout(l2)

        # noinspection PyUnresolvedReferences
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(w1)
        split.addWidget(w2)
        split.setSizes([360, 280])

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(header)
        layout.addLayout(top)
        layout.addWidget(note)
        layout.addWidget(split)

        worker_panel = self._build_worker_records_panel()  # 새로 추가(근로자 조회 박스)
        layout.addWidget(worker_panel)

        self.setLayout(layout)

        self.refresh()
        self.refresh_disputes()
        self.refresh_worker_records()

    def refresh(self):
        d1, d2 = self.filter_pending.get_range()
        try:
            rows = self.db.list_pending_requests(d1, d2)
            out = []
            for r in rows:
                req_type_label = dict(REQ_TYPES).get(r["req_type"], r["req_type"])
                status_label = REQ_STATUS.get(r["status"], r["status"])
                out.append([
                    str(r["id"]),
                    r["worker_username"],
                    req_type_label,
                    r["requested_at"],
                    status_label,
                ])

            self.pending_table.set_rows(out)
        except Exception as e:
            logging.exception("refresh pending failed")
            Message.err(self, "오류", f"미처리 요청 조회 중 오류: {e}")

    def refresh_disputes(self):
        d1, d2 = self.filter_disputes.get_range()
        try:
            rows = self.db.list_disputes(d1, d2)
            out = []
            for d in rows:
                req_type_label = dict(REQ_TYPES).get(d["req_type"], d["req_type"])
                raw_status = d["status"] if "status" in d.keys() else "OPEN"
                status_label = DISPUTE_STATUS.get(raw_status, raw_status)

                out.append([
                    str(d["id"]),
                    d["worker_username"],
                    str(d["request_id"]),
                    req_type_label,
                    d["requested_at"],
                    d["approved_at"] or "",
                    d["dispute_type"],
                    (d["comment"] or "").replace("\n", " "),
                    d["created_at"],
                    status_label,
                    (d["resolution_comment"] or "").replace("\n", " ") if "resolution_comment" in d.keys() else "",
                    d["resolved_at"] or "" if "resolved_at" in d.keys() else "",
                ])

            self.dispute_table.set_rows(out)
        except Exception as e:
            logging.exception("refresh disputes failed")
            Message.err(self, "오류", f"이의 제기 조회 중 오류: {e}")

    def approve_selected(self):
        row_idx = self.pending_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "승인", "미처리 요청 테이블에서 항목을 선택하세요.")
            return
        request_id = int(self.pending_table.get_cell(row_idx, 0))
        detail = self.db.get_request_with_details(request_id)
        if not detail:
            Message.err(self, "승인", "요청 정보를 불러올 수 없습니다.")
            return

        dlg = ApproveDialog(self, detail)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        approved_at, reason_code, comment = dlg.get_values()
        try:
            self.db.approve_request(request_id, self.session.user_id, approved_at, reason_code, comment or "")
            Message.info(self, "승인 완료", f"요청ID {request_id} 승인 완료.\n확정 시각: {approved_at}")
            self.refresh()
        except Exception as e:
            logging.exception("approve failed")
            Message.err(self, "오류", f"승인 처리 중 오류: {e}")

    def export_csv(self):
        d1, d2 = self.filter_pending.get_range()
        dlg = QtWidgets.QFileDialog(self)
        dlg.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        dlg.setNameFilters(["CSV 파일 (*.csv)"])
        dlg.setDefaultSuffix("csv")
        dlg.selectFile(f"timeclock_export_{d1}_to_{d2}.csv")
        if dlg.exec_() != QtWidgets.QFileDialog.Accepted:
            return
        out_path = Path(dlg.selectedFiles()[0])

        try:
            self.db.export_records_csv(out_path, d1, d2)
            Message.info(self, "내보내기 완료", f"CSV 저장 완료:\n{out_path}\n(기간: {d1} ~ {d2})")
        except Exception as e:
            logging.exception("export failed")
            Message.err(self, "오류", f"CSV 내보내기 중 오류: {e}")

    def change_password(self):
        dlg = ChangePasswordDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        new_pw = dlg.get_password()
        if not new_pw:
            Message.warn(self, "비밀번호 변경", "비밀번호는 8자 이상이며, 확인 값이 일치해야 합니다.")
            return
        try:
            self.db.change_password(self.session.user_id, new_pw)
            Message.info(self, "비밀번호 변경", "비밀번호가 변경되었습니다.")
        except Exception as e:
            logging.exception("change_password failed")
            Message.err(self, "오류", f"비밀번호 변경 중 오류: {e}")

    def _build_worker_records_panel(self):
        box = QtWidgets.QGroupBox("근로자 기록 조회(승인/미승인 포함)")
        v = QtWidgets.QVBoxLayout()

        top = QtWidgets.QHBoxLayout()
        self.cb_worker = QtWidgets.QComboBox()
        self.cb_worker.setMinimumWidth(200)

        workers = self.db.list_workers()
        for w in workers:
            self.cb_worker.addItem(w["username"], w["id"])

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


    def refresh_worker_records(self):
        if self.cb_worker.count() == 0:
            Message.warn(self, "조회", "등록된 근로자가 없습니다.")
            return
        worker_id = int(self.cb_worker.currentData())
        d1, d2 = self.worker_filter.get_range()

        try:
            rows = self.db.list_requests_for_any_user(worker_id, d1, d2)
            out = []
            for r in rows:
                req_type_label = dict(REQ_TYPES).get(r["req_type"], r["req_type"])
                status_label = REQ_STATUS.get(r["status"], r["status"])
                reason_label = REASON_CODES.get(r["reason_code"] or "", r["reason_code"] or "")

                out.append([
                    str(r["id"]),
                    r["worker_username"],
                    req_type_label,
                    r["requested_at"],
                    status_label,
                    r["approved_at"] or "",
                    reason_label,
                    r["approval_comment"] or "",
                    r["owner_username"] or "",
                ])

            self.worker_table.set_rows(out)
        except Exception as e:
            logging.exception("refresh_worker_records failed")
            Message.err(self, "오류", f"근로자 기록 조회 중 오류: {e}")

    def resolve_selected_dispute(self):
        row_idx = self.dispute_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "이의 처리", "이의 제기 테이블에서 항목을 선택하세요.")
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

        # 한글 → 코드 변환
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
                status_code,  # ✅ DB에는 영문 코드 저장
                (comment or "").strip()
            )
            Message.info(
                self,
                "처리 완료",
                f"이의ID {dispute_id} 처리 상태가 '{selected_label}'로 저장되었습니다."
            )
            self.refresh_disputes()
        except Exception as e:
            logging.exception("resolve_dispute failed")
            Message.err(self, "오류", f"이의 처리 저장 중 오류: {e}")


