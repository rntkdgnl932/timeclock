# timeclock/ui/owner_page.py
# -*- coding: utf-8 -*-
import logging
from PyQt5 import QtWidgets, QtCore

from timeclock.utils import Message, now_str
from timeclock.settings import WORK_STATUS  # ★ [수정] 설정 파일에서 상태값 가져옴
from ui.widgets import DateRangeBar, Table
from ui.dialogs import ChangePasswordDialog, DisputeTimelineDialog
from timeclock.settings import WORK_STATUS, SIGNUP_STATUS


class OwnerPage(QtWidgets.QWidget):
    logout_requested = QtCore.pyqtSignal()

    def __init__(self, db, session, parent=None):
        super().__init__(parent)
        self.db = db
        self.session = session

        self._dispute_rows = []
        self._work_rows = []
        self._member_rows = []

        header = QtWidgets.QLabel(f"사업주 화면 - {session.username}")
        f = header.font()
        f.setPointSize(14)
        f.setBold(True)
        header.setFont(f)

        self.btn_change_pw = QtWidgets.QPushButton("비밀번호 변경")
        self.btn_logout = QtWidgets.QPushButton("로그아웃")
        self.btn_change_pw.clicked.connect(self.change_password)
        self.btn_logout.clicked.connect(self.logout_requested.emit)

        top_btns = QtWidgets.QHBoxLayout()
        top_btns.addStretch(1)
        top_btns.addWidget(self.btn_change_pw)
        top_btns.addWidget(self.btn_logout)

        # 탭 구성
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_work_log_tab(), "근무 기록 관리 (승인)")
        self.tabs.addTab(self._build_member_tab(), "회원(급여) 관리")
        self.tabs.addTab(self._build_dispute_tab(), "이의 제기 관리")
        self.tabs.addTab(self._build_signup_tab(), "가입 신청 관리")

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(header)
        layout.addLayout(top_btns)
        layout.addWidget(self.tabs)

        self.setLayout(layout)

        self.refresh_work_logs()
        self.refresh_members()
        self.refresh_disputes()
        self.refresh_signup_requests()

    # ==========================================================
    # 1. 근무 기록 관리 탭
    # ==========================================================
    def _build_work_log_tab(self):
        self.filter_work = DateRangeBar(label="조회기간")
        self.filter_work.applied.connect(lambda *_: self.refresh_work_logs())

        self.btn_work_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_work_refresh.clicked.connect(self.refresh_work_logs)

        self.btn_edit_start = QtWidgets.QPushButton("출근 승인/수정")
        self.btn_edit_start.setStyleSheet("font-weight: bold; color: #004d40; background-color: #e0f2f1;")
        self.btn_edit_start.clicked.connect(lambda: self.approve_selected_log(mode="START"))

        self.btn_edit_end = QtWidgets.QPushButton("퇴근 승인/수정")
        self.btn_edit_end.setStyleSheet("font-weight: bold; color: #b71c1c; background-color: #ffebee;")
        self.btn_edit_end.clicked.connect(lambda: self.approve_selected_log(mode="END"))

        self.work_table = Table([
            "ID", "일자", "근로자", "출근(요청)", "퇴근(요청)", "상태",
            "확정 출근", "확정 퇴근", "비고(코멘트)"
        ])
        self.work_table.setColumnWidth(0, 0)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addWidget(self.btn_work_refresh)
        btn_layout.addSpacing(20)
        btn_layout.addWidget(self.btn_edit_start)
        btn_layout.addWidget(self.btn_edit_end)
        btn_layout.addStretch(1)

        l = QtWidgets.QVBoxLayout()
        l.addWidget(self.filter_work)
        l.addLayout(btn_layout)
        l.addWidget(QtWidgets.QLabel("※ 출근 시간만 고치려면 '출근 승인', 퇴근까지 확정하려면 '퇴근 승인'을 누르세요."))
        l.addWidget(self.work_table)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w

    def refresh_work_logs(self):
        d1, d2 = self.filter_work.get_range()
        try:
            rows = self.db.list_all_work_logs(None, d1, d2)
            self._work_rows = rows

            out = []
            for r in rows:
                rr = dict(r)
                st = rr["status"]
                # ★ [수정] settings.py 의 WORK_STATUS 사용 (중복 코드 제거됨)
                st_str = WORK_STATUS.get(st, st)

                out.append([
                    str(rr["id"]),
                    rr["work_date"],
                    rr["worker_username"],
                    rr["start_time"] or "",
                    rr["end_time"] or "",
                    st_str,
                    rr["approved_start"] or "",
                    rr["approved_end"] or "",
                    rr["owner_comment"] or ""
                ])
            self.work_table.set_rows(out)
        except Exception as e:
            logging.exception("refresh_work_logs failed")
            Message.err(self, "오류", f"근무 기록 조회 실패: {e}")

    def approve_selected_log(self, mode="START"):
        row_idx = self.work_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "알림", "목록에서 근무 기록을 먼저 선택하세요.")
            return

        if row_idx >= len(self._work_rows): return
        target_row = dict(self._work_rows[row_idx])

        dlg = WorkLogApproveDialog(self, target_row, mode=mode)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            app_start, app_end, comment = dlg.get_data()
            try:
                self.db.approve_work_log(
                    target_row["id"],
                    self.session.user_id,
                    app_start, app_end, comment
                )
                msg = "출근 시간이 수정되었습니다." if mode == "START" else "퇴근 승인(수정)이 완료되었습니다."
                Message.info(self, "성공", msg)
                self.refresh_work_logs()
            except Exception as e:
                Message.err(self, "오류", f"처리 중 오류: {e}")

    # ==========================================================
    # 2. 회원(급여) 관리 탭
    # ==========================================================
    def _build_member_tab(self):
        # 상단 필터 영역
        self.le_member_search = QtWidgets.QLineEdit()
        self.le_member_search.setPlaceholderText("이름 검색...")
        self.le_member_search.returnPressed.connect(self.refresh_members) # 엔터 치면 검색

        self.cb_member_filter = QtWidgets.QComboBox()
        self.cb_member_filter.addItem("재직자 보기", "ACTIVE")
        self.cb_member_filter.addItem("퇴사자 보기", "INACTIVE")
        self.cb_member_filter.addItem("전체 보기", "ALL")
        self.cb_member_filter.currentIndexChanged.connect(self.refresh_members)

        self.btn_member_search = QtWidgets.QPushButton("검색")
        self.btn_member_search.clicked.connect(self.refresh_members)

        # 기능 버튼 영역
        self.btn_edit_wage = QtWidgets.QPushButton("시급 변경")
        self.btn_edit_wage.setStyleSheet("background-color: #E3F2FD; color: #0D47A1;")
        self.btn_edit_wage.clicked.connect(self.edit_wage)

        self.btn_resign = QtWidgets.QPushButton("퇴사 처리")
        self.btn_resign.setStyleSheet("background-color: #ffebee; color: #b71c1c;")
        self.btn_resign.clicked.connect(self.resign_worker)

        # 테이블
        self.member_table = Table([
            "ID", "아이디", "성함", "전화번호", "생년월일", "시급", "가입일", "상태"
        ])
        self.member_table.setColumnWidth(0, 0)
        self.member_table.itemDoubleClicked.connect(self.edit_wage)

        # 레이아웃 배치
        top_filter = QtWidgets.QHBoxLayout()
        top_filter.addWidget(self.le_member_search)
        top_filter.addWidget(self.cb_member_filter)
        top_filter.addWidget(self.btn_member_search)
        top_filter.addStretch(1)
        top_filter.addWidget(self.btn_edit_wage)
        top_filter.addWidget(self.btn_resign)

        l = QtWidgets.QVBoxLayout()
        l.addLayout(top_filter)
        l.addWidget(self.member_table)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w


    def refresh_members(self):
        keyword = self.le_member_search.text().strip()
        status_filter = self.cb_member_filter.currentData()

        try:
            rows = self.db.list_workers(keyword=keyword, status_filter=status_filter)
            self._member_rows = rows
            out = []
            for r in rows:
                rr = dict(r)
                wage_str = f"{rr['hourly_wage']:,}" if rr['hourly_wage'] else "0"
                status = "재직중" if rr['is_active'] else "퇴사"

                # [수정] 데이터 매핑 (없는 경우 빈칸 처리)
                out.append([
                    str(rr['id']),
                    rr['username'],
                    rr.get('name') or "",  # 성함
                    rr.get('phone') or "",  # 전화번호
                    rr.get('birthdate') or "",  # 생년월일
                    wage_str,
                    rr['created_at'],
                    status
                ])
            self.member_table.set_rows(out)
        except Exception as e:
            Message.err(self, "오류", f"회원 목록 로드 실패: {e}")

    def resign_worker(self):
        """퇴사 처리 버튼 핸들러"""
        row = self.member_table.selected_first_row_index()
        if row < 0:
            Message.warn(self, "알림", "퇴사 처리할 직원을 선택하세요.")
            return

        rr = dict(self._member_rows[row])
        user_id = rr['id']
        username = rr['username']
        is_active = rr['is_active']

        if is_active == 0:
            Message.warn(self, "알림", "이미 퇴사 처리된 직원입니다.")
            return

        if Message.confirm(self, "퇴사 확인", f"정말 '{username}' 님을 퇴사 처리하시겠습니까?\n(계정은 삭제되지 않고 비활성화됩니다)"):
            try:
                self.db.resign_user(user_id)
                Message.info(self, "완료", "퇴사 처리가 완료되었습니다.")
                self.refresh_members()
            except Exception as e:
                Message.err(self, "오류", str(e))

    def edit_wage(self):
        row = self.member_table.selected_first_row_index()
        if row < 0:
            Message.warn(self, "알림", "시급을 변경할 회원을 선택하세요.")
            return

        rr = dict(self._member_rows[row])
        user_id = rr['id']
        username = rr['username']
        current_wage = rr['hourly_wage'] or 9860

        val, ok = QtWidgets.QInputDialog.getInt(
            self, "시급 변경",
            f"'{username}' 님의 새로운 시급을 입력하세요:",
            current_wage, 0, 1000000, 10
        )
        if ok:
            try:
                self.db.update_user_wage(user_id, val)
                Message.info(self, "완료", f"{username}님의 시급이 {val:,}원으로 변경되었습니다.")
                self.refresh_members()
            except Exception as e:
                Message.err(self, "오류", str(e))

    # ==========================================================
    # 3. 이의 제기 탭
    # ==========================================================
    def _build_dispute_tab(self):
        self.filter_disputes = DateRangeBar(label="이의제기 조회기간")
        self.filter_disputes.applied.connect(lambda *_: self.refresh_disputes())

        self.cb_dispute_filter = QtWidgets.QComboBox()
        self.cb_dispute_filter.addItem("진행 중 (검토/미처리)", "ACTIVE")
        self.cb_dispute_filter.addItem("종료 (완료/기각)", "CLOSED")
        self.cb_dispute_filter.currentIndexChanged.connect(lambda *_: self.refresh_disputes())

        self.btn_disputes_refresh = QtWidgets.QPushButton("조회")
        self.btn_disputes_refresh.clicked.connect(self.refresh_disputes)

        self.btn_open_chat = QtWidgets.QPushButton("선택 건 채팅방 열기")
        self.btn_open_chat.clicked.connect(self.open_dispute_chat)

        self.dispute_table = Table([
            "ID", "근로자", "근무일자", "이의유형", "상태", "최근대화", "등록일"
        ])
        self.dispute_table.setColumnWidth(0, 0)
        QtCore.QTimer.singleShot(0, self._wire_dispute_doubleclick)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.filter_disputes)
        top.addWidget(self.cb_dispute_filter)
        top.addWidget(self.btn_disputes_refresh)
        top.addStretch(1)

        l = QtWidgets.QVBoxLayout()
        l.addLayout(top)
        l.addWidget(self.dispute_table)
        l.addWidget(self.btn_open_chat)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w

    def refresh_disputes(self):
        d1, d2 = self.filter_disputes.get_range()
        filter_type = self.cb_dispute_filter.currentData()

        try:
            rows = self.db.list_disputes(d1, d2, filter_type)
            self._dispute_rows = rows

            out = []
            for r in rows:
                rr = dict(r)
                st = rr["status"]
                st_map = {"PENDING": "미처리", "IN_REVIEW": "검토중", "RESOLVED": "완료", "REJECTED": "기각"}

                summary = (rr["comment"] or "").replace("\n", " ")
                if len(summary) > 30: summary = summary[:30] + "..."

                out.append([
                    str(rr["id"]),
                    rr["worker_username"],
                    rr["work_date"],
                    rr["dispute_type"],
                    st_map.get(st, st),
                    summary,
                    rr["created_at"]
                ])
            self.dispute_table.set_rows(out)
        except Exception as e:
            logging.exception("refresh_disputes failed")
            Message.err(self, "오류", f"이의제기 로드 실패: {e}")

    def _wire_dispute_doubleclick(self):
        try:
            self.dispute_table.itemDoubleClicked.disconnect()
        except:
            pass
        self.dispute_table.itemDoubleClicked.connect(self.open_dispute_chat)

    def open_dispute_chat(self):
        row = self.dispute_table.selected_first_row_index()
        if row < 0 or row >= len(self._dispute_rows):
            Message.warn(self, "알림", "목록에서 항목을 선택하세요.")
            return

        rr = dict(self._dispute_rows[row])
        dispute_id = int(rr["id"])

        dlg = DisputeTimelineDialog(
            parent=self,
            db=self.db,
            user_id=self.session.user_id,
            dispute_id=dispute_id,
            my_role="owner"
        )
        dlg.exec_()
        self.refresh_disputes()

    # ==========================================================
    # 4. 가입 신청 관리
    # ==========================================================
    def _build_signup_tab(self):
        self.btn_approve_signup = QtWidgets.QPushButton("선택 가입 승인")
        self.btn_reject_signup = QtWidgets.QPushButton("선택 가입 거절")
        self.btn_refresh_signup = QtWidgets.QPushButton("새로고침")

        self.btn_approve_signup.clicked.connect(self.approve_signup)
        self.btn_reject_signup.clicked.connect(self.reject_signup)
        self.btn_refresh_signup.clicked.connect(self.refresh_signup_requests)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.btn_approve_signup)
        top.addWidget(self.btn_reject_signup)
        top.addWidget(self.btn_refresh_signup)
        top.addStretch(1)

        self.signup_table = Table(["ID", "신청ID", "전화번호", "생년월일", "신청일", "상태"])
        self.signup_table.setColumnWidth(0, 0)

        l = QtWidgets.QVBoxLayout()
        l.addLayout(top)
        l.addWidget(self.signup_table)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w

    def refresh_signup_requests(self):
        try:
            rows = self.db.list_pending_signup_requests()
            data = []

            for r in rows:
                rr = dict(r)
                phone = rr.get("phone", "")

                # DB의 영어 상태값
                raw_status = rr["status"]

                # [수정] settings.py에서 가져온 표를 사용 (없으면 영어 그대로 표시)
                status_str = SIGNUP_STATUS.get(raw_status, raw_status)

                data.append([
                    rr["id"],
                    rr["username"],
                    phone,
                    rr["birthdate"],
                    rr["created_at"],
                    status_str  # 한글로 변환된 값
                ])
            self.signup_table.set_rows(data)
        except Exception as e:
            Message.err(self, "오류", str(e))

    def approve_signup(self):
        row = self.signup_table.selected_first_row_index()
        if row < 0: return
        sid = int(self.signup_table.get_cell(row, 0))
        name = self.signup_table.get_cell(row, 1)

        if Message.confirm(self, "승인", f"'{name}'님의 가입을 승인하시겠습니까?"):
            try:
                self.db.approve_signup_request(sid, self.session.user_id, "Approved")
                Message.info(self, "완료", "계정이 생성되었습니다.")
                self.refresh_signup_requests()
                self.refresh_members()
            except Exception as e:
                Message.err(self, "오류", str(e))

    def reject_signup(self):
        row = self.signup_table.selected_first_row_index()
        if row < 0: return
        sid = int(self.signup_table.get_cell(row, 0))

        text, ok = QtWidgets.QInputDialog.getText(self, "거절", "거절 사유:")
        if ok:
            try:
                self.db.reject_signup_request(sid, self.session.user_id, text)
                Message.info(self, "완료", "거절 처리되었습니다.")
                self.refresh_signup_requests()
            except Exception as e:
                Message.err(self, "오류", str(e))

    def change_password(self):
        dlg = ChangePasswordDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            pw = dlg.get_password()
            if pw:
                self.db.change_password(self.session.user_id, pw)
                Message.info(self, "성공", "비밀번호가 변경되었습니다.")


class WorkLogApproveDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, row_data=None, mode="START"):
        super().__init__(parent)
        self.data = row_data or {}
        self.mode = mode

        if self.mode == "START":
            self.setWindowTitle("출근 시간 승인/수정")
        else:
            self.setWindowTitle("퇴근 시간 승인/수정")

        self.resize(450, 250)

        layout = QtWidgets.QVBoxLayout()

        info_text = (
            f"일자: {self.data.get('work_date')}\n"
            f"근로자: {self.data.get('worker_username')}\n"
        )
        lbl_info = QtWidgets.QLabel(info_text)
        lbl_info.setStyleSheet("background-color: #f0f0f0; padding: 10px; border-radius: 5px;")
        layout.addWidget(lbl_info)

        form = QtWidgets.QFormLayout()

        self.dte_start = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.dte_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dte_start.setCalendarPopup(True)

        self.dte_end = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.dte_end.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dte_end.setCalendarPopup(True)

        s_time_str = self.data.get("approved_start") or self.data.get("start_time")
        e_time_str = self.data.get("approved_end") or self.data.get("end_time")

        if s_time_str:
            self.dte_start.setDateTime(QtCore.QDateTime.fromString(s_time_str, "yyyy-MM-dd HH:mm:ss"))

        if e_time_str:
            self.dte_end.setDateTime(QtCore.QDateTime.fromString(e_time_str, "yyyy-MM-dd HH:mm:ss"))
        else:
            self.dte_end.setDateTime(QtCore.QDateTime.currentDateTime())

        if self.mode == "START":
            self.dte_end.setEnabled(False)
            self.dte_end.setStyleSheet("color: #aaa; background-color: #eee;")
        else:
            self.dte_start.setEnabled(False)
            self.dte_start.setStyleSheet("color: #aaa; background-color: #eee;")

        self.cb_comment = QtWidgets.QComboBox()
        self.cb_comment.setEditable(True)
        standard_reasons = [
            "정상 승인 (특이사항 없음)",
            "지각 (실제 출근 시각 반영)",
            "조퇴 (실제 퇴근 시각 반영)",
            "연장 근무 승인",
            "근로자 요청에 의한 시간 정정",
            "기타 (직접 입력)"
        ]
        self.cb_comment.addItems(standard_reasons)

        old_comment = self.data.get("owner_comment")
        if old_comment:
            self.cb_comment.setCurrentText(old_comment)

        form.addRow("확정 출근시각", self.dte_start)
        form.addRow("확정 퇴근시각", self.dte_end)
        form.addRow("비고(사유)", self.cb_comment)

        layout.addLayout(form)

        btns = QtWidgets.QHBoxLayout()
        btn_label = "출근 확정" if self.mode == "START" else "퇴근 확정"

        self.btn_ok = QtWidgets.QPushButton(btn_label)
        self.btn_ok.setStyleSheet("font-weight: bold; color: #003366; padding: 6px;")
        self.btn_ok.clicked.connect(self.on_ok_clicked)

        self.btn_cancel = QtWidgets.QPushButton("취소")
        self.btn_cancel.clicked.connect(self.reject)

        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)

        layout.addLayout(btns)
        self.setLayout(layout)

    def on_ok_clicked(self):
        if self.mode == "END" and self.dte_end.isEnabled():
            s_dt = self.dte_start.dateTime()
            e_dt = self.dte_end.dateTime()
            secs = s_dt.secsTo(e_dt)
            hours = secs / 3600.0

            added_min = 0
            if hours >= 8:
                added_min = 60
            elif hours >= 4:
                added_min = 30

            if added_min > 0:
                msg = f"근무시간이 {int(hours)}시간 이상입니다.\n법정 휴게시간({added_min}분)을 부여하고 퇴근시간을 연장하시겠습니까?"
                ans = QtWidgets.QMessageBox.question(self, "휴게시간 확인", msg,
                                                     QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
                if ans == QtWidgets.QMessageBox.Yes:
                    new_e_dt = e_dt.addSecs(added_min * 60)
                    self.dte_end.setDateTime(new_e_dt)

                    slots = []
                    curr = s_dt
                    while curr < new_e_dt:
                        nxt = curr.addSecs(30 * 60)
                        if nxt > new_e_dt: break
                        slot_str = f"{curr.toString('HH:mm')} ~ {nxt.toString('HH:mm')}"
                        slots.append(slot_str)
                        curr = nxt

                    item, ok = QtWidgets.QInputDialog.getItem(
                        self, "휴게시간 선택",
                        f"부여한 휴게시간({added_min}분)을 선택하거나 입력하세요:",
                        slots, 0, True
                    )

                    if ok and item:
                        current_txt = self.cb_comment.currentText()
                        new_txt = f"{current_txt} | 휴게시간: {item}"
                        self.cb_comment.setCurrentText(new_txt)
                        QtWidgets.QMessageBox.information(self, "완료", f"퇴근시간이 {added_min}분 연장되고 휴게시간이 기록되었습니다.")

        self.accept()

    def get_data(self):
        s = self.dte_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        if self.mode == "START" and not self.dte_end.isEnabled():
            if not self.data.get("end_time") and not self.data.get("approved_end"):
                e = None
            else:
                e = self.dte_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        else:
            e = self.dte_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")

        c = self.cb_comment.currentText().strip()
        return s, e, c