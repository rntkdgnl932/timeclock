# timeclock/ui/dialogs.py
# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets, QtCore
from timeclock.settings import REASON_CODES, REQ_TYPES


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
        if not p1 or len(p1) < 8:
            return None
        if p1 != p2:
            return None
        return p1


class ApproveDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, request_row=None):
        super().__init__(parent)
        self.setWindowTitle("요청 승인(근로시간 확정)")
        self.setModal(True)
        self.resize(520, 260)
        self.request_row = request_row

        self.lbl_info = QtWidgets.QLabel()
        self.lbl_info.setWordWrap(True)

        self.dt_approved = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.dt_approved.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_approved.setCalendarPopup(True)

        try:
            req_dt = QtCore.QDateTime.fromString(request_row["requested_at"], "yyyy-MM-dd HH:mm:ss")
            if req_dt.isValid():
                self.dt_approved.setDateTime(req_dt)
        except Exception:
            pass

        self.cb_reason = QtWidgets.QComboBox()
        for code, label in REASON_CODES.items():  # ✅ dict 순회
            self.cb_reason.addItem(f"{label} ({code})", code)

        # ✅ 기본 선택: 요청대로 승인
        idx = self.cb_reason.findData("AS_REQUESTED")
        if idx >= 0:
            self.cb_reason.setCurrentIndex(idx)
        elif self.cb_reason.count() > 0:
            self.cb_reason.setCurrentIndex(0)

        if self.cb_reason.count() > 0:
            self.cb_reason.setCurrentIndex(0)

        self.te_comment = QtWidgets.QPlainTextEdit()
        self.te_comment.setPlaceholderText("예: 준비시간으로 인한 무급 / 실제 퇴근 시간 기록 정정함 등")

        form = QtWidgets.QFormLayout()
        form.addRow("확정 시각(승인)", self.dt_approved)
        form.addRow("정정 사유", self.cb_reason)
        form.addRow("코멘트", self.te_comment)

        self.btn_ok = QtWidgets.QPushButton("승인 확정")
        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)

        rt = dict(request_row)
        req_type_label = dict(REQ_TYPES).get(rt.get("req_type", ""), rt.get("req_type", ""))
        self.lbl_info.setText(
            f"요청자: {rt.get('worker_username')} | 요청유형: {req_type_label}\n"
            f"요청시각: {rt.get('requested_at')} | 상태: {rt.get('status')}"
        )

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.lbl_info)
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def get_values(self):
        approved_at = self.dt_approved.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        reason_code = self.cb_reason.currentData()
        comment = self.te_comment.toPlainText().strip()
        return approved_at, reason_code, comment


class DisputeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, request_detail=None):
        super().__init__(parent)
        self.setWindowTitle("이의 제기")
        self.setModal(True)
        self.resize(520, 240)
        self.request_detail = request_detail

        self.cb_type = QtWidgets.QComboBox()
        self.cb_type.addItems(["근로시간 확정 시각에 이의", "정정 사유/코멘트에 이의", "기타"])

        self.te_comment = QtWidgets.QPlainTextEdit()
        self.te_comment.setPlaceholderText("이의 내용(사실관계/시간/사유)을 구체적으로 작성하세요.")

        info = QtWidgets.QLabel()
        info.setWordWrap(True)
        rt = dict(request_detail)
        req_type_label = dict(REQ_TYPES).get(rt.get("req_type", ""), rt.get("req_type", ""))
        info.setText(
            f"요청ID: {rt.get('id')} | 유형: {req_type_label}\n"
            f"요청시각: {rt.get('requested_at')} | 승인시각: {rt.get('approved_at') or '-'}\n"
            f"정정사유: {rt.get('reason_code') or '-'}\n"
            f"코멘트: {rt.get('approval_comment') or '-'}"
        )

        form = QtWidgets.QFormLayout()
        form.addRow("이의 유형", self.cb_type)
        form.addRow("이의 내용", self.te_comment)

        self.btn_ok = QtWidgets.QPushButton("제출")
        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def get_values(self):
        dtype = self.cb_type.currentText()
        comment = self.te_comment.toPlainText().strip()
        if not comment:
            return None, None
        return dtype, comment
# timeclock/ui/dialogs.py (파일 하단에 추가)

class RejectSignupDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, username=None):
        super().__init__(parent)
        self.setWindowTitle("가입 거절 사유 입력")
        self.setModal(True)
        self.resize(450, 200)

        info = QtWidgets.QLabel(f"'{username}'님의 가입을 거절하는 사유를 입력하세요:")
        info.setWordWrap(True)

        self.te_comment = QtWidgets.QPlainTextEdit()
        self.te_comment.setPlaceholderText("거절 사유를 구체적으로 작성하세요.")

        self.btn_ok = QtWidgets.QPushButton("거절 처리")
        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(info)
        layout.addWidget(self.te_comment)
        layout.addLayout(btns)
        self.setLayout(layout)

    def get_comment(self):
        return self.te_comment.toPlainText().strip()