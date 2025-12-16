# timeclock/backup_manager.py
import shutil
import os
from datetime import datetime
from pathlib import Path

# 1. 로컬 저장소 (내 컴퓨터 안전지대)
LOCAL_BACKUP_DIR = Path(r"C:\my_games\timeclock\app_data\backups")

# 2. 원격 저장소 (구글 드라이브)
# 데스크톱용 Google Drive가 설치된 경로 (보통 G:\내 드라이브)
REMOTE_BACKUP_DIR = Path(r"G:\내 드라이브\Timeclock_Backup")

DB_FILENAME = "timeclock.db"


def run_backup(reason="auto"):
    """
    현재 DB 상태를 로컬과 구글 드라이브에 이중 백업합니다.
    reason: 백업 원인 (파일명에 기록됨)
    """
    if not os.path.exists(DB_FILENAME):
        return False, "원본 데이터베이스가 없습니다."

    # 파일명 생성: timeclock_20251217_183000_reason.db
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"timeclock_{now_str}_{reason}.db"

    msg_list = []

    # [1] 로컬 백업
    try:
        if not LOCAL_BACKUP_DIR.exists():
            LOCAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        shutil.copy2(DB_FILENAME, LOCAL_BACKUP_DIR / filename)
        msg_list.append("내PC저장")
    except Exception as e:
        print(f"[백업오류-로컬] {e}")
        msg_list.append("로컬실패")

    # [2] 구글 드라이브 백업
    # 드라이브가 연결되어 있을 때만 시도
    if REMOTE_BACKUP_DIR.exists():
        try:
            shutil.copy2(DB_FILENAME, REMOTE_BACKUP_DIR / filename)
            msg_list.append("구글드라이브저장")
        except Exception as e:
            # 구글 드라이브 오류는 프로그램 중단 없이 로그만 남김
            print(f"[백업오류-원격] {e}")
            msg_list.append("원격실패")

    return True, ", ".join(msg_list)


def get_backup_list():
    """복구 화면에 보여줄 백업 파일 목록을 최신순으로 가져옵니다."""
    if not LOCAL_BACKUP_DIR.exists():
        return []

    files = list(LOCAL_BACKUP_DIR.glob("*.db"))
    data_list = []

    for f in files:
        # 파일명 분석: timeclock_날짜_시간_사유.db
        parts = f.stem.split('_', 3)  # 최대 4덩어리로 나눔

        display_reason = "알수없음"
        if len(parts) >= 4:
            raw = parts[3]
            # 보기 좋게 한글로 변환
            if raw == "program_start":
                display_reason = "프로그램 시작"
            elif raw == "periodic_6h":
                display_reason = "정기 자동백업(6H)"
            elif raw == "manual":
                display_reason = "사장님 수동저장"
            elif raw == "request_in":
                display_reason = "직원 출근"
            elif raw == "request_out":
                display_reason = "직원 퇴근"
            elif raw == "approve":
                display_reason = "관리자 승인/수정"
            elif raw == "before_restore":
                display_reason = "복구 직전 자동저장"
            else:
                display_reason = raw

        # 시간 (파일 수정시간 기준)
        dt = datetime.fromtimestamp(f.stat().st_mtime)
        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        # 용량 (KB)
        size_kb = f"{round(f.stat().st_size / 1024, 1)} KB"

        data_list.append({
            "filename": f.name,
            "time": time_str,
            "reason": display_reason,
            "size": size_kb,
            "sort_key": dt  # 정렬용
        })

    # 최신순 정렬
    data_list.sort(key=lambda x: x['sort_key'], reverse=True)
    return data_list


def restore_backup(backup_filename):
    """선택한 백업 파일로 데이터를 되돌립니다."""
    target_path = LOCAL_BACKUP_DIR / backup_filename

    if not target_path.exists():
        return False, "백업 파일을 찾을 수 없습니다."

    try:
        # 1. 안전장치: 덮어쓰기 전에 현재 상태를 한 번 더 저장함
        run_backup("before_restore")

        # 2. 복구 실행 (덮어쓰기)
        shutil.copy2(target_path, DB_FILENAME)
        return True, "복구 완료"
    except Exception as e:
        return False, f"복구 실패: {e}"