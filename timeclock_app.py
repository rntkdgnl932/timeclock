# timeclock_app.py
# -*- coding: utf-8 -*-
import sys
from PyQt5 import QtWidgets

from timeclock.utils import setup_logging
from timeclock.settings import DB_PATH, APP_NAME
from timeclock.db import DB
from ui.main_window import MainWindow


def main():
    setup_logging()
    db = DB(DB_PATH)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    win = MainWindow(db)
    win.show()

    rc = app.exec_()
    db.close()
    sys.exit(rc)


if __name__ == "__main__":
    main()
