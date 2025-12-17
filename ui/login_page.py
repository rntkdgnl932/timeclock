# ui/login_page.py
# -*- coding: utf-8 -*-
import logging
from dataclasses import dataclass
from PyQt5 import QtWidgets, QtCore, QtGui

# [기존 설정 및 유틸 참조 유지]
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
    must_change_pw: bool


class LoginPage(QtWidgets.QWidget):
    logged_in = QtCore.pyqtSignal(object)  # Session
    signup_requested = QtCore.pyqtSignal()  # 화면 전환 요청(가입)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db

        # [1] 배경 스타일 설정 (따뜻한 감성 톤)
        self.setObjectName("loginPage")
        self.setStyleSheet("""
            QWidget#loginPage {
                background-color: #fcfaf5; /* 따뜻한 아이보리 배경 */
            }
        """)

        # 전체 중앙 배치를 위한 레이아웃
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setAlignment(QtCore.Qt.AlignCenter)

        # [2] 로그인 카드 (반투명 화이트 박스)
        self.card = QtWidgets.QFrame()
        self.card.setFixedWidth(450)
        self.card.setStyleSheet("""
            QFrame {
                background-color: white;
                border-radius: 30px;
                border: 1px solid #eee;
            }
        """)

        # 카드 그림자 효과
        shadow = QtWidgets.QGraphicsDropShadowEffect()
        shadow.setBlurRadius(40)
        shadow.setColor(QtGui.QColor(0, 0, 0, 30))
        shadow.setOffset(0, 15)
        self.card.setGraphicsEffect(shadow)

        card_layout = QtWidgets.QVBoxLayout(self.card)
        card_layout.setContentsMargins(50, 60, 50, 60)
        card_layout.setSpacing(20)

        # [3] HobbyBrown 로고 디자인 (텍스트 로고)
        self.logo_label = QtWidgets.QLabel("HobbyBrown")
        self.logo_label.setAlignment(QtCore.Qt.AlignCenter)

        # 로고 폰트 설정
        logo_font = QtGui.QFont("Arial Rounded MT Bold", 40)
        if not logo_font.exactMatch():
            logo_font = QtGui.QFont("Malgun Gothic", 40, QtGui.QFont.Bold)

        self.logo_label.setFont(logo_font)
        self.logo_label.setStyleSheet("color: #5d4037; margin-bottom: 10px;")  # 진한 브라운

        # 로고 은은한 그림자
        logo_shadow = QtWidgets.QGraphicsDropShadowEffect()
        logo_shadow.setBlurRadius(4)
        logo_shadow.setOffset(2, 2)
        logo_shadow.setColor(QtGui.QColor(0, 0, 0, 40))
        self.logo_label.setGraphicsEffect(logo_shadow)

        card_layout.addWidget(self.logo_label)

        sub_title = QtWidgets.QLabel("근로시간 관리 시스템")
        sub_title.setAlignment(QtCore.Qt.AlignCenter)
        sub_title.setStyleSheet("color: #999; font-size: 14px; margin-bottom: 20px;")
        card_layout.addWidget(sub_title)

        # [4] 입력 필드 스타일링
        input_style = """
            QLineEdit {
                background-color: #f8f8f8;
                border: 1px solid #eee;
                border-radius: 12px;
                padding: 15px;
                font-size: 15px;
                color: #333;
            }
            QLineEdit:focus {
                border: 1px solid #8d6e63;
                background-color: #fff;
            }
        """
        self.le_user = QtWidgets.QLineEdit()
        self.le_user.setPlaceholderText("아이디")
        self.le_user.setStyleSheet(input_style)

        self.le_pass = QtWidgets.QLineEdit()
        self.le_pass.setPlaceholderText("비밀번호")
        self.le_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        self.le_pass.setStyleSheet(input_style)

        card_layout.addWidget(self.le_user)
        card_layout.addWidget(self.le_pass)

        # [5] 로그인 버튼
        self.btn_login = QtWidgets.QPushButton("로그인")
        self.btn_login.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_login.setFixedHeight(55)
        self.btn_login.setStyleSheet("""
            QPushButton {
                background-color: #6d4c41;
                color: white;
                border-radius: 12px;
                font-size: 17px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5d4037;
            }
            QPushButton:pressed {
                background-color: #4e342e;
            }
        """)
        self.btn_login.clicked.connect(self.on_login)
        card_layout.addWidget(self.btn_login)

        # [6] 하단 보조 버튼들 (회원가입 및 테스트 계정)
        bottom_layout = QtWidgets.QHBoxLayout()

        link_style = "color: #888; border: none; background: none; font-size: 13px;"

        self.btn_signup = QtWidgets.QPushButton("직원 가입하기")
        self.btn_signup.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_signup.setStyleSheet(link_style + "text-decoration: underline;")
        self.btn_signup.clicked.connect(self.on_signup_clicked)

        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_signup)
        bottom_layout.addStretch()
        card_layout.addLayout(bottom_layout)

        # 구분선
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("background-color: #f0f0f0;")
        card_layout.addWidget(line)

        # 테스트 계정 퀵 버튼
        test_layout = QtWidgets.QHBoxLayout()
        self.btn_test_owner = QtWidgets.QPushButton("사장님 체험")
        self.btn_test_worker = QtWidgets.QPushButton("알바생 체험")

        for b in [self.btn_test_owner, self.btn_test_worker]:
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet("color: #bbb; border: 1px solid #eee; border-radius: 10px; padding: 5px; font-size: 11px;")
            test_layout.addWidget(b)

        self.btn_test_owner.clicked.connect(self.fill_owner)
        self.btn_test_worker.clicked.connect(self.fill_worker)
        card_layout.addLayout(test_layout)

        main_layout.addWidget(self.card)

    # 기능 로직 유지
    def fill_owner(self):
        self.le_user.setText(DEFAULT_OWNER_USER)
        self.le_pass.setText(DEFAULT_OWNER_PASS)
        self.le_pass.setFocus()

    def fill_worker(self):
        self.le_user.setText(DEFAULT_WORKER_USER)
        self.le_pass.setText(DEFAULT_WORKER_PASS)
        self.le_pass.setFocus()

    def on_signup_clicked(self):
        self.signup_requested.emit()

    def on_login(self):
        username = self.le_user.text().strip()
        password = self.le_pass.text().strip()
        if not username or not password:
            Message.warn(self, "로그인", "아이디와 비밀번호를 입력하세요.")
            return

        try:
            user = self.db.verify_login(username, password)
        except Exception as e:
            logging.exception("verify_login failed")
            Message.err(self, "오류", f"로그인 중 오류 발생: {e}")
            return

        if not user:
            Message.err(self, "로그인 실패", "아이디 또는 비밀번호가 올바르지 않습니다.")
            return

        if isinstance(user, dict) and user.get("status") == "INACTIVE":
            Message.err(self, "로그인 실패", "퇴사 처리된 계정입니다.")
            return

        session = Session(
            user_id=user['id'],
            username=user['username'],
            role=user['role'],
            must_change_pw=(user.get('must_change_pw') == 1)
        )
        self.logged_in.emit(session)