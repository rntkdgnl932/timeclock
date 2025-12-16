# timeclock/ui/owner_page.py
# -*- coding: utf-8 -*-
import logging
from PyQt5 import QtWidgets, QtCore
from timeclock import backup_manager
from datetime import datetime
import os
from pathlib import Path
from timeclock.settings import DATA_DIR

from timeclock.excel_maker import generate_payslip, create_default_template

from timeclock.utils import Message
from ui.widgets import DateRangeBar, Table
from timeclock.settings import WORK_STATUS, SIGNUP_STATUS
from ui.dialogs import ChangePasswordDialog, DisputeTimelineDialog, DateRangeDialog # ◀ 추가
from timeclock.salary import SalaryCalculator  # [NEW]


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

        self.tabs.addTab(self._build_restore_tab(), "데이터 복구 (백업)")

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

        # [추가] 상태 필터 콤보박스
        self.cb_work_status = QtWidgets.QComboBox()

        self.cb_work_status.addItem("승인 대기 (처리 필요)", "PENDING")
        self.cb_work_status.addItem("승인 완료 (확정됨)", "APPROVED")
        self.cb_work_status.addItem("근무 중", "WORKING")
        self.cb_work_status.addItem("전체 보기", "ALL")
        self.cb_work_status.currentIndexChanged.connect(lambda *_: self.refresh_work_logs())

        self.btn_work_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_work_refresh.clicked.connect(self.refresh_work_logs)

        # ... (기존 버튼들: edit_start, edit_end 등) ...
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

        # [수정] 상단 레이아웃에 콤보박스 추가
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addWidget(self.filter_work)
        top_layout.addWidget(self.cb_work_status)  # 콤보박스 배치
        top_layout.addWidget(self.btn_work_refresh)
        top_layout.addStretch(1)

        # 버튼 레이아웃
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addWidget(self.btn_edit_start)
        btn_layout.addWidget(self.btn_edit_end)
        btn_layout.addStretch(1)

        l = QtWidgets.QVBoxLayout()
        l.addLayout(top_layout)
        l.addLayout(btn_layout)
        l.addWidget(QtWidgets.QLabel("※ 출근 시간만 고치려면 '출근 승인', 퇴근까지 확정하려면 '퇴근 승인'을 누르세요."))
        l.addWidget(self.work_table)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w

    def refresh_work_logs(self):
        d1, d2 = self.filter_work.get_range()

        # [추가] 현재 선택된 상태값 가져오기
        status_filter = self.cb_work_status.currentData()

        try:
            # [수정] status_filter 인자 전달
            rows = self.db.list_all_work_logs(None, d1, d2, status_filter=status_filter)
            self._work_rows = rows

            out = []
            for r in rows:
                rr = dict(r)
                st = rr["status"]
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

                # ▼ [추가됨] 승인 성공 시 자동 백업 (구글드라이브 + PC)
                backup_manager.run_backup("approve")

                msg = "출근 시간이 수정되었습니다." if mode == "START" else "퇴근 승인(수정)이 완료되었습니다."
                Message.info(self, "성공", msg)
                self.refresh_work_logs()
            except Exception as e:
                Message.err(self, "오류", f"처리 중 오류: {e}")

    # ==========================================================
    # 2. 회원(급여) 관리 탭
    # ==========================================================
    def _build_member_tab(self):
        # 1. 검색 및 필터 컨트롤
        self.le_member_search = QtWidgets.QLineEdit()
        self.le_member_search.setPlaceholderText("이름 검색...")
        self.le_member_search.returnPressed.connect(self.refresh_members)

        self.cb_member_filter = QtWidgets.QComboBox()
        self.cb_member_filter.addItem("재직자 보기", "ACTIVE")
        self.cb_member_filter.addItem("퇴사자 보기", "INACTIVE")
        self.cb_member_filter.addItem("전체 보기", "ALL")
        self.cb_member_filter.currentIndexChanged.connect(self.refresh_members)

        self.btn_member_search = QtWidgets.QPushButton("검색")
        self.btn_member_search.clicked.connect(self.refresh_members)

        # 2. 기능 버튼들 생성 (★ 순서 중요: addWidget 전에 무조건 생성되어야 함)

        # [시급 변경]
        self.btn_edit_wage = QtWidgets.QPushButton("시급 변경")
        self.btn_edit_wage.setStyleSheet("background-color: #E3F2FD; color: #0D47A1;")
        self.btn_edit_wage.clicked.connect(self.edit_wage)

        # [급여 정산]
        self.btn_calc_salary = QtWidgets.QPushButton("급여 정산")
        self.btn_calc_salary.setStyleSheet("background-color: #fff3e0; color: #e65100; font-weight: bold;")
        self.btn_calc_salary.clicked.connect(self.calculate_salary)

        # [명세서 발급] (★ 여기가 누락되었거나 순서가 뒤였을 수 있음)
        self.btn_export_payslip = QtWidgets.QPushButton("명세서 발급 (Excel)")
        try:
            self.btn_export_payslip.clicked.disconnect()
        except:
            pass
        self.btn_export_payslip.setStyleSheet("background-color: #e8f5e9; color: #1b5e20; font-weight: bold;")
        self.btn_export_payslip.clicked.connect(self.export_payslip)



        # [퇴사 처리]
        self.btn_resign = QtWidgets.QPushButton("퇴사 처리")
        self.btn_resign.setStyleSheet("background-color: #ffebee; color: #b71c1c;")
        self.btn_resign.clicked.connect(self.resign_worker)

        # 3. 레이아웃 배치
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.addWidget(self.le_member_search)
        top_layout.addWidget(self.cb_member_filter)
        top_layout.addWidget(self.btn_member_search)
        top_layout.addStretch(1)  # 중간 여백

        # 버튼들 순서대로 추가
        top_layout.addWidget(self.btn_edit_wage)
        top_layout.addWidget(self.btn_calc_salary)
        top_layout.addWidget(self.btn_export_payslip)  # 생성된 버튼 추가
        top_layout.addWidget(self.btn_resign)

        # 4. 테이블 구성
        self.member_table = Table([
            "ID", "아이디", "성함", "전화번호", "생년월일", "시급", "가입일", "상태"
        ])
        self.member_table.setColumnWidth(0, 0)
        self.member_table.itemDoubleClicked.connect(self.edit_wage)

        # 전체 레이아웃 조합
        l = QtWidgets.QVBoxLayout()
        l.addLayout(top_layout)
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

        # OwnerPage 클래스 내부 메서드로 추가

    def calculate_salary(self):
        try:
            # 1. 대상 선택 확인
            row = self.member_table.selected_first_row_index()
            if row < 0:
                Message.warn(self, "알림", "급여를 정산할 직원을 목록에서 선택하세요.")
                return

            rr = dict(self._member_rows[row])
            user_id = rr['id']
            username = rr['username']
            hourly_wage = rr['hourly_wage'] or 0

            # 2. 기간 선택 (달력 팝업)
            dlg = DateRangeDialog(self)
            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                return  # 취소 시 중단

            d1, d2 = dlg.get_range()

            # 3. DB에서 확정된(APPROVED) 근무 기록만 가져오기
            logs = self.db.list_all_work_logs(user_id, d1, d2, status_filter='APPROVED')

            if not logs:
                Message.info(self, "결과", "해당 기간에 승인된 근무 기록이 없습니다.")
                return

            # 4. 계산기 가동
            calc = SalaryCalculator(wage_per_hour=hourly_wage)
            res = calc.calculate_period([dict(r) for r in logs])

            if not res:
                Message.info(self, "결과", "계산할 데이터가 없습니다.")
                return

            # 5. 결과 문자열 만들기 (새로운 salary.py 로직 반영)
            final_pay = res['grand_total']

            # 주휴수당 상세 내역
            details = res.get('ju_hyu_details', [])
            if details:
                detail_str = " + ".join([f"{x:,}" for x in details])
                ju_hyu_msg = f"주휴수당: {detail_str} = {res['ju_hyu_pay']:,}원"
            else:
                ju_hyu_msg = f"주휴수당: {res['ju_hyu_pay']:,}원"

            # 메시지 구성 (연장/야간 분리 표시)
            msg = (
                f"[{d1} ~ {d2} 급여 정산 결과]\n\n"
                f"• 총 근무시간: {res['total_hours']}시간\n"
                f"• 실제 근무(공제후): {res['actual_hours']}시간\n\n"
                f"-------------- 상세 내역 --------------\n"
                f"1. 기본급: {res['base_pay']:,}원\n"
                f"2. 연장수당: {res['overtime_pay']:,}원 (8h 초과)\n"
                f"3. 야간수당: {res['night_pay']:,}원 (22시~06시)\n"
                f"4. {ju_hyu_msg}\n"
                f"---------------------------------------\n"
                f"💰 예상 지급 총액: {final_pay:,}원"
            )

            QtWidgets.QMessageBox.information(self, "예상 급여 내역", msg)

        except Exception as e:
            import traceback
            traceback.print_exc()
            Message.err(self, "오류", f"계산 중 오류가 발생했습니다.\n{e}")

    #
    def export_payslip(self):
        # 1. 직원 선택 확인
        row = self.member_table.selected_first_row_index()
        if row < 0:
            Message.warn(self, "알림", "명세서를 발급할 직원을 선택하세요.")
            return

        rr = dict(self._member_rows[row])
        user_id = rr['id']
        username = rr['username']
        real_name = rr.get('name') or username
        hourly_wage = rr['hourly_wage'] or 0

        # 2. 기간 선택
        dlg = DateRangeDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted: return
        d1, d2 = dlg.get_range()

        # 3. 데이터 조회
        logs = self.db.list_all_work_logs(user_id, d1, d2, status_filter='APPROVED')
        if not logs:
            Message.warn(self, "알림", "해당 기간에 승인된 근무 기록이 없습니다.")
            return

        # 4. 급여 계산
        calc = SalaryCalculator(hourly_wage)
        res = calc.calculate_period([dict(r) for r in logs])
        total_pay = res['grand_total']

        # 5. 공제 계산
        ei_tax = int(total_pay * 0.009 / 10) * 10
        pension = 0
        health = 0
        care = 0
        income_tax = 0
        local_tax = 0
        total_deduction = ei_tax + pension + health + care + income_tax + local_tax
        net_pay = total_pay - total_deduction

        # 6. 상세 문구 작성
        # (1) 시간 역산
        over_hours = 0
        night_hours = 0
        ju_hyu_hours = 0
        if hourly_wage > 0:
            over_hours = round(res['overtime_pay'] / (hourly_wage * 0.5), 1)
            night_hours = round(res['night_pay'] / (hourly_wage * 0.5), 1)
            ju_hyu_hours = round(res['ju_hyu_pay'] / hourly_wage, 1)

        # (2) 텍스트 생성
        break_time = round(res['total_hours'] - res['actual_hours'], 1)
        calc_str = f"• 근태: 총 {res['total_hours']}h - 휴게 {break_time}h = 실 근무 {res['actual_hours']}h"
        base_str = f"• 기본급: {res['actual_hours']}시간 × {hourly_wage:,}원 = {res['base_pay']:,}원"

        if res['overtime_pay'] > 0 or res['night_pay'] > 0:
            over_msg = []
            if res['overtime_pay'] > 0: over_msg.append(f"연장 {over_hours}h")
            if res['night_pay'] > 0: over_msg.append(f"야간 {night_hours}h")
            sum_add_pay = res['overtime_pay'] + res['night_pay']
            over_str = f"• 가산(0.5배): {' + '.join(over_msg)} = {sum_add_pay:,}원"
        else:
            over_str = "• 가산수당: 해당 없음"

        if res['ju_hyu_pay'] > 0:
            ju_hyu_str = f"• 주휴수당: {ju_hyu_hours}시간 (주 15시간↑ 개근) = {res['ju_hyu_pay']:,}원"
        else:
            ju_hyu_str = "• 주휴수당: 해당 없음 (조건 미충족)"

        note_text = ""
        if res['ju_hyu_pay'] > 0:
            note_text = (
                "※ 주휴수당 지급 안내:\n"
                "본 주는 일시적 업무 증가로 주 15시간 이상 근무하여\n"
                "근로기준법에 의거 주휴수당을 지급하였습니다."
            )
        else:
            note_text = "※ 본 명세서는 근로기준법 제48조에 따라 교부합니다."

        # 7. 엑셀 데이터 매핑
        data_ctx = {
            "title": f"{d1[:4]}년 {d1[5:7]}월 급여명세서",
            "name": real_name,
            "period": f"{d1} ~ {d2}",
            "pay_date": datetime.now().strftime("%Y-%m-%d"),
            "company": "Hobby Store",

            "base_pay": res['base_pay'],
            "ju_hyu_pay": res['ju_hyu_pay'],
            "overtime_pay": res['overtime_pay'],
            "night_pay": res['night_pay'],
            "holiday_pay": res['holiday_pay'],
            "other_pay": 0,
            "total_pay": total_pay,

            "ei_ins": ei_tax,
            "pension": pension,
            "health_ins": health,
            "care_ins": care,
            "income_tax": income_tax,
            "local_tax": local_tax,
            "total_deduction": total_deduction,
            "net_pay": net_pay,

            "calc_detail": calc_str,
            "base_detail": base_str,
            "over_detail": over_str,
            "ju_hyu_detail": ju_hyu_str,
            "tax_detail": "고용보험 0.9%",
            "note": note_text
        }

        # 8. 파일 생성 및 저장
        try:
            template_path = DATA_DIR / "template.xlsx"

            # ★ [핵심 수정] 파일이 없으면 에러 내지 말고, 즉시 생성!
            if not template_path.exists():
                print(f"템플릿이 없어서 새로 만듭니다: {template_path}")
                create_default_template(str(template_path))

            save_dir = Path(r"C:\my_games\timeclock\pay_result")
            save_dir.mkdir(parents=True, exist_ok=True)

            safe_d1 = d1.replace("-", "")
            safe_d2 = d2.replace("-", "")
            filename = f"급여명세서_{real_name}_{safe_d1}_{safe_d2}.xlsx"
            target_path = save_dir / filename

            save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "명세서 저장",
                str(target_path),
                "Excel Files (*.xlsx)"
            )

            if save_path:
                # 파일 생성
                result = generate_payslip(str(template_path), save_path, data_ctx)

                if result:
                    Message.info(self, "완료", f"급여명세서가 생성되었습니다.\n{save_path}")
                    try:
                        os.startfile(os.path.dirname(save_path))
                    except:
                        pass
                else:
                    Message.err(self, "실패", "엑셀 파일 생성 중 오류가 발생했습니다.")

        except Exception as e:
            print("=" * 50)
            import traceback
            traceback.print_exc()
            print("=" * 50)
            Message.err(self, "오류", f"처리 중 오류 발생: {e}")

    #
    # ==========================================================
    # 5. 데이터 복구 탭 (새로 추가된 기능)
    # ==========================================================
    def _build_restore_tab(self):
        layout = QtWidgets.QVBoxLayout()

        # 안내 문구
        lbl_info = QtWidgets.QLabel("⚠️ 원하는 시점을 선택하고 [복구]를 누르면, 데이터가 그 시절로 돌아갑니다.")
        lbl_info.setStyleSheet("color: #d32f2f; font-weight: bold; margin: 10px;")
        layout.addWidget(lbl_info)

        # 버튼들
        btn_layout = QtWidgets.QHBoxLayout()
        btn_refresh = QtWidgets.QPushButton("🔄 목록 새로고침")
        btn_refresh.clicked.connect(self.refresh_backup_list)
        btn_manual = QtWidgets.QPushButton("💾 현재 상태 수동 저장")
        btn_manual.clicked.connect(self.manual_backup)

        btn_layout.addWidget(btn_refresh)
        btn_layout.addWidget(btn_manual)
        layout.addLayout(btn_layout)

        # 테이블 (리스트)
        self.table_backup = QtWidgets.QTableWidget()
        self.table_backup.setColumnCount(4)
        self.table_backup.setHorizontalHeaderLabels(["저장 시각", "저장 이유", "크기", "파일명(숨김)"])
        self.table_backup.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table_backup.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table_backup.setColumnHidden(3, True)  # 파일명은 숨김
        self.table_backup.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table_backup.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table_backup)

        # 복구 버튼
        self.btn_restore = QtWidgets.QPushButton("⏳ 선택한 시점으로 되돌리기 (복구)")
        self.btn_restore.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; padding: 12px;")
        self.btn_restore.clicked.connect(self.run_restore)
        layout.addWidget(self.btn_restore)

        # 탭 만들어질 때 리스트 로딩
        self.refresh_backup_list()

        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    def refresh_backup_list(self):
        """백업 매니저에서 목록을 가져와 테이블 갱신"""
        data = backup_manager.get_backup_list()
        self.table_backup.setRowCount(0)

        for item in data:
            row = self.table_backup.rowCount()
            self.table_backup.insertRow(row)

            self.table_backup.setItem(row, 0, QtWidgets.QTableWidgetItem(item['time']))
            self.table_backup.setItem(row, 1, QtWidgets.QTableWidgetItem(item['reason']))
            self.table_backup.setItem(row, 2, QtWidgets.QTableWidgetItem(item['size']))
            self.table_backup.setItem(row, 3, QtWidgets.QTableWidgetItem(item['filename']))

    def manual_backup(self):
        """수동 저장 버튼 클릭 시"""
        res = QtWidgets.QMessageBox.question(self, "저장", "현재 데이터를 백업하시겠습니까?",
                                             QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if res == QtWidgets.QMessageBox.Yes:
            ok, msg = backup_manager.run_backup("manual")
            if ok:
                Message.info(self, "성공", f"백업 완료!\n({msg})")
            else:
                Message.err(self, "실패", msg)
            self.refresh_backup_list()

    def run_restore(self):
        """복구 버튼 클릭 시"""
        row = self.table_backup.currentRow()
        if row < 0:
            Message.warn(self, "선택", "복구할 시점을 목록에서 선택해주세요.")
            return

        time_str = self.table_backup.item(row, 0).text()
        reason_str = self.table_backup.item(row, 1).text()
        filename = self.table_backup.item(row, 3).text()

        msg = (f"정말 데이터를 되돌리시겠습니까?\n\n"
               f"선택한 시점: {time_str}\n"
               f"내용: {reason_str}\n\n"
               f"⚠️ 주의: 복구 시, 현재 데이터는 덮어씌워집니다.\n"
               f"(안전을 위해, 복구 직전 상태가 한 번 더 자동 저장됩니다.)")

        res = QtWidgets.QMessageBox.warning(self, "데이터 복구", msg,
                                            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)

        if res == QtWidgets.QMessageBox.Yes:
            ok, result_msg = backup_manager.restore_backup(filename)
            if ok:
                QtWidgets.QMessageBox.information(self, "복구 완료",
                                                  "데이터가 성공적으로 복구되었습니다.\n안전한 적용을 위해 프로그램이 종료됩니다.\n다시 실행해주세요.")
                QtWidgets.QApplication.quit()  # 프로그램 종료 (재시작 유도)
            else:
                Message.err(self, "오류", result_msg)
            self.refresh_backup_list()

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

        # [수정] START 모드이면 퇴근 시간은 건드리지 않음 (None으로 처리)
        if self.mode == "START":
            e = None
        else:
            # END 모드일 때만 퇴근 시간 값을 가져감
            if self.dte_end.isEnabled():
                e = self.dte_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            else:
                e = None

        c = self.cb_comment.currentText().strip()
        return s, e, c