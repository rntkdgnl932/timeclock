# timeclock/settings.py
# -*- coding: utf-8 -*-
from pathlib import Path

APP_NAME = "근로시간 관리 프로그램"

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "app_data"
DB_PATH = DATA_DIR / "timeclock.db"
LOG_PATH = DATA_DIR / "app.log"
CONFIG_PATH = DATA_DIR / "config.json"
EXPORT_DIR = DATA_DIR / "exports"
BACKUP_DIR = DATA_DIR / "backups"
ARCHIVE_DIR = DATA_DIR / "archives"

DEFAULT_OWNER_USER = "owner"
DEFAULT_OWNER_PASS = "admin1234"
DEFAULT_WORKER_USER = "worker"
DEFAULT_WORKER_PASS = "worker1234"

# 요청 유형
REQ_TYPES = {
    "IN": "출근",
    "OUT": "퇴근",
}

# 요청 상태
REQ_STATUS = {
    "PENDING": "미승인",
    "APPROVED": "승인 완료",
}

# 승인(정정) 사유
REASON_CODES = {
    "PREP_DELAY": "준비 지연으로 실제 시작 시각 반영",
    "EARLY_OUT": "조기 퇴근으로 실제 종료 시각 반영",
    "LATE_IN": "지각",
    "OTHER": "기타",
}

# 이의 상태
DISPUTE_STATUS = {
    "OPEN": "접수됨",
    "IN_PROGRESS": "검토 중",
    "RESOLVED": "처리 완료",
    "REJECTED": "기각",
}

# 이의 처리 상태 선택용 (UI 표시 한글 / 저장은 코드)
DISPUTE_STATUS_ITEMS = [
    ("IN_PROGRESS", "검토 중"),
    ("RESOLVED", "처리 완료"),
    ("REJECTED", "기각"),
]

