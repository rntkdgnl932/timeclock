# timeclock_app.py
# -*- coding: utf-8 -*-
import sys
import re
from PyQt5 import QtWidgets
from PyQt5.QtCore import QTimer  # ◀ [추가] 타이머용

from timeclock.utils import setup_logging
from timeclock.settings import DB_PATH, APP_NAME
from timeclock.db import DB
from ui.main_window import MainWindow
from timeclock import backup_manager  # ◀ [추가] 백업매니저


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

    # 최초 생성 안내
    QtWidgets.QMessageBox.information(
        None,
        "백업 ID 설정 필요",
        "이 PC의 백업을 구분하기 위한 backup_id가 필요합니다.\n"
        "영문(영문/숫자/_/-)으로 backup_id를 입력해 주세요.\n\n"
        "예: TESTPC, office_01, dev-laptop"
    )

    pattern = re.compile(r"^[A-Za-z0-9_-]+$")  # '영어로만' 기준: 영문/숫자/_/-
    while True:
        text, ok = QtWidgets.QInputDialog.getText(
            None,
            "backup_id 생성",
            "backup_id (영문/숫자/_/- 만 허용):"
        )

        if not ok:
            QtWidgets.QMessageBox.critical(
                None,
                "종료",
                "backup_id 설정이 취소되었습니다. 프로그램을 종료합니다."
            )
            sys.exit(0)

        bid = (text or "").strip()
        if not bid:
            QtWidgets.QMessageBox.warning(None, "입력 오류", "backup_id는 비어 있을 수 없습니다.")
            continue

        if not pattern.match(bid):
            QtWidgets.QMessageBox.warning(
                None,
                "입력 오류",
                "backup_id는 영문/숫자/_/- 만 사용할 수 있습니다."
            )
            continue

        # 저장
        ok2, msg = backup_manager.write_backup_id(bid)
        if not ok2:
            QtWidgets.QMessageBox.critical(None, "저장 실패", f"backup_id 저장에 실패했습니다.\n{msg}")
            sys.exit(1)

        QtWidgets.QMessageBox.information(None, "설정 완료", f"backup_id 저장 완료: {bid}")
        return bid


def main():
    # QApplication을 먼저 만들어야 입력창/메시지박스를 띄울 수 있음
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # [0] backup_id.txt 선확보 (백업/로그인 등 다른 작업보다 먼저)
    _ensure_backup_id_or_exit(app)

    # 이후부터 기존 로직 수행
    setup_logging()
    db = DB(DB_PATH)

    # [1] 프로그램 시작 시 자동 백업 (PC + 구글드라이브)
    print("[System] 시작 자동 백업 실행 중...")
    backup_manager.run_backup("program_start")

    # [2] 6시간 주기 자동 백업 타이머 설정
    backup_timer = QTimer()
    interval = 6 * 60 * 60 * 1000  # 6시간
    backup_timer.timeout.connect(lambda: backup_manager.run_backup("periodic_6h"))
    backup_timer.start(interval)

    win = MainWindow(db)
    win.show()

    rc = app.exec_()
    db.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
