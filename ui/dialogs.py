# timeclock/ui/dialogs.py
# -*- coding: utf-8 -*-
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
        # noinspection PyUnresolvedReferences
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
        # noinspection PyUnresolvedReferences
        lbl_info.setAlignment(QtCore.Qt.AlignCenter)

        lbl_type = QtWidgets.QLabel(f"<b>이의 유형:</b> {d_type}")
        lbl_type.setStyleSheet("font-size: 13px; color: #d9534f;")
        # noinspection PyUnresolvedReferences
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

        KAKAO_BG = "#B2C7D9"
        MY_BUBBLE = "#FEE500"
        OTHER_BUBBLE = "#FFFFFF"
        TIME_COLOR = "#666666"

        # 좌/우 정렬을 안정적으로 만들기 위한 스페이서
        SPACER_W = "45%"

        def esc(s: str) -> str:
            if s is None:
                return ""
            s = str(s)
            s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            s = s.replace("\n", "<br>")
            return s

        def date_only(ts: str) -> str:
            if not ts:
                return ""
            return ts.split(" ")[0].strip()

        def time_only(ts: str) -> str:
            if not ts:
                return ""
            parts = ts.split(" ")
            if len(parts) < 2:
                return ts
            t = parts[1]
            return t[:5] if len(t) >= 5 else t

        def date_chip(d: str) -> str:
            d = esc(d)
            return f"""
            <div align="center" style="margin:10px 0 14px 0;">
              <span style="background:#D7E2EC; color:#333; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:bold;">
                {d}
              </span>
            </div>
            """

        def sys_chip(msg: str) -> str:
            msg = esc(msg)
            return f"""
            <div align="center" style="margin:18px 0 6px 0;">
              <span style="background:#90A4AE; color:#fff; padding:5px 12px; border-radius:14px; font-size:12px; font-weight:bold;">
                {msg}
              </span>
            </div>
            """

        def bubble_html(text: str, t: str, bg: str, align: str) -> str:
            text = esc(text).strip()
            t = esc(t)
            if not text:
                return ""

            # 말풍선(사각형 + padding 중심). Qt RichText 한계상 둥글림은 기대하지 않음.
            bubble_div = (
                f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;">'
                f'  <tr>'
                f'    <td bgcolor="{bg}" style="padding:10px 14px; border:1px solid #E6E6E6;">'
                f'      <div style="font-size:14px; color:#111; line-height:1.45;">{text}</div>'
                f'    </td>'
                f'  </tr>'
                f'</table>'
            )

            # ✅ 시간 정렬: 상대(왼쪽)는 왼쪽 정렬, 내(오른쪽)는 오른쪽 정렬
            time_align = "right" if align == "right" else "left"
            time_td_style = f'font-size:10px; color:{TIME_COLOR}; padding-top:4px;'

            # 메시지 + 시간(말풍선 아래)
            if align == "right":
                return f"""
                <table width="100%" cellspacing="0" cellpadding="0" style="margin:10px 0;">
                  <tr>
                    <td width="{SPACER_W}"></td>
                    <td align="right" valign="top">
                      {bubble_div}
                      <table width="100%" cellspacing="0" cellpadding="0">
                        <tr><td align="{time_align}" style="{time_td_style}">{t}</td></tr>
                      </table>
                    </td>
                  </tr>
                </table>
                """
            else:
                return f"""
                <table width="100%" cellspacing="0" cellpadding="0" style="margin:10px 0;">
                  <tr>
                    <td align="left" valign="top">
                      {bubble_div}
                      <table width="100%" cellspacing="0" cellpadding="0">
                        <tr><td align="{time_align}" style="{time_td_style}">{t}</td></tr>
                      </table>
                    </td>
                    <td width="{SPACER_W}"></td>
                  </tr>
                </table>
                """

        html = []
        html.append(f"""
        <html><body style="background:{KAKAO_BG}; font-family:'Malgun Gothic','Segoe UI',sans-serif; margin:0; padding:12px 12px 18px 12px;">
        """)

        last_date = None

        for event in timeline_events:
            who = event.get("who", "unknown")
            username = event.get("username", "") or ""
            at = event.get("at", "") or ""
            comment = event.get("comment", "") or ""

            if not comment:
                continue

            d = date_only(at)
            if d and d != last_date:
                html.append(date_chip(d))
                last_date = d

            is_me = (who == self.my_role)
            t = time_only(at)

            if is_me:
                html.append(bubble_html(comment, t, MY_BUBBLE, "right"))
            else:
                # 상대는 이름을 위에 표시(원하면 제거 가능)
                if username:
                    html.append(
                        f'<div style="font-size:12px; font-weight:bold; color:#1f2a33; margin:0 0 4px 2px;">{esc(username)}</div>')
                html.append(bubble_html(comment, t, OTHER_BUBBLE, "left"))

        if self.current_status == "RESOLVED":
            html.append(sys_chip("처리 완료된 이의제기입니다."))
        elif self.current_status == "REJECTED":
            html.append(sys_chip("기각 처리된 이의제기입니다."))
        elif self.current_status == "IN_REVIEW":
            html.append(sys_chip("현재 검토 중입니다."))

        html.append("</body></html>")
        self.browser.setHtml("".join(html))

        sb = self.browser.verticalScrollBar()
        sb.setValue(sb.maximum())


# timeclock/ui/dialogs.py 파일 맨 아래에 추가하세요.

class DateRangeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("기간 선택")
        self.resize(300, 150)

        layout = QtWidgets.QVBoxLayout()

        # 설명 라벨
        lbl_guide = QtWidgets.QLabel("급여를 정산할 기간을 선택하세요.")
        # noinspection PyUnresolvedReferences
        lbl_guide.setAlignment(QtCore.Qt.AlignCenter)
        lbl_guide.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(lbl_guide)

        # 폼 레이아웃 (시작일, 종료일)
        form = QtWidgets.QFormLayout()

        # 오늘 날짜 기준
        now = QtCore.QDate.currentDate()
        first_day = QtCore.QDate(now.year(), now.month(), 1)

        # 시작일 위젯 (달력 팝업 활성화)
        self.de_start = QtWidgets.QDateEdit()
        self.de_start.setCalendarPopup(True)  # ★ 핵심: 달력 팝업 켜기
        self.de_start.setDisplayFormat("yyyy-MM-dd")
        self.de_start.setDate(first_day)  # 이번달 1일 기본값

        # 종료일 위젯
        self.de_end = QtWidgets.QDateEdit()
        self.de_end.setCalendarPopup(True)  # ★ 핵심
        self.de_end.setDisplayFormat("yyyy-MM-dd")
        self.de_end.setDate(now)  # 오늘 날짜 기본값

        form.addRow("시작일:", self.de_start)
        form.addRow("종료일:", self.de_end)

        layout.addLayout(form)

        # 버튼 (확인/취소)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.setLayout(layout)

    def get_range(self):
        # 문자열(YYYY-MM-DD) 형태로 반환
        s = self.de_start.date().toString("yyyy-MM-dd")
        e = self.de_end.date().toString("yyyy-MM-dd")
        return s, e


