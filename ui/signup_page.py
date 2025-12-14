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
        title = QtWidgets.QLabel("직원 가입 신청")
        f = title.font()
        f.setPointSize(14)
        f.setBold(True)
        title.setFont(f)

        form = QtWidgets.QFormLayout()

        # ---------- ID + 중복확인 ----------
        self.ed_id = QtWidgets.QLineEdit()
        self.btn_check_id = QtWidgets.QPushButton("중복확인")
        self.btn_check_id.clicked.connect(self.check_id)

        id_row = QtWidgets.QHBoxLayout()
        id_row.addWidget(self.ed_id)
        id_row.addWidget(self.btn_check_id)
        form.addRow("아이디(ID) *", id_row)

        # ---------- PW ----------
        self.ed_pw = QtWidgets.QLineEdit()
        self.ed_pw.setEchoMode(QtWidgets.QLineEdit.Password)
        form.addRow("비밀번호 *", self.ed_pw)

        self.ed_pw2 = QtWidgets.QLineEdit()
        self.ed_pw2.setEchoMode(QtWidgets.QLineEdit.Password)
        form.addRow("비밀번호 확인 *", self.ed_pw2)

        # ---------- 전화번호 3칸 ----------
        self.ed_phone1 = QtWidgets.QLineEdit()
        self.ed_phone2 = QtWidgets.QLineEdit()
        self.ed_phone3 = QtWidgets.QLineEdit()

        self.ed_phone1.setMaxLength(3)
        self.ed_phone2.setMaxLength(4)
        self.ed_phone3.setMaxLength(4)

        for ed in (self.ed_phone1, self.ed_phone2, self.ed_phone3):
            ed.setFixedWidth(60)
            # noinspection PyUnresolvedReferences
            # 🌟 수정됨: QtGui.QIntValidator() 사용
            ed.setValidator(QtGui.QIntValidator())

        phone_row = QtWidgets.QHBoxLayout()
        phone_row.addWidget(self.ed_phone1)
        phone_row.addWidget(QtWidgets.QLabel("-"))
        phone_row.addWidget(self.ed_phone2)
        phone_row.addWidget(QtWidgets.QLabel("-"))
        phone_row.addWidget(self.ed_phone3)
        phone_row.addStretch(1)

        form.addRow("전화번호 *", phone_row)

        # ---------- 생년월일 3칸 ----------
        self.ed_birth_y = QtWidgets.QLineEdit()
        self.ed_birth_m = QtWidgets.QLineEdit()
        self.ed_birth_d = QtWidgets.QLineEdit()

        self.ed_birth_y.setPlaceholderText("YYYY")
        self.ed_birth_m.setPlaceholderText("MM")
        self.ed_birth_d.setPlaceholderText("DD")

        self.ed_birth_y.setMaxLength(4)
        self.ed_birth_m.setMaxLength(2)
        self.ed_birth_d.setMaxLength(2)

        for ed in (self.ed_birth_y, self.ed_birth_m, self.ed_birth_d):
            ed.setFixedWidth(60)
            # noinspection PyUnresolvedReferences
            # 🌟 수정됨: QtGui.QIntValidator() 사용
            ed.setValidator(QtGui.QIntValidator())

        birth_row = QtWidgets.QHBoxLayout()
        birth_row.addWidget(self.ed_birth_y)
        birth_row.addWidget(QtWidgets.QLabel("-"))
        birth_row.addWidget(self.ed_birth_m)
        birth_row.addWidget(QtWidgets.QLabel("-"))
        birth_row.addWidget(self.ed_birth_d)
        birth_row.addStretch(1)

        form.addRow("생년월일 *", birth_row)

        # ---------- 선택 입력 ----------
        self.ed_email = QtWidgets.QLineEdit()
        form.addRow("이메일", self.ed_email)

        self.ed_bank = QtWidgets.QLineEdit()
        form.addRow("계좌정보", self.ed_bank)

        self.ed_addr = QtWidgets.QLineEdit()
        form.addRow("주소", self.ed_addr)

        # ---------- 버튼 ----------
        btn_apply = QtWidgets.QPushButton("가입신청")
        btn_cancel = QtWidgets.QPushButton("취소")

        btn_apply.clicked.connect(self.submit)
        btn_cancel.clicked.connect(self.signup_done.emit)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_cancel)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(title)
        layout.addSpacing(10)
        layout.addLayout(form)
        layout.addSpacing(10)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    # ---------------- Logic ----------------

    def check_id(self):
        username = self.ed_id.text().strip()

        if not ID_PATTERN.match(username):
            Message.err(self, "ID 확인", "ID는 영문/숫자/_ 4~20자만 가능합니다.")
            return

        # db.py에 check_username_available 메서드가 STEP 3에 정의되었다고 가정합니다.
        # STEP 3의 db.py 코드를 확인했을 때 해당 메서드가 존재했습니다.
        ok, reason = self.db.check_username_available(username)
        if ok:
            self._id_checked_ok = True
            self._last_checked_username = username
            Message.info(self, "ID 확인", "사용 가능한 ID입니다.")
        else:
            self._id_checked_ok = False
            Message.err(self, "ID 확인", reason)

    def submit(self):
        # ---------- 필수값 ----------
        username = self.ed_id.text().strip()
        pw = self.ed_pw.text()
        pw2 = self.ed_pw2.text()

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