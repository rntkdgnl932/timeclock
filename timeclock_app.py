# timeclock_app.py
# -*- coding: utf-8 -*-
import sys
from PyQt5 import QtWidgets
from PyQt5.QtCore import QTimer  # ◀ [추가] 타이머용

from timeclock.utils import setup_logging
from timeclock.settings import DB_PATH, APP_NAME
from timeclock.db import DB
from ui.main_window import MainWindow
from timeclock import backup_manager # ◀ [추가] 백업매니저

def main():
    setup_logging()
    db = DB(DB_PATH)

    # [1] 프로그램 시작 시 자동 백업 (PC + 구글드라이브)
    print("[System] 시작 자동 백업 실행 중...")
    backup_manager.run_backup("program_start")

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # [2] 6시간 주기 자동 백업 타이머 설정
    # (앱이 켜져있는 동안 6시간마다 돕니다)
    backup_timer = QTimer()
    # 6시간 * 60분 * 60초 * 1000밀리초
    interval = 6 * 60 * 60 * 1000
    backup_timer.timeout.connect(lambda: backup_manager.run_backup("periodic_6h"))
    backup_timer.start(interval)

    win = MainWindow(db)
    win.show()

    rc = app.exec_()
    db.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()