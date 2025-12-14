# timeclock/ui/main_window.py
# -*- coding: utf-8 -*-
import logging
import traceback
from datetime import datetime, timedelta
from PyQt5 import QtWidgets, QtCore

from timeclock.settings import APP_NAME, DB_PATH, LOG_PATH, EXPORT_DIR, BACKUP_DIR, ARCHIVE_DIR
from timeclock.utils import Message
from ui.login_page import LoginPage
from ui.worker_page import WorkerPage
from ui.owner_page import OwnerPage
from ui.signup_page import SignupPage


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
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = BACKUP_DIR / f"timeclock_backup_{ts}.db"
        try:
            self.db.backup_db_copy(out_path)
            Message.info(self, "백업 완료", f"DB 백업 완료:\n{out_path}")
        except Exception as e:
            Message.err(self, "오류", f"백업 중 오류: {e}")

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

        new_page = None  # 🚨 new_page 변수 초기화

        try:
            if session.role == "worker":
                logging.info("Attempting to create WorkerPage...")
                self._worker_page = WorkerPage(self.db, session)
                self._worker_page.logout_requested.connect(self.on_logout)
                new_page = self._worker_page
            else:
                logging.info("Attempting to create OwnerPage...")
                # 🚨 충돌 지점: OwnerPage 객체 생성 (현재는 __init__ 최소화 상태)
                self._owner_page = OwnerPage(self.db, session)
                self._owner_page.logout_requested.connect(self.on_logout)
                new_page = self._owner_page

            if new_page:
                self._set_page(new_page)  # 🚨 new_page가 생성되었을 때만 전환 시도
            else:
                raise Exception("Page object was not created.")

        except Exception as e:
            logging.exception("Failed to create page after login")
            # 🚨 오류 메시지를 띄우고 로그인 화면으로 복귀
            Message.err(self, "오류", f"로그인 후 화면 생성 중 오류:\n{e}\n\n{traceback.format_exc()}")
            self.session = None
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
