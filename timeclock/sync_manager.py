# timeclock/sync_manager.py
# -*- coding: utf-8 -*-
import os
import shutil
import logging
from pathlib import Path
import datetime
from timeclock.settings import DB_PATH, APP_DIR
from timeclock.utils import now_str

# [설정] 구글 드라이브 경로 및 설정
SECRETS_FILE = APP_DIR / "client_secrets.json"
CREDS_FILE = APP_DIR / "mycreds.txt"
GDRIVE_SYNC_FOLDER_NAME = "timeclock_sync_data"
GDRIVE_DB_FILENAME = "timeclock.db"

HAS_GOOGLE_DRIVE = False
try:
    from pydrive.auth import GoogleAuth
    from pydrive.drive import GoogleDrive

    HAS_GOOGLE_DRIVE = True
except ImportError:
    pass


def _get_gauth():
    """인증 객체 생성 및 토큰 갱신"""
    if not HAS_GOOGLE_DRIVE:
        return None

    if not SECRETS_FILE.exists():
        logging.error(f"[Sync] client_secrets.json 없음: {SECRETS_FILE}")
        return None

    try:
        gauth = GoogleAuth()
        gauth.settings['client_config_file'] = str(SECRETS_FILE)

        # 1. 기존 토큰 로드
        if CREDS_FILE.exists():
            try:
                gauth.LoadCredentialsFile(str(CREDS_FILE))
            except Exception:
                gauth.credentials = None

        # 2. 토큰 갱신 또는 재로그인
        if gauth.credentials is None:
            print("[Sync] 토큰 없음. 웹 로그인 시도...")
            gauth.LocalWebserverAuth()
        elif gauth.access_token_expired:
            try:
                gauth.Refresh()
            except Exception as e:
                print(f"[Sync] 토큰 갱신 실패({e}). 재인증 진행.")
                if CREDS_FILE.exists():
                    os.remove(str(CREDS_FILE))
                gauth.credentials = None
                gauth.LocalWebserverAuth()
        else:
            gauth.Authorize()

        # 3. 토큰 저장
        gauth.SaveCredentialsFile(str(CREDS_FILE))
        return gauth

    except Exception as e:
        logging.error(f"[Sync] 인증 오류: {e}")
        return None


def _get_drive():
    gauth = _get_gauth()
    if gauth:
        return GoogleDrive(gauth)
    return None


def _get_folder_id(drive, folder_name):
    """폴더 ID 찾기 (없으면 생성)"""
    query = f"title = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    file_list = drive.ListFile({'q': query}).GetList()

    if file_list:
        return file_list[0]['id']
    else:
        folder_metadata = {
            'title': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = drive.CreateFile(folder_metadata)
        folder.Upload()
        return folder['id']

# --- [추가] 충돌 방지용 로컬 마커(마지막 클라우드 동기화 시각) 관리 ---

def _sync_marker_path() -> Path:
    # DB_PATH가 app_data/timeclock.db 라면, app_data/last_cloud_sync_ts.txt 로 저장
    return DB_PATH.parent / "last_cloud_sync_ts.txt"


def _load_last_sync_ts() -> int:
    """
    마지막으로 '클라우드 최신 DB를 받아온 시각(클라우드 modifiedDate)'을 epoch seconds로 저장/로드.
    없으면 0.
    """
    p = _sync_marker_path()
    try:
        if not p.exists():
            return 0
        s = p.read_text(encoding="utf-8").strip()
        if not s:
            return 0
        return int(s)
    except Exception:
        return 0


def _save_last_sync_ts(ts: int) -> None:
    try:
        p = _sync_marker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(int(ts)), encoding="utf-8")
    except Exception:
        pass


def _parse_gdrive_modified_date(modified_date_str: str) -> int:
    """
    PyDrive의 modifiedDate는 대개 ISO8601(예: '2025-12-30T10:05:12.123Z') 형태.
    이를 epoch seconds로 변환. 파싱 실패 시 0.
    """
    try:
        s = (modified_date_str or "").strip()
        if not s:
            return 0

        # 'Z' 처리
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"

        # datetime.fromisoformat은 마이크로초/타임존 포함을 지원
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # tz 없으면 UTC로 가정
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


def _get_cloud_db_file_and_ts(drive, folder_id: str):
    """
    클라우드의 timeclock.db 파일(최신 1개)과 modifiedDate(epoch seconds) 반환.
    중복 파일은 Trash 처리.
    """
    query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
    file_list = drive.ListFile({'q': query}).GetList()

    if not file_list:
        return None, 0

    file_list.sort(key=lambda x: x.get('modifiedDate', ''), reverse=True)
    gfile = file_list[0]

    # 중복 정리
    if len(file_list) > 1:
        for old_f in file_list[1:]:
            try:
                old_f.Trash()
            except Exception:
                pass

    remote_ts = _parse_gdrive_modified_date(gfile.get('modifiedDate', ''))
    return gfile, remote_ts


def cloud_changed_since_last_sync() -> bool:
    """
    '마지막으로 내가 받아온 클라우드 버전' 이후에 클라우드가 바뀌었는지 검사.
    True면 업로드 금지(덮어쓰기 위험).
    """
    if not HAS_GOOGLE_DRIVE:
        return False

    try:
        drive = _get_drive()
        if not drive:
            return False

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)
        _, remote_ts = _get_cloud_db_file_and_ts(drive, folder_id)

        last_ts = _load_last_sync_ts()
        if last_ts <= 0:
            # 마커가 없으면 "동기화 이력 불명" -> 안전하게 변경된 것으로 간주
            return True

        return remote_ts > last_ts
    except Exception:
        # 실패 시 업로드를 막아야 안전
        return True


def download_latest_db():
    """
    [동작]
    - 클라우드(timeclock_sync_data/timeclock.db)가 존재하면 최신 1개를 내려받아 DB_PATH에 덮어씀
    - 내려받은 뒤, '클라우드 modifiedDate'를 로컬 마커(last_cloud_sync_ts.txt)에 저장
    - 클라우드에 파일이 없으면(최초) 로컬 DB가 있으면 그대로 두고 False 반환
    """
    if not HAS_GOOGLE_DRIVE:
        return False, "PyDrive 미설치"

    try:
        drive = _get_drive()
        if not drive:
            return False, "구글 드라이브 인증 실패"

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)
        gfile, remote_ts = _get_cloud_db_file_and_ts(drive, folder_id)

        if not gfile:
            return False, "클라우드 DB 없음"

        # 다운로드
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        gfile.GetContentFile(str(DB_PATH))

        # ★ 중요: 마지막으로 받아온 클라우드 버전 기록
        if remote_ts > 0:
            _save_last_sync_ts(remote_ts)

        return True, f"클라우드 최신 DB 다운로드 완료 ({datetime.datetime.fromtimestamp(remote_ts).isoformat() if remote_ts else 'unknown'})"

    except Exception as e:
        logging.error(f"[Sync] 다운로드 실패: {e}")
        return False, str(e)


def is_cloud_newer():
    """
    기존 로직(로컬 mtime vs 클라우드 modifiedDate) 기반의 '신규 여부' 판단은
    덮어쓰기 사고를 유발하므로,
    여기서는 '클라우드가 마지막 동기화 이후 변경되었는지'로 대체합니다.
    """
    return cloud_changed_since_last_sync()


def upload_current_db():
    """
    [중요] 업로드 전에 충돌 검사:
    - last_cloud_sync_ts.txt(내가 마지막으로 받은 클라우드 버전) 이후에
      클라우드 DB가 변경되었으면 업로드를 막는다.
    - 막았을 때는 사용자가 먼저 download_latest_db()를 수행해야 한다.
    """
    if not HAS_GOOGLE_DRIVE:
        return False

    try:
        drive = _get_drive()
        if not drive:
            return False

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)

        # ★ 핵심: 충돌 감지(덮어쓰기 방지)
        # 마커가 없거나, 클라우드가 그 이후 변경되었으면 업로드 금지
        if cloud_changed_since_last_sync():
            logging.warning(
                "[Sync] 업로드 차단: 클라우드 DB가 마지막 동기화 이후 변경되었습니다. "
                "먼저 클라우드 최신 DB를 다운로드(download_latest_db)한 뒤 다시 시도하세요."
            )
            return False

        # 기존 파일 검색(없으면 새로 생성)
        query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()

        gfile = None
        if file_list:
            file_list.sort(key=lambda x: x.get('modifiedDate', ''), reverse=True)
            gfile = file_list[0]
            if len(file_list) > 1:
                for old_f in file_list[1:]:
                    try:
                        old_f.Trash()
                    except Exception:
                        pass
        else:
            gfile = drive.CreateFile({'title': GDRIVE_DB_FILENAME, 'parents': [{'id': folder_id}]})

        gfile.SetContentFile(str(DB_PATH))
        gfile.Upload()

        # ★ 업로드 성공 후: 방금 업로드된 클라우드 modifiedDate를 마커로 저장
        remote_ts = _parse_gdrive_modified_date(gfile.get('modifiedDate', ''))
        if remote_ts > 0:
            _save_last_sync_ts(remote_ts)

        logging.info(f"[Sync] 업로드 완료: {GDRIVE_DB_FILENAME}")
        return True

    except Exception as e:
        logging.error(f"[Sync] 업로드 실패: {e}")
        return False



#