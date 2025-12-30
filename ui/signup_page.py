# timeclock/ui/signup_page.py
# -*- coding: utf-8 -*-

import re
from datetime import datetime
from PyQt5 import QtWidgets, QtCore, QtGui  # ⬅️ QtGui 모듈 추가
import logging
from timeclock.utils import Message
from timeclock.auth import pbkdf2_hash_password  # 비밀번호 해시 함수 (submit 함수에서 사용)

ID_PATTERN = re.compile(r"^[a-zA-Z0-9_]{4,20}$")


class SignupPage(QtWidgets.QWidget):
    signup_done = QtCore.pyqtSignal()  # 로그인 화면으로 돌아가기

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db

        self._id_checked_ok = False
        self._last_checked_username = None

        self._build_ui()

    # ---------------- UI ----------------

    def _build_ui(self):
        # 배경 스타일 설정
        self.setObjectName("signupPage")
        self.setStyleSheet("QWidget#signupPage { background-color: #fcfaf5; }")

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setAlignment(QtCore.Qt.AlignCenter)

        # 가입 카드 구성
        self.card = QtWidgets.QFrame()
        self.card.setFixedWidth(520)
        self.card.setStyleSheet("""
            QFrame {
                background-color: white;
                border-radius: 25px;
                border: 1px solid #eee;
            }
        """)

        # 카드 그림자 효과
        shadow = QtWidgets.QGraphicsDropShadowEffect()
        shadow.setBlurRadius(30)
        shadow.setColor(QtGui.QColor(0, 0, 0, 20))
        shadow.setOffset(0, 10)
        self.card.setGraphicsEffect(shadow)

        card_layout = QtWidgets.QVBoxLayout(self.card)
        card_layout.setContentsMargins(40, 40, 40, 40)
        card_layout.setSpacing(12)

        # 상단 HobbyBrown 로고
        logo_label = QtWidgets.QLabel("HobbyBrown")
        logo_label.setAlignment(QtCore.Qt.AlignCenter)
        logo_label.setStyleSheet("font-family: 'Arial Rounded MT Bold'; font-size: 28px; color: #5d4037;")
        card_layout.addWidget(logo_label)

        title = QtWidgets.QLabel("직원 가입 신청")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #888; margin-bottom: 10px;")
        card_layout.addWidget(title)

        # 입력 필드 공통 스타일
        input_style = """
            QLineEdit {
                background-color: #f9f9f9;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                padding: 10px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #8d6e63;
                background-color: #fff;
            }
        """

        form = QtWidgets.QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(QtCore.Qt.AlignLeft)

        # 아이디 입력 및 중복확인
        self.ed_id = QtWidgets.QLineEdit()
        self.ed_id.setStyleSheet(input_style)
        self.btn_check_id = QtWidgets.QPushButton("중복확인")
        self.btn_check_id.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_check_id.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f5; border: 1px solid #ddd; border-radius: 8px;
                padding: 8px 15px; color: #666; font-weight: bold;
            }
            QPushButton:hover { background-color: #eee; }
        """)
        self.btn_check_id.clicked.connect(self.check_id)

        id_row = QtWidgets.QHBoxLayout()
        id_row.addWidget(self.ed_id)
        id_row.addWidget(self.btn_check_id)
        form.addRow("아이디 *", id_row)

        # 비밀번호 입력
        self.ed_pw = QtWidgets.QLineEdit()
        self.ed_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_pw.setStyleSheet(input_style)
        form.addRow("비밀번호 *", self.ed_pw)

        self.ed_pw2 = QtWidgets.QLineEdit()
        self.ed_pw2.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_pw2.setStyleSheet(input_style)
        form.addRow("비밀번호 확인 *", self.ed_pw2)

        # 성함 입력
        self.ed_name = QtWidgets.QLineEdit()
        self.ed_name.setPlaceholderText("실명을 입력하세요")
        self.ed_name.setStyleSheet(input_style)
        form.addRow("성함 *", self.ed_name)

        # 전화번호 입력 (3칸)
        phone_row = QtWidgets.QHBoxLayout()
        self.ed_phone1 = QtWidgets.QLineEdit()
        self.ed_phone2 = QtWidgets.QLineEdit()
        self.ed_phone3 = QtWidgets.QLineEdit()
        for ed in (self.ed_phone1, self.ed_phone2, self.ed_phone3):
            ed.setStyleSheet(input_style)
            ed.setAlignment(QtCore.Qt.AlignCenter)
            ed.setValidator(QtGui.QIntValidator())
        self.ed_phone1.setMaxLength(3)
        self.ed_phone2.setMaxLength(4)
        self.ed_phone3.setMaxLength(4)

        phone_row.addWidget(self.ed_phone1)
        phone_row.addWidget(QtWidgets.QLabel("-"))
        phone_row.addWidget(self.ed_phone2)
        phone_row.addWidget(QtWidgets.QLabel("-"))
        phone_row.addWidget(self.ed_phone3)
        form.addRow("전화번호 *", phone_row)

        # 생년월일 입력 (3칸)
        birth_row = QtWidgets.QHBoxLayout()
        self.ed_birth_y = QtWidgets.QLineEdit()
        self.ed_birth_m = QtWidgets.QLineEdit()
        self.ed_birth_d = QtWidgets.QLineEdit()
        for ed in (self.ed_birth_y, self.ed_birth_m, self.ed_birth_d):
            ed.setStyleSheet(input_style)
            ed.setAlignment(QtCore.Qt.AlignCenter)
            ed.setValidator(QtGui.QIntValidator())
        self.ed_birth_y.setPlaceholderText("YYYY")
        self.ed_birth_m.setPlaceholderText("MM")
        self.ed_birth_d.setPlaceholderText("DD")
        self.ed_birth_y.setMaxLength(4)
        self.ed_birth_m.setMaxLength(2)
        self.ed_birth_d.setMaxLength(2)

        birth_row.addWidget(self.ed_birth_y)
        birth_row.addWidget(QtWidgets.QLabel("-"))
        birth_row.addWidget(self.ed_birth_m)
        birth_row.addWidget(QtWidgets.QLabel("-"))
        birth_row.addWidget(self.ed_birth_d)
        form.addRow("생년월일 *", birth_row)

        # 선택 입력 정보
        self.ed_email = QtWidgets.QLineEdit()
        self.ed_email.setStyleSheet(input_style)
        form.addRow("이메일", self.ed_email)

        self.ed_bank = QtWidgets.QLineEdit()
        self.ed_bank.setStyleSheet(input_style)
        form.addRow("계좌정보", self.ed_bank)

        self.ed_addr = QtWidgets.QLineEdit()
        self.ed_addr.setStyleSheet(input_style)
        form.addRow("주소", self.ed_addr)

        card_layout.addLayout(form)

        # 하단 액션 버튼
        btn_action_layout = QtWidgets.QVBoxLayout()
        btn_action_layout.setSpacing(10)
        btn_action_layout.setContentsMargins(0, 15, 0, 0)

        self.btn_apply = QtWidgets.QPushButton("가입 신청하기")
        self.btn_apply.setFixedHeight(50)
        self.btn_apply.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #6d4c41; color: white; border-radius: 12px;
                font-size: 16px; font-weight: bold;
            }
            QPushButton:hover { background-color: #5d4037; }
        """)
        self.btn_apply.clicked.connect(self.submit)

        self.btn_cancel = QtWidgets.QPushButton("취소 후 돌아가기")
        self.btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_cancel.setStyleSheet(
            "color: #888; border: none; background: none; font-size: 13px; text-decoration: underline;")
        self.btn_cancel.clicked.connect(self.signup_done.emit)

        btn_action_layout.addWidget(self.btn_apply)
        btn_action_layout.addWidget(self.btn_cancel)
        card_layout.addLayout(btn_action_layout)

        main_layout.addWidget(self.card)

    # ---------------- Logic ----------------

    def check_id(self):
        username = self.ed_id.text().strip()

        if not ID_PATTERN.match(username):
            Message.err(self, "ID 확인", "ID는 영문/숫자/_ 4~20자만 가능합니다.")
            return

        # [수정] db.py에는 is_username_available 함수가 있고, True/False만 반환합니다.
        is_available = self.db.is_username_available(username)

        if is_available:
            self._id_checked_ok = True
            self._last_checked_username = username
            Message.info(self, "ID 확인", "사용 가능한 ID입니다.")
        else:
            self._id_checked_ok = False
            Message.err(self, "ID 확인", "이미 사용 중이거나 신청 중인 아이디입니다.")

    def submit(self):
        # ---------- 필수값 ----------
        username = self.ed_id.text().strip()
        pw = self.ed_pw.text()
        pw2 = self.ed_pw2.text()

        name = self.ed_name.text().strip()

        p1 = self.ed_phone1.text()
        p2 = self.ed_phone2.text()
        p3 = self.ed_phone3.text()

        y = self.ed_birth_y.text()
        m = self.ed_birth_m.text()
        d = self.ed_birth_d.text()

        # ---------- ID 검증 ----------
        if not ID_PATTERN.match(username):
            Message.err(self, "가입신청", "ID 형식이 올바르지 않습니다.")
            return

        if not self._id_checked_ok or self._last_checked_username != username:
            Message.err(self, "가입신청", "ID 중복확인을 먼저 해주세요.")
            return

        # ---------- PW ----------
        if not pw or pw != pw2 or len(pw) < 6:
            Message.err(self, "가입신청", "비밀번호를 확인하세요.(6자 이상)")
            return
        # ---------- 성함 ----------
        if not name:
            Message.err(self, "가입신청", "성함을 입력해주세요.")
            return

        # ---------- 전화 ----------
        phone_digits = f"{p1}{p2}{p3}"
        if not phone_digits.isdigit() or len(phone_digits) not in (10, 11):
            Message.err(self, "가입신청", "전화번호가 올바르지 않습니다.")
            return

        phone = phone_digits

        # ---------- 생년월일 ----------
        try:
            birthdate = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            datetime.strptime(birthdate, "%Y-%m-%d")
        except Exception:
            Message.err(self, "가입신청", "생년월일이 올바르지 않습니다.")
            return

        # ---------- 선택 ----------
        email = self.ed_email.text().strip()
        bank_account = self.ed_bank.text().strip()
        address = self.ed_addr.text().strip()

        # 비밀번호 해싱 (auth.py 재사용)
        pw_hash = pbkdf2_hash_password(pw)

        # ---------- DB ----------
        try:
            self.db.create_signup_request(
                username=username,
                pw_hash=pw_hash,  # ⬅️ 해시된 비밀번호를 전달해야 합니다.
                name=name,
                phone=phone,
                birth=birthdate,  # db.py의 인자가 birth이므로 birthdate 대신 birth를 사용합니다.
                email=email,
                account=bank_account,  # db.py의 인자가 account이므로 bank_account 대신 account를 사용합니다.
                address=address,
            )

        except Exception as e:
            # 로깅을 추가하여 디버깅을 돕습니다.
            logging.exception("가입신청 DB 등록 중 오류 발생")
            Message.err(self, "가입신청 실패", str(e))
            return

        Message.info(
            self,
            "가입신청 완료",
            "가입신청이 완료되었습니다.\n사업주 승인 후 로그인 가능합니다.",
        )
        self.signup_done.emit()


    # ?