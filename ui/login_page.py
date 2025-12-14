# timeclock/ui/login_page.py
# -*- coding: utf-8 -*-
import logging
from dataclasses import dataclass
from PyQt5 import QtWidgets, QtCore

from timeclock.settings import (
    APP_NAME,
    DEFAULT_OWNER_USER, DEFAULT_OWNER_PASS,
    DEFAULT_WORKER_USER, DEFAULT_WORKER_PASS,
)
from timeclock.utils import Message


@dataclass
class Session:
    user_id: int
    username: str
    role: str  # 'worker' | 'owner'
    must_change_pw: bool  # 🚨 STEP 5: must_change_pw 상태 추가


class LoginPage(QtWidgets.QWidget):
    """
    STEP 1 추가:
    - [직원가입] 버튼 추가
    - 클릭 시 signup_requested 시그널만 emit (DB 작업 없음)
    """
    logged_in = QtCore.pyqtSignal(object)   # Session
    signup_requested = QtCore.pyqtSignal()  # 화면 전환 요청(가입)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db

        title = QtWidgets.QLabel(APP_NAME)
        f = title.font()
        f.setPointSize(16)
        f.setBold(True)
        title.setFont(f)
        # noinspection PyUnresolvedReferences
        title.setAlignment(QtCore.Qt.AlignCenter)

        self.le_user = QtWidgets.QLineEdit()
        self.le_user.setPlaceholderText("사용자 ID")
        self.le_user.setClearButtonEnabled(True)

        self.le_pass = QtWidgets.QLineEdit()
        self.le_pass.setPlaceholderText("비밀번호")
        self.le_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        self.le_pass.setClearButtonEnabled(True)

        self.btn_login = QtWidgets.QPushButton("로그인")
        self.btn_signup = QtWidgets.QPushButton("직원가입")

        # (개발/테스트 편의) 기본 계정 자동 입력
        self.btn_fill_owner = QtWidgets.QPushButton("기본 사장 계정 입력")
        self.btn_fill_worker = QtWidgets.QPushButton("기본 근로자 계정 입력")

        self.btn_login.clicked.connect(self.on_login)
        self.btn_signup.clicked.connect(self.on_signup_clicked)
        self.btn_fill_owner.clicked.connect(self._fill_owner)
        self.btn_fill_worker.clicked.connect(self._fill_worker)

        self.le_pass.returnPressed.connect(self.on_login)
        self.le_user.returnPressed.connect(lambda: self.le_pass.setFocus())

        form = QtWidgets.QFormLayout()
        form.addRow("ID", self.le_user)
        form.addRow("PW", self.le_pass)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.btn_login, 2)
        btn_row.addWidget(self.btn_signup, 1)

        dev_row = QtWidgets.QHBoxLayout()
        dev_row.addWidget(self.btn_fill_owner)
        dev_row.addWidget(self.btn_fill_worker)

        box = QtWidgets.QGroupBox("로그인")
        vbox = QtWidgets.QVBoxLayout()
        vbox.addLayout(form)
        vbox.addLayout(btn_row)
        vbox.addSpacing(8)
        vbox.addLayout(dev_row)
        box.setLayout(vbox)

        outer = QtWidgets.QVBoxLayout()
        outer.addWidget(title)
        outer.addSpacing(10)
        outer.addWidget(box)
        outer.addStretch(1)

        self.setLayout(outer)

    def _fill_owner(self):
        self.le_user.setText(DEFAULT_OWNER_USER)
        self.le_pass.setText(DEFAULT_OWNER_PASS)
        self.le_pass.setFocus()

    def _fill_worker(self):
        self.le_user.setText(DEFAULT_WORKER_USER)
        self.le_pass.setText(DEFAULT_WORKER_PASS)
        self.le_pass.setFocus()

    def on_signup_clicked(self):
        logging.info("Signup requested from LoginPage")
        self.signup_requested.emit()  # ← 이게 반드시 있어야 함

    def on_login(self):
        username = self.le_user.text().strip()
        password = self.le_pass.text().strip()
        if not username or not password:
            Message.warn(self, "로그인", "사용자 ID와 비밀번호를 입력하세요.")
            return

        try:
            user = self.db.verify_login(username, password)
        except Exception as e:
            logging.exception("verify_login failed")
            Message.err(self, "오류", f"로그인 처리 중 오류: {e}")
            return

        if not user:
            Message.err(self, "로그인 실패", "ID 또는 비밀번호가 올바르지 않습니다.")
            return

        # 🚨 STEP 5: 비활성 계정 처리 (return을 통해 다음 로직 실행 방지)
        if isinstance(user, dict) and user.get("status") == "INACTIVE":
            Message.err(self, "로그인 실패", "퇴사 처리된 계정입니다. 사업주에게 문의하세요.")
            return  # 🚨 이 시점에서 함수를 종료합니다.

        # 🚨 비활성 계정이 아니며 user가 DB Row 객체인 경우에만 Session 생성 및 시그널 발생

        # 🚨 STEP 5: Session 객체에 must_change_pw 상태 추가
        session = Session(
            user_id=user["id"],
            username=user["username"],
            role=user["role"],
            must_change_pw=(user.get("must_change_pw", 0) == 1)  # DB 값 사용
        )

        logging.info("Login success: %s (%s)", session.username, session.role)
        self.logged_in.emit(session)
