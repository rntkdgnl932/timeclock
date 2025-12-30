# timeclock_app.py
# -*- coding: utf-8 -*-
import sys
import re
from PyQt5 import QtWidgets, QtCore  # QtCore 추가됨
from PyQt5.QtCore import QTimer

from timeclock.utils import setup_logging
from timeclock.settings import DB_PATH, APP_NAME
from timeclock.db import DB
from ui.main_window import MainWindow
from timeclock import backup_manager


def _ensure_backup_id_or_exit(app: QtWidgets.QApplication) -> str:
    """
    backup_id.txt가 없으면 강제 생성(영문/숫자/_/-만 허용).
    취소 시 프로그램 종료.
    """
    backup_id_file = backup_manager.get_backup_id_file_path()
    if backup_id_file.exists():
        bid = backup_manager.read_backup_id()
        if bid:
            return bid

    QtWidgets.QMessageBox.information(
        None,
        "백업 ID 설정 필요",
        "이 PC의 백업을 구분하기 위한 backup_id가 필요합니다.\n"
        "영문(영문/숫자/_/-)으로 backup_id를 입력해 주세요.\n\n"
        "예: TESTPC, office_01, dev-laptop"
    )

    pattern = re.compile(r"^[A-Za-z0-9_-]+$")
    while True:
        text, ok = QtWidgets.QInputDialog.getText(
            None,
            "backup_id 생성",
            "backup_id (영문/숫자/_/- 만 허용):"
        )

        if not ok:
            sys.exit(0)

        bid = (text or "").strip()
        if not bid:
            QtWidgets.QMessageBox.warning(None, "입력 오류", "backup_id는 비어 있을 수 없습니다.")
            continue

        if not pattern.match(bid):
            QtWidgets.QMessageBox.warning(None, "입력 오류", "backup_id는 영문/숫자/_/- 만 사용할 수 있습니다.")
            continue

        ok2, msg = backup_manager.write_backup_id(bid)
        if not ok2:
            QtWidgets.QMessageBox.critical(None, "저장 실패", f"backup_id 저장에 실패했습니다.\n{msg}")
            sys.exit(1)

        QtWidgets.QMessageBox.information(None, "설정 완료", f"backup_id 저장 완료: {bid}")
        return bid


# ------------------------------------------------------------------------
# [추가] 자동 로그아웃 감지 필터
# ------------------------------------------------------------------------
class AutoLogoutFilter(QtCore.QObject):
    def __init__(self, app, main_window, timeout_min=10):
        super().__init__()
        self.app = app
        self.win = main_window
        self.timeout_ms = timeout_min * 60 * 1000  # 10분 (설정 시간)
        self.warning_ms = 60 * 1000  # 1분 (경고 후 대기 시간)

        # 1. 비활동 감지 타이머
        self.idle_timer = QtCore.QTimer(self)
        self.idle_timer.setInterval(self.timeout_ms)
        self.idle_timer.timeout.connect(self.on_idle_timeout)

        # 2. 로그아웃 카운트다운 타이머
        self.logout_timer = QtCore.QTimer(self)
        self.logout_timer.setInterval(self.warning_ms)
        self.logout_timer.setSingleShot(True)
        self.logout_timer.timeout.connect(self.do_logout)

        self.warn_dialog = None

        # 앱 전체 이벤트 필터링 시작
        self.app.installEventFilter(self)
        self.idle_timer.start()

    def eventFilter(self, obj, event):
        # 사용자가 마우스를 움직이거나, 클릭하거나, 키보드를 누르면 타이머 리셋
        if event.type() in (QtCore.QEvent.MouseMove,
                            QtCore.QEvent.MouseButtonPress,
                            QtCore.QEvent.KeyPress):
            self.reset_activity()
        return super().eventFilter(obj, event)

    def reset_activity(self):
        """활동이 감지되면 타이머를 초기화하고 경고창이 있다면 닫음"""
        # 경고창이 떠있으면 닫기 (사용자가 돌아옴)
        if self.warn_dialog:
            try:
                self.warn_dialog.close()
            except:
                pass
            self.warn_dialog = None

        if self.logout_timer.isActive():
            self.logout_timer.stop()

        # 메인 타이머 재시작
        self.idle_timer.start(self.timeout_ms)

    def on_idle_timeout(self):
        """10분간 입력이 없을 때 호출됨"""
        # 로그인 상태가 아니면 무시 (이미 로그인 화면이면 작동 X)
        if hasattr(self.win, "is_logged_in") and not self.win.is_logged_in():
            self.idle_timer.start(self.timeout_ms)
            return

        self.idle_timer.stop()

        # 경고창 표시
        self.warn_dialog = QtWidgets.QMessageBox(self.win)
        self.warn_dialog.setWindowTitle("자동 로그아웃 알림")
        self.warn_dialog.setText("10분 동안 활동이 감지되지 않아\n잠시 후 자동으로 로그아웃됩니다.\n\n계속 하시려면 마우스를 움직이거나 클릭하세요.")
        self.warn_dialog.setIcon(QtWidgets.QMessageBox.Warning)
        self.warn_dialog.setStandardButtons(QtWidgets.QMessageBox.Ok)
        self.warn_dialog.button(QtWidgets.QMessageBox.Ok).setText("로그인 연장")
        self.warn_dialog.setModal(True)  # 다른 작업 못하게 막음
        self.warn_dialog.show()

        # 1분 카운트다운 시작 (이 시간 안에도 반응 없으면 로그아웃)
        self.logout_timer.start()

    def do_logout(self):
        """최종 로그아웃 실행"""
        if self.warn_dialog:
            try:
                self.warn_dialog.close()
            except:
                pass
            self.warn_dialog = None

        # 메인 윈도우에 로그아웃 명령 전달
        if hasattr(self.win, "force_logout"):
            self.win.force_logout()

        # 타이머 재시작 (로그인 화면에서도 감지는 계속 하되, on_idle_timeout에서 걸러짐)
        self.idle_timer.start(self.timeout_ms)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # [0] backup_id 확인
    _ensure_backup_id_or_exit(app)

    setup_logging()
    db = DB(DB_PATH)

    # [1] 6시간 주기 자동 백업 타이머
    backup_timer = QTimer()
    interval = 6 * 60 * 60 * 1000
    backup_timer.timeout.connect(lambda: backup_manager.run_backup("periodic_6h"))
    backup_timer.start(interval)

    win = MainWindow(db)
    win.show()

    # [2] 자동 로그아웃 감시자 실행 (10분 = 10, 테스트시 숫자를 줄여보세요)
    logout_filter = AutoLogoutFilter(app, win, timeout_min=10)

    # 메인 루프 실행
    rc = app.exec_()
    db.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()