# timeclock_app.py
# -*- coding: utf-8 -*-
import sys
import re
from PyQt5 import QtWidgets
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


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # [0] backup_id 확인
    _ensure_backup_id_or_exit(app)

    setup_logging()
    db = DB(DB_PATH)

    # 🔴 [삭제됨] 여기서 백업을 실행하면 안 됩니다! (화면이 뜨기 전이라 에러 발생/멈춤 원인)
    # print("[System] 시작 자동 백업 실행 중...")  <-- 삭제
    # backup_manager.run_backup("program_start")    <-- 삭제

    # [1] 6시간 주기 자동 백업 타이머 (이건 백그라운드라 유지해도 괜찮음)
    backup_timer = QTimer()
    interval = 6 * 60 * 60 * 1000
    backup_timer.timeout.connect(lambda: backup_manager.run_backup("periodic_6h"))
    backup_timer.start(interval)

    win = MainWindow(db)
    win.show()

    # 메인 루프 실행
    rc = app.exec_()
    db.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()