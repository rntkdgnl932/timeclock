# timeclock/ui/dialogs.py
# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets, QtCore, QtGui


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
        if not p1 or len(p1) < 4:
            return None
        if p1 != p2:
            return None
        return p1


# ==========================================================
# ★ [최종 수정] 이의 제기 대화방 (중첩 테이블로 강제 줄바꿈)
# ==========================================================
class DisputeTimelineDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, db=None, user_id=None, dispute_id=None, my_role="worker"):
        super().__init__(parent)
        self.db = db
        self.user_id = user_id
        self.dispute_id = dispute_id
        self.my_role = my_role

        self.current_status = "PENDING"
        self.header_info = {}
        self._load_data()

        self.setWindowTitle("이의 제기 대화방")
        self.resize(550, 800)

        # ---------------- 레이아웃 구성 ----------------
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 1. 상단 고정 헤더
        self.header_widget = self._create_fixed_header()
        layout.addWidget(self.header_widget)

        # 2. 채팅 브라우저
        self.browser = QtWidgets.QTextBrowser()
        self.browser.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.browser.setStyleSheet("background-color: #b2c7d9;")
        layout.addWidget(self.browser, 1)

        # 3. 하단 입력창
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
            self._set_combo_index_by_data(self.current_status)
            self.cb_status.setMinimumHeight(35)
            input_layout.addWidget(self.cb_status)

        self.le_input = QtWidgets.QLineEdit()
        self.le_input.setPlaceholderText("메시지를 입력하세요...")
        self.le_input.setMinimumHeight(35)
        self.le_input.returnPressed.connect(self.send_message)
        input_layout.addWidget(self.le_input, 1)

        self.btn_send = QtWidgets.QPushButton("전송")
        self.btn_send.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_send.setStyleSheet("""
            QPushButton {
                background-color: #fef01b; color: #3c1e1e; border: none; 
                border-radius: 4px; padding: 0 15px; font-weight: bold; height: 35px;
            }
            QPushButton:hover { background-color: #e5d817; }
        """)
        self.btn_send.clicked.connect(self.send_message)
        input_layout.addWidget(self.btn_send)

        layout.addWidget(input_container)
        self.setLayout(layout)

        self.refresh_timeline()

    def _load_data(self):
        if not self.db or not self.dispute_id: return

        row = self.db.conn.execute(
            "SELECT work_log_id, dispute_type, status FROM disputes WHERE id=?",
            (self.dispute_id,)
        ).fetchone()

        if row:
            self.current_status = row["status"]
            wl_id = row["work_log_id"]

            wl_row = self.db.conn.execute(
                "SELECT work_date, start_time, end_time FROM work_logs WHERE id=?",
                (wl_id,)
            ).fetchone()

            self.header_info = {
                "work_date": wl_row["work_date"] if wl_row else "-",
                "dispute_type": row["dispute_type"],
                "start_time": wl_row["start_time"] if wl_row else "-",
                "end_time": wl_row["end_time"] if wl_row else "-"
            }

    def _create_fixed_header(self):
        widget = QtWidgets.QWidget()
        widget.setStyleSheet("background-color: #e2e2e2; border-bottom: 1px solid #c0c0c0;")

        vbox = QtWidgets.QVBoxLayout(widget)
        vbox.setContentsMargins(15, 10, 15, 10)
        vbox.setSpacing(4)

        w_date = self.header_info.get("work_date", "-")
        d_type = self.header_info.get("dispute_type", "-")

        lbl_info = QtWidgets.QLabel(f"<b>근무 일자:</b> {w_date}")
        lbl_info.setStyleSheet("font-size: 14px; color: #333;")
        lbl_info.setAlignment(QtCore.Qt.AlignCenter)

        lbl_type = QtWidgets.QLabel(f"<b>이의 유형:</b> {d_type}")
        lbl_type.setStyleSheet("font-size: 13px; color: #d9534f;")
        lbl_type.setAlignment(QtCore.Qt.AlignCenter)

        vbox.addWidget(lbl_info)
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

        # 카톡 스타일 색상
        KAKAO_BG = "#b2c7d9"
        MY_BUBBLE = "#fef01b"
        OTHER_BUBBLE = "#ffffff"

        html_content.append(f"""
        <html><head><style>
            body {{ background-color: {KAKAO_BG}; font-family: 'Malgun Gothic', sans-serif; margin: 0; padding: 15px; }}
            .time {{ font-size: 10px; color: #555; margin-top: 4px; }}
        </style></head><body>

        <table width="100%" border="0" cellspacing="0" cellpadding="0">
            <tr>
                <td width="45%"></td>
                <td width="10%"></td>
                <td width="45%"></td>
            </tr>
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
                # [나] - 오른쪽 정렬 (이름 숨김)
                # 여기도 중첩 테이블을 써서 정렬을 확실하게 잡음
                html_content.append(f"""
                <tr>
                    <td colspan="2"></td>
                    <td align="right" valign="top">
                        <table border="0" cellspacing="0" cellpadding="0">
                            <tr>
                                <td align="right">
                                    <div style="background-color: {MY_BUBBLE}; padding: 8px 12px; font-size: 14px; color: #000; border-radius: 4px; display: inline-block;">
                                        {safe_comment}
                                    </div>
                                    <div class="time">{at}</div>
                                </td>
                            </tr>
                        </table>
                        <br>
                    </td>
                </tr>
                """)
            else:
                # [상대방] - 왼쪽 정렬
                # ★ 중첩 테이블 사용: 1행(이름) -> 2행(말풍선) -> 3행(시간)
                # 이렇게 하면 절대로 옆으로 붙을 수가 없음
                html_content.append(f"""
                <tr>
                    <td align="left" valign="top">
                        <table border="0" cellspacing="0" cellpadding="0">
                            <tr>
                                <td align="left" style="padding-bottom: 4px;">
                                    <span style="font-size: 13px; font-weight: bold; color: #4b4b4b;">{username}</span>
                                </td>
                            </tr>
                            <tr>
                                <td align="left">
                                    <div style="background-color: {OTHER_BUBBLE}; padding: 8px 12px; font-size: 14px; color: #000; border-radius: 4px; display: inline-block;">
                                        {safe_comment}
                                    </div>
                                </td>
                            </tr>
                            <tr>
                                <td align="left">
                                    <div class="time">{at}</div>
                                </td>
                            </tr>
                        </table>
                        <br>
                    </td>
                    <td colspan="2"></td>
                </tr>
                """)

        html_content.append("</table>")

        # 시스템 메시지
        if self.current_status == "RESOLVED":
            html_content.append(
                """<div style="text-align: center; margin: 20px;"><span style="background-color: rgba(0,0,0,0.1); color: #fff; padding: 6px 15px; border-radius: 10px; font-size: 12px;">처리 완료된 이의제기입니다.</span></div>""")
        elif self.current_status == "REJECTED":
            html_content.append(
                """<div style="text-align: center; margin: 20px;"><span style="background-color: rgba(0,0,0,0.1); color: #fff; padding: 6px 15px; border-radius: 10px; font-size: 12px;">기각 처리된 이의제기입니다.</span></div>""")

        html_content.append("<br></body></html>")

        self.browser.setHtml("".join(html_content))

        # 스크롤 아래로
        slider = self.browser.verticalScrollBar()
        slider.setValue(slider.maximum())