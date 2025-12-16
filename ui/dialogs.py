# timeclock/ui/dialogs.py
# -*- coding: utf-8 -*-
from timeclock.settings import REASON_CODES, REQ_TYPES
from PyQt5 import QtWidgets, QtCore

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




class DisputeTimelineDialog(QtWidgets.QDialog):
    """
    [수정됨] 상단 정보창 고정(Fixed Header) + 채팅방 + 하단 입력창
    """

    def __init__(self, parent=None, db=None, user_id=None, dispute_id=None, my_role="worker"):
        super().__init__(parent)
        self.db = db
        self.user_id = user_id
        self.dispute_id = dispute_id
        self.my_role = my_role

        # DB에서 현재 상태 및 헤더 정보 조회
        self.current_status = "PENDING"
        self.header_info = {}
        self._load_data()

        self.setWindowTitle("이의 제기 대화방")
        self.resize(550, 800)

        # ---------------- 레이아웃 구성 ----------------
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. ★ 상단 고정 헤더 (정보창) ★
        # 스크롤 영역 밖에 배치하여 항상 보임
        self.header_widget = self._create_fixed_header()
        layout.addWidget(self.header_widget)

        # 2. 채팅 내용 표시 영역 (브라우저) - 여기가 스크롤됨
        self.browser = QtWidgets.QTextBrowser()
        self.browser.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.browser.setStyleSheet("background-color: #b2c7d9;")  # 카톡 배경색
        layout.addWidget(self.browser, 1)  # stretch=1 (남는 공간 다 차지)

        # 3. 하단 입력 영역 (흰색 배경)
        input_container = QtWidgets.QWidget()
        input_container.setStyleSheet("background-color: white; border-top: 1px solid #ddd;")
        input_layout = QtWidgets.QHBoxLayout(input_container)
        input_layout.setContentsMargins(10, 10, 10, 10)

        # [사업주 전용] 상태 변경 콤보박스
        self.cb_status = None
        if self.my_role == "owner":
            self.cb_status = QtWidgets.QComboBox()
            self.cb_status.addItem("검토 중", "IN_REVIEW")
            self.cb_status.addItem("처리 완료", "RESOLVED")
            self.cb_status.addItem("기각", "REJECTED")

            # 현재 상태에 맞춰 선택
            self._set_combo_index_by_data(self.current_status)

            self.cb_status.setMinimumHeight(35)
            input_layout.addWidget(self.cb_status)

        # 텍스트 입력창
        self.le_input = QtWidgets.QLineEdit()
        self.le_input.setPlaceholderText("메시지를 입력하세요...")
        self.le_input.setMinimumHeight(35)
        self.le_input.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.le_input, 1)

        # 전송 버튼
        self.btn_send = QtWidgets.QPushButton("전송")
        # noinspection PyUnresolvedReferences
        self.btn_send.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_send.setStyleSheet("""
            QPushButton {
                background-color: #fef01b; 
                color: #3c1e1e; 
                border: none; 
                border-radius: 4px;
                padding: 0 15px;
                font-weight: bold;
                height: 35px;
            }
            QPushButton:hover { background-color: #e5d817; }
        """)
        self.btn_send.clicked.connect(self.send_message)
        input_layout.addWidget(self.btn_send)

        layout.addWidget(input_container)
        self.setLayout(layout)

        # 채팅 데이터 로드
        self.refresh_timeline()

    def _load_data(self):
        """DB에서 상태와 헤더용 정보를 조회"""
        if not self.db or not self.dispute_id: return

        # 상태 조회
        row = self.db.conn.execute("SELECT request_id, dispute_type, status FROM disputes WHERE id=?",
                                   (self.dispute_id,)).fetchone()
        if row:
            self.current_status = row["status"]

            # 헤더용 요청 정보 조회
            req_id = row["request_id"]
            req_row = self.db.conn.execute("SELECT req_type, requested_at FROM requests WHERE id=?",
                                           (req_id,)).fetchone()

            self.header_info = {
                "request_id": req_id,
                "dispute_type": row["dispute_type"],
                "req_type": req_row["req_type"] if req_row else "-",
                "requested_at": req_row["requested_at"] if req_row else "-"
            }

    def _create_fixed_header(self):
        """상단에 고정될 정보창 위젯 생성"""
        widget = QtWidgets.QWidget()
        widget.setStyleSheet("background-color: #e2e2e2; border-bottom: 1px solid #c0c0c0;")

        vbox = QtWidgets.QVBoxLayout(widget)
        vbox.setContentsMargins(15, 10, 15, 10)
        vbox.setSpacing(4)

        # 데이터 가져오기
        r_id = self.header_info.get("request_id", "-")
        r_type_code = self.header_info.get("req_type", "-")
        r_type_label = dict(REQ_TYPES).get(r_type_code, r_type_code)
        r_time = self.header_info.get("requested_at", "-")
        d_type = self.header_info.get("dispute_type", "-")

        # 라벨 1: 대상 요청 정보
        lbl_req = QtWidgets.QLabel(f"<b>대상 요청:</b> {r_type_label} (ID: {r_id}) | <b>요청시각:</b> {r_time}")
        lbl_req.setStyleSheet("font-size: 13px; color: #333;")
        # noinspection PyUnresolvedReferences
        lbl_req.setAlignment(QtCore.Qt.AlignCenter)

        # 라벨 2: 최초 이의 유형
        lbl_type = QtWidgets.QLabel(f"<b>최초 이의 유형:</b> {d_type}")
        lbl_type.setStyleSheet("font-size: 13px; color: #d9534f;")  # 약간 붉은색 포인트
        # noinspection PyUnresolvedReferences
        lbl_type.setAlignment(QtCore.Qt.AlignCenter)

        vbox.addWidget(lbl_req)
        vbox.addWidget(lbl_type)

        return widget

    def _set_combo_index_by_data(self, status_code):
        if not self.cb_status: return
        idx = self.cb_status.findData(status_code)
        if idx >= 0:
            self.cb_status.setCurrentIndex(idx)
        else:
            self.cb_status.setCurrentIndex(0)

    def send_message(self):
        msg = self.le_input.text().strip()
        if not msg: return

        try:
            if self.my_role == "owner":
                new_status = self.cb_status.currentData()
                self.db.resolve_dispute(self.dispute_id, self.user_id, new_status, msg)
                self.current_status = new_status
            else:
                self.db.add_dispute_message(
                    self.dispute_id,
                    sender_user_id=self.user_id,
                    sender_role="worker",
                    message=msg
                )

            self.le_input.clear()
            self.refresh_timeline()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "오류", f"전송 실패: {e}")

    def refresh_timeline(self):
        try:
            timeline_events = self.db.get_dispute_timeline(self.dispute_id)
        except Exception:
            return

        html_content = []

        KAKAO_BG = "#b2c7d9"
        MY_BUBBLE = "#fef01b"
        OTHER_BUBBLE = "#ffffff"

        html_content.append(f"""
        <html><head><style>
            body {{ background-color: {KAKAO_BG}; font-family: 'Malgun Gothic', sans-serif; margin: 0; padding: 15px; }}
            .bubble {{
                display: inline-block; padding: 8px 12px; border-radius: 12px;
                font-size: 14px; color: #000; line-height: 140%;
                box-shadow: 1px 1px 2px rgba(0,0,0,0.1); max-width: 100%; word-wrap: break-word;
            }}
            .name {{ font-size: 12px; color: #555; margin-bottom: 4px; margin-left: 2px; }}
            .time {{ font-size: 10px; color: #555; margin-top: 2px; }}
            table {{ width: 100%; border-spacing: 0; table-layout: fixed; }}
            td {{ padding-bottom: 8px; vertical-align: top; }}

            .system-msg {{ text-align: center; margin-top: 20px; margin-bottom: 20px; }}
            .system-box {{
                display: inline-block; background-color: rgba(0,0,0,0.1); 
                color: #fff; font-size: 12px; padding: 6px 15px; border-radius: 20px;
            }}
        </style></head><body>

        <table border="0" cellspacing="0" cellpadding="0">
            <tr><td width="40%"></td><td width="20%"></td><td width="40%"></td></tr>
        """)

        for event in timeline_events:
            who = event.get("who", "unknown")
            username = event.get("username", "")
            at = event.get("at", "") or ""
            comment = event.get("comment", "") or ""

            safe_comment = comment.replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')
            if not safe_comment.strip(): continue

            is_me = (who == self.my_role)

            if is_me:
                html_content.append(f"""
                <tr>
                    <td></td><td></td>
                    <td align="right">
                        <div style="text-align: right;">
                            <span class="bubble" style="background-color: {MY_BUBBLE}; text-align: left;">{safe_comment}</span>
                            <div class="time">{at}</div>
                        </div>
                    </td>
                </tr>
                """)
            else:
                html_content.append(f"""
                <tr>
                    <td align="left">
                        <div style="text-align: left;">
                            <div class="name">{username}</div>
                            <span class="bubble" style="background-color: {OTHER_BUBBLE};">{safe_comment}</span>
                            <div class="time">{at}</div>
                        </div>
                    </td>
                    <td></td><td></td>
                </tr>
                """)

        html_content.append("</table>")

        if self.current_status == "RESOLVED":
            html_content.append("""<div class="system-msg"><span class="system-box">처리 완료된 이의제기입니다.</span></div>""")
        elif self.current_status == "REJECTED":
            html_content.append("""<div class="system-msg"><span class="system-box">기각 처리된 이의제기입니다.</span></div>""")

        html_content.append("<br></body></html>")

        slider = self.browser.verticalScrollBar()
        old_val = slider.value()
        is_bottom = (old_val >= slider.maximum() - 10)

        self.browser.setHtml("".join(html_content))

        if is_bottom:
            slider.setValue(slider.maximum())
        else:
            slider.setValue(old_val)


