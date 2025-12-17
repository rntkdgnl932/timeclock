# timeclock/backup_manager.py
# -*- coding: utf-8 -*-
import shutil
from datetime import datetime
from pathlib import Path
from timeclock.settings import DB_PATH, BACKUP_DIR, APP_DIR

# -----------------------------------------------------------
# [설정] 파일 경로 절대 경로로 고정
# -----------------------------------------------------------
SECRETS_FILE = APP_DIR / "client_secrets.json"
CREDS_FILE = APP_DIR / "mycreds.txt"
GDRIVE_FOLDER_NAME = "timeclock_backup"  # ★ 구글 드라이브 저장 폴더명

# backup_id.txt는 app_data(=BACKUP_DIR의 상위) 아래에 둔다
BACKUP_ID_FILE = BACKUP_DIR.parent / "backup_id.txt"

HAS_GOOGLE_DRIVE = False
GoogleAuth = None
GoogleDrive = None

try:
    from pydrive.auth import GoogleAuth
    from pydrive.drive import GoogleDrive
    HAS_GOOGLE_DRIVE = True
except ImportError:
    pass


# ---------------------------
# backup_id helpers
# ---------------------------
def get_backup_id_file_path() -> Path:
    return BACKUP_ID_FILE


def read_backup_id() -> str:
    try:
        if not BACKUP_ID_FILE.exists():
            return ""
        return BACKUP_ID_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def write_backup_id(backup_id: str):
    try:
        backup_id = (backup_id or "").strip()
        if not backup_id:
            return False, "backup_id가 비어 있습니다."

        # app_data 디렉토리 보장
        BACKUP_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        BACKUP_ID_FILE.write_text(backup_id, encoding="utf-8")
        return True, "OK"
    except Exception as e:
        return False, str(e)


# ---------------------------
# local backup / restore
# ---------------------------
def run_backup(reason="auto"):
    """DB 백업 수행 (BACKUP_DIR/{backup_id}/filename)"""
    try:
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        backup_id = read_backup_id() or "unknown"
        target_dir = BACKUP_DIR / backup_id
        target_dir.mkdir(parents=True, exist_ok=True)

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{now_str}_{reason}.db"
        target_path = target_dir / filename

        # 로컬 백업
        shutil.copy2(DB_PATH, target_path)
        msg = f"백업 완료: {backup_id}/{filename}"
        print(f"[Backup] {msg}")

        # 구글 드라이브 백업
        if HAS_GOOGLE_DRIVE:
            try:
                upload_to_gdrive(target_path, filename, backup_id=backup_id)
                msg += " (+Google Drive)"
            except Exception as e:
                print(f"[Google Drive Error] {e}")

        return True, msg
    except Exception as e:
        return False, f"백업 실패: {e}"


def get_backup_list():
    """백업 목록 조회 (최신순 정렬) - 하위 폴더(backup_id) 포함"""
    if not BACKUP_DIR.exists():
        return []

    files = []

    # 1) 신규 구조: BACKUP_DIR/{backup_id}/*.db
    for f in BACKUP_DIR.glob("*/*.db"):
        _append_backup_item(files, f)

    # 2) 구버전 호환: BACKUP_DIR/*.db
    for f in BACKUP_DIR.glob("*.db"):
        _append_backup_item(files, f)

    files.sort(key=lambda x: x["timestamp"], reverse=True)
    return files


def _append_backup_item(files_list, f: Path):
    try:
        stat = f.stat()
        parts = f.stem.split("_")
        time_part = f"{parts[0]}_{parts[1]}"
        reason_part = "_".join(parts[2:])
        dt = datetime.strptime(time_part, "%Y%m%d_%H%M%S")
        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        stat = f.stat()
        time_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        reason_part = "unknown"

    size_kb = round(stat.st_size / 1024, 1)

    # backup_id 추정: BACKUP_DIR 바로 아래면 parent.name이 backup_id
    backup_id = ""
    try:
        if f.parent.resolve() != BACKUP_DIR.resolve():
            backup_id = f.parent.name
    except Exception:
        pass

    files_list.append({
        "filename": f.name,
        "path": str(f),
        "time": time_str,
        "reason": reason_part,
        "size": f"{size_kb} KB",
        "timestamp": stat.st_mtime,
        "backup_id": backup_id
    })


def restore_backup(filename):
    """복구 (신규/구버전 경로 모두 지원)"""
    # filename이 'backup_id/xxx.db' 형태일 수도 있고, 그냥 'xxx.db' 일 수도 있음
    source_path = None

    # 1) 경로가 포함된 경우 우선 처리
    if ("/" in filename) or ("\\" in filename):
        candidate = BACKUP_DIR / filename
        if candidate.exists():
            source_path = candidate

    # 2) 파일명만 온 경우: 하위 폴더 + 루트에서 탐색 (동명이인이면 최신 1개)
    if source_path is None:
        candidates = []
        for f in BACKUP_DIR.glob("*/*.db"):
            if f.name == filename:
                candidates.append(f)
        for f in BACKUP_DIR.glob("*.db"):
            if f.name == filename:
                candidates.append(f)

        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            source_path = candidates[0]

    if source_path is None or not source_path.exists():
        return False, "파일 없음"

    try:
        run_backup("restore_safety")
        shutil.copy2(source_path, DB_PATH)
        return True, "복구 성공"
    except Exception as e:
        return False, f"실패: {e}"


# ---------------------------
# Google Drive
# ---------------------------
def authenticate_gdrive():
    """구글 연동(로그인)"""
    if not HAS_GOOGLE_DRIVE:
        return False, "PyDrive 미설치"
    if not SECRETS_FILE.exists():
        return False, f"파일 없음: {SECRETS_FILE}"

    try:
        gauth = GoogleAuth()
        gauth.settings['client_config_file'] = str(SECRETS_FILE)

        if CREDS_FILE.exists():
            gauth.LoadCredentialsFile(str(CREDS_FILE))

        if gauth.credentials is None:
            gauth.LocalWebserverAuth()
        elif gauth.access_token_expired:
            gauth.Refresh()
        else:
            gauth.Authorize()

        gauth.SaveCredentialsFile(str(CREDS_FILE))
        return True, "구글 드라이브 연동 성공!"
    except Exception as e:
        return False, f"인증 실패: {e}"


def _get_or_create_folder(drive, title: str, parent_id: str = None) -> str:
    """
    제목(title)과 부모(parent_id)에 해당하는 폴더를 찾고 없으면 생성 후 id 반환
    """
    if parent_id:
        query = (
            "title = '{title}' and mimeType = 'application/vnd.google-apps.folder' "
            "and trashed = false and '{parent}' in parents"
        ).format(title=title.replace("'", "\\'"), parent=parent_id)
    else:
        query = (
            "title = '{title}' and mimeType = 'application/vnd.google-apps.folder' "
            "and trashed = false"
        ).format(title=title.replace("'", "\\'"))

    file_list = drive.ListFile({'q': query}).GetList()
    if file_list:
        return file_list[0]['id']

    folder_metadata = {
        'title': title,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        folder_metadata['parents'] = [{'id': parent_id}]

    folder = drive.CreateFile(folder_metadata)
    folder.Upload()
    print(f"[GDrive] 새 폴더 생성됨: {title}" + (f" (parent={parent_id})" if parent_id else ""))
    return folder['id']


def upload_to_gdrive(file_path, filename, backup_id: str = None):
    """
    실제 업로드:
    - 루트 폴더: GDRIVE_FOLDER_NAME
    - 하위 폴더: backup_id
    - 업로드 위치: GDRIVE_FOLDER_NAME/backup_id/filename
    """
    if not HAS_GOOGLE_DRIVE or GoogleAuth is None:
        return
    if not SECRETS_FILE.exists():
        return
    if not CREDS_FILE.exists():
        return

    try:
        backup_id = (backup_id or read_backup_id() or "unknown").strip()

        gauth = GoogleAuth()
        gauth.settings['client_config_file'] = str(SECRETS_FILE)
        gauth.LoadCredentialsFile(str(CREDS_FILE))

        if gauth.credentials is None:
            return

        if gauth.access_token_expired:
            gauth.Refresh()
            gauth.SaveCredentialsFile(str(CREDS_FILE))
        else:
            gauth.Authorize()

        drive = GoogleDrive(gauth)

        # 1) timeclock_backup 폴더 확보
        root_id = _get_or_create_folder(drive, GDRIVE_FOLDER_NAME, parent_id=None)

        # 2) timeclock_backup/{backup_id} 폴더 확보
        child_id = _get_or_create_folder(drive, backup_id, parent_id=root_id)

        # 3) 업로드
        gfile = drive.CreateFile({
            'title': filename,
            'parents': [{'id': child_id}]
        })
        gfile.SetContentFile(str(file_path))
        gfile.Upload()
        print(f"[GDrive] Uploaded to folder: {GDRIVE_FOLDER_NAME}/{backup_id}/{filename}")

    except Exception as e:
        print(f"[GDrive Fail] {e}")
        raise e


def test_gdrive_upload():
    """테스트용 업로드 (backup_id 폴더 하위로 업로드)"""
    try:
        if not HAS_GOOGLE_DRIVE:
            return False, "라이브러리 없음"

        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        backup_id = read_backup_id() or "unknown"
        test_dir = BACKUP_DIR / backup_id
        test_dir.mkdir(parents=True, exist_ok=True)

        test_file = test_dir / "gdrive_test.txt"
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(f"구글 드라이브 폴더 테스트 파일입니다.\n{datetime.now()}\nbackup_id={backup_id}")

        upload_to_gdrive(test_file, "폴더테스트.txt", backup_id=backup_id)
        return True, f"성공! 구글 드라이브 '{GDRIVE_FOLDER_NAME}/{backup_id}' 폴더를 확인하세요."
    except Exception as e:
        return False, f"실패: {e}"
