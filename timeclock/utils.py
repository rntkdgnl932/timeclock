# timeclock/utils.py
# -*- coding: utf-8 -*-
import sys
import json
import logging
from datetime import datetime
from PyQt5 import QtWidgets, QtCore

from timeclock.settings import DATA_DIR, LOG_PATH, CONFIG_PATH, EXPORT_DIR, BACKUP_DIR, ARCHIVE_DIR


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging():
    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(str(LOG_PATH), encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def qdate_to_str(d: QtCore.QDate) -> str:
    return d.toString("yyyy-MM-dd")


def normalize_date_range(date_from: str, date_to: str) -> tuple[str, str]:
    if not date_from:
        date_from = "1970-01-01"
    if not date_to:
        date_to = "2999-12-31"
    return date_from, date_to


def load_config() -> dict:
    ensure_dirs()
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: dict):
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class Message:
    @staticmethod
    def info(parent, title: str, text: str):
        QtWidgets.QMessageBox.information(parent, title, text)

    @staticmethod
    def warn(parent, title: str, text: str):
        QtWidgets.QMessageBox.warning(parent, title, text)

    @staticmethod
    def err(parent, title: str, text: str):
        QtWidgets.QMessageBox.critical(parent, title, text)

    @staticmethod
    def confirm(parent, title: str, text: str) -> bool:
        """예/아니오 확인 메시지 다이얼로그"""
        reply = QtWidgets.QMessageBox.question(
            parent,
            title,
            text,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        return reply == QtWidgets.QMessageBox.Yes