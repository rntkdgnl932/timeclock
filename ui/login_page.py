# timeclock/ui/login_page.py
# -*- coding: utf-8 -*-
import logging
from dataclasses import dataclass
from PyQt5 import QtWidgets, QtCore

from timeclock.settings import APP_NAME, DEFAULT_OWNER_USER, DEFAULT_OWNER_PASS, DEFAULT_WORKER_USER, DEFAULT_WORKER_PASS
from timeclock.utils import Message


@dataclass
class Session:
    user_id: int
    username: str
    role: str  # worker/owner


class LoginPage(QtWidgets.QWidget):
    logged_in = QtCore.pyqtSignal(object)  # Session

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db

        title = QtWidgets.QLabel(APP_NAME)
        f = title.font()
        f.setPointSize(16)
        f.setBold(True)
        title.setFont(f)

        self.le_user = QtWidgets.QLineEdit()
        self.le_user.setPlaceholderText("사용자 ID (예: owner, worker)")
        self.le_pass = QtWidgets.QLineEdit()
        self.le_pass.setPlaceholderText("비밀번호")
        self.le_pass.setEchoMode(QtWidgets.QLineEdit.Password)

        self.btn_login = QtWidgets.QPushButton("로그인")
        self.btn_login.clicked.connect(self.on_login)

        hint = QtWidgets.QLabel(
            "최초 계정:\n"
            f"- 사업주(owner): {DEFAULT_OWNER_USER} / {DEFAULT_OWNER_PASS}\n"
            f"- 근로자(worker): {DEFAULT_WORKER_USER} / {DEFAULT_WORKER_PASS}\n"
            "실운영 전 비밀번호 변경을 권장합니다."
        )
        hint.setWordWrap(True)

        form = QtWidgets.QFormLayout()
        form.addRow("사용자 ID", self.le_user)
        form.addRow("비밀번호", self.le_pass)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(title)
        layout.addSpacing(10)
        layout.addLayout(form)
        layout.addWidget(self.btn_login)
        layout.addSpacing(10)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.setLayout(layout)

    def on_login(self):
        username = self.le_user.text().strip()
        password = self.le_pass.text().strip()
        if not username or not password:
            Message.warn(self, "로그인", "사용자 ID와 비밀번호를 입력하세요.")
            return
        user = self.db.verify_login(username, password)
        if not user:
            Message.err(self, "로그인 실패", "ID 또는 비밀번호가 올바르지 않습니다.")
            return
        session = Session(user_id=user["id"], username=user["username"], role=user["role"])
        logging.info(f"Login success: {session.username} ({session.role})")
        self.logged_in.emit(session)
