# timeclock/ui/main_window.py
# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta
from PyQt5 import QtWidgets, QtCore

from timeclock.settings import APP_NAME, DB_PATH, LOG_PATH, EXPORT_DIR, BACKUP_DIR, ARCHIVE_DIR
from timeclock.utils import Message
from ui.login_page import LoginPage
from ui.worker_page import WorkerPage
from ui.owner_page import OwnerPage
from ui.signup_page import SignupPage
from ui.dialogs import ChangePasswordDialog
from timeclock import backup_manager
from ui.async_helper import run_job_with_progress_async



class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.session = None

        self.setWindowTitle(APP_NAME)
        self.resize(1150, 760)

        self.stack = QtWidgets.QStackedWidget()
        self.setCentralWidget(self.stack)

        self.login = LoginPage(db)
        self.login.logged_in.connect(self.on_logged_in)

        # STEP 1에서 만든 직원가입 전환 시그널 연결
        if hasattr(self.login, "signup_requested"):
            self.login.signup_requested.connect(self.on_signup_requested)

        self.stack.addWidget(self.login)

        self._worker_page = None
        self._owner_page = None
        self._signup_page = None

        self._create_menu()

        QtCore.QTimer.singleShot(200, self.run_startup_backup)

    def run_startup_backup(self):
        """프로그램 시작 시 자동 백업 (비동기 + 진행바)"""

        def job_fn(progress_callback):
            # 프로그램 시작 백업 실행
            return backup_manager.run_backup("program_start", progress_callback)

        # 진행바 띄우기
        run_job_with_progress_async(
            self,
            "프로그램 시작 데이터 백업",
            job_fn
        )

    def _create_menu(self):
        menubar = self.menuBar()

        m_file = menubar.addMenu("파일")
        act_quit = QtWidgets.QAction("종료", self)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_manage = menubar.addMenu("관리")

        act_backup = QtWidgets.QAction("DB 백업(복사본 생성)", self)
        act_backup.triggered.connect(self.do_backup)
        m_manage.addAction(act_backup)

        act_export_month = QtWidgets.QAction("이번 달 CSV 백업(승인 기록)", self)
        act_export_month.triggered.connect(self.do_export_this_month)
        m_manage.addAction(act_export_month)

        act_vacuum = QtWidgets.QAction("DB 최적화(VACUUM)", self)
        act_vacuum.triggered.connect(self.do_vacuum)
        m_manage.addAction(act_vacuum)

        act_archive = QtWidgets.QAction("아카이브 DB 생성(승인 기록 복사)", self)
        act_archive.triggered.connect(self.do_archive)
        m_manage.addAction(act_archive)

        m_help = menubar.addMenu("도움말")
        act_about = QtWidgets.QAction("정보", self)
        act_about.triggered.connect(self.show_about)
        m_help.addAction(act_about)

    def show_about(self):
        Message.info(
            self,
            "정보",
            f"{APP_NAME}\n\n"
            "요청(근로자)과 승인(사업주)을 분리하여 근로시간을 객관적으로 기록합니다.\n"
            "백업/CSV/아카이브/최적화는 [관리] 메뉴에서 실행합니다.\n\n"
            f"DB: {DB_PATH}\nLOG: {LOG_PATH}\nEXPORT: {EXPORT_DIR}\nBACKUP: {BACKUP_DIR}\nARCHIVE: {ARCHIVE_DIR}",
        )

    def _require_owner(self) -> bool:
        if not self.session or self.session.role != "owner":
            Message.warn(self, "권한", "해당 기능은 사업주(owner) 로그인 상태에서만 사용할 수 있습니다.")
            return False
        return True

    def do_backup(self):
        if not self._require_owner():
            return

        # 1. 비동기 백업 함수 정의
        def job_fn(progress_callback):
            # "manual_owner"라는 태그로 백업 수행
            return backup_manager.run_backup("manual_owner", progress_callback)

        # 2. 완료 후 처리 (성공/실패 메시지 대신 자동 닫힘 처리됨)
        def on_done(ok, res, err):
            # 실패했을 때만 여기서 추가 메시지를 띄우거나,
            # 성공 시에는 async_helper가 알아서 "완료" 후 닫아줌
            pass

        # 3. 진행창 실행
        run_job_with_progress_async(
            self,
            "관리자 수동 DB 백업",
            job_fn,
            on_done=on_done
        )

    def do_export_this_month(self):
        if not self._require_owner():
            return
        today = datetime.now().date()
        first = today.replace(day=1)
        next_month = (first + timedelta(days=32)).replace(day=1)
        last = next_month - timedelta(days=1)
        d1 = first.strftime("%Y-%m-%d")
        d2 = last.strftime("%Y-%m-%d")
        out_path = EXPORT_DIR / f"approved_{d1}_to_{d2}.csv"
        try:
            self.db.export_records_csv(out_path, d1, d2)
            Message.info(self, "CSV 백업 완료", f"승인 기록 CSV 저장 완료:\n{out_path}\n(기간: {d1} ~ {d2})")
        except Exception as e:
            Message.err(self, "오류", f"CSV 백업 중 오류: {e}")

    def do_vacuum(self):
        if not self._require_owner():
            return
        try:
            self.db.vacuum()
            Message.info(self, "최적화 완료", "DB 최적화(VACUUM)가 완료되었습니다.")
        except Exception as e:
            Message.err(self, "오류", f"최적화 중 오류: {e}")

    def do_archive(self):
        if not self._require_owner():
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self,
            "아카이브",
            "아카이브 기준일(YYYY-MM-DD) 이전(포함) 승인 기록을 아카이브 DB로 '복사'합니다.\n예: 2025-12-31",
        )
        if not ok:
            return
        cutoff = text.strip()
        try:
            datetime.strptime(cutoff, "%Y-%m-%d")
        except Exception:
            Message.warn(self, "입력 오류", "날짜 형식이 올바르지 않습니다. 예: 2025-12-31")
            return

        archive_path = ARCHIVE_DIR / f"archive_upto_{cutoff.replace('-','')}.db"
        try:
            n = self.db.archive_approved_before_copyonly(cutoff, archive_path)
            Message.info(
                self,
                "아카이브 완료",
                f"아카이브 DB 생성/복사 완료:\n{archive_path}\n복사된 승인 기록 수: {n}\n\n"
                "안전상 운영 DB에서 삭제는 하지 않았습니다.",
            )
        except Exception as e:
            Message.err(self, "오류", f"아카이브 중 오류: {e}")

    def on_logged_in(self, session):
        self.session = session
        logging.info(f"Logged in: {session.username} ({session.role})")

        # 🔴 STEP 5: 비밀번호 변경 강제
        if session.must_change_pw:
            dlg = ChangePasswordDialog(parent=self)
            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                Message.warn(self, "비밀번호 변경", "비밀번호 변경이 필요합니다.")
                self.session = None
                self._back_to_login()
                return

            new_pw = dlg.get_password()
            if not new_pw:
                Message.warn(self, "비밀번호 변경", "비밀번호 변경이 완료되지 않았습니다.")
                self.session = None
                self._back_to_login()
                return

            try:
                self.db.change_password(session.user_id, new_pw)
                session.must_change_pw = False
            except Exception as e:
                logging.exception("Password change failed")
                Message.err(self, "오류", f"비밀번호 변경 실패: {e}")
                self.session = None
                self._back_to_login()
                return

            Message.info(self, "완료", "비밀번호가 변경되었습니다. 다시 로그인해주세요.")
            self.session = None
            self._back_to_login()
            return

        # 🔽 정상 로그인 흐름
        is_owner_view = (session.role != "worker") or (getattr(session, "job_title", "") == "대표")

        try:
            if is_owner_view:
                self._owner_page = OwnerPage(self.db, session)
                self._owner_page.logout_requested.connect(self.on_logout)
                self._set_page(self._owner_page)
            else:
                self._worker_page = WorkerPage(self.db, session)
                self._worker_page.logout_requested.connect(self.on_logout)
                self._set_page(self._worker_page)

        except Exception as e:
            logging.exception("Failed to create page after login")
            Message.err(self, "오류", f"로그인 후 화면 생성 중 오류:\n{e}")
            self._back_to_login()



    def _set_page(self, widget):
        # login(0)은 유지, 1번 이후는 모두 제거 후 새로 붙임
        while self.stack.count() > 1:
            w = self.stack.widget(1)
            self.stack.removeWidget(w)

            # 🚨 수정: deleteLater()는 이벤트 루프가 돌 때 호출되어야 안정적입니다.
            #     충돌을 피하기 위해 QTimer.singleShot으로 지연 삭제를 시도합니다.
            QtCore.QTimer.singleShot(0, w.deleteLater)

        self.stack.addWidget(widget)
        self.stack.setCurrentWidget(widget)

    def _back_to_login(self):
        while self.stack.count() > 1:
            w = self.stack.widget(1)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.stack.setCurrentWidget(self.login)

    # STEP 2: 직원가입 화면 전환
    def on_signup_requested(self):
        try:
            self._signup_page = SignupPage(self.db)
            # ❗ 이 시그널이 signup_page.py에 정확히 정의되어 있어야 합니다.
            self._signup_page.signup_done.connect(self._back_to_login)
            self._set_page(self._signup_page)
        except Exception as e:
            # 이 부분이 없으면 그냥 꺼집니다. 로그를 남겨야 합니다.
            logging.exception("SignupPage 생성 중 오류 발생")
            Message.err(self, "오류", f"가입 화면 로드 실패: {e}")
            self._back_to_login()

    def on_back_to_login(self):
        self._back_to_login()

    def on_logout(self):
        logging.info(f"Logout: {self.session.username if self.session else '-'}")
        self.session = None
        self._back_to_login()


    def is_logged_in(self) -> bool:
        """현재 로그인 페이지가 아닌 다른 페이지(업무/관리자)에 있는지 확인"""
        # 스택 위젯의 현재 페이지가 로그인 페이지인지 확인하는 로직
        # 예: 현재 페이지 위젯의 타입이 LoginPage가 아니면 로그인 상태로 간주
        current_widget = self.stack.currentWidget()
        # self.login_page 변수가 __init__에서 생성되어 있다고 가정
        return current_widget != self.login_page

    def force_logout(self):
        """강제 로그아웃 실행"""
        # 기존 로그아웃 처리 로직 호출 (화면 전환, 세션 초기화 등)
        # 예: 로그인 페이지로 이동
        self.stack.setCurrentWidget(self.login_page)
        # 필요하다면 메시지 표시
        # QtWidgets.QMessageBox.information(self, "알림", "장시간 미사용으로 로그아웃되었습니다.")
