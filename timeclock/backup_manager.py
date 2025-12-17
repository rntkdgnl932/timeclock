# timeclock/backup_manager.py
# -*- coding: utf-8 -*-
import shutil
import os
import logging
from datetime import datetime
from pathlib import Path
from timeclock.settings import DB_PATH, BACKUP_DIR, APP_DIR

# -----------------------------------------------------------
# [설정] 파일 경로 절대 경로로 고정
# -----------------------------------------------------------
SECRETS_FILE = APP_DIR / "client_secrets.json"
CREDS_FILE = APP_DIR / "mycreds.txt"
GDRIVE_FOLDER_NAME = "timeclock_backup"  # ★ 구글 드라이브 저장 폴더명

HAS_GOOGLE_DRIVE = False
GoogleAuth = None
GoogleDrive = None

try:
    from pydrive.auth import GoogleAuth
    from pydrive.drive import GoogleDrive

    HAS_GOOGLE_DRIVE = True
except ImportError:
    pass


def run_backup(reason="auto"):
    """DB 백업 수행"""
    try:
        if not BACKUP_DIR.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{now_str}_{reason}.db"
        target_path = BACKUP_DIR / filename

        # 로컬 백업
        shutil.copy2(DB_PATH, target_path)
        msg = f"백업 완료: {filename}"
        print(f"[Backup] {msg}")

        # 구글 드라이브 백업
        if HAS_GOOGLE_DRIVE:
            try:
                upload_to_gdrive(target_path, filename)
                msg += " (+Google Drive)"
            except Exception as e:
                print(f"[Google Drive Error] {e}")

        return True, msg
    except Exception as e:
        return False, f"백업 실패: {e}"


def get_backup_list():
    """백업 목록 조회 (최신순 정렬)"""
    if not BACKUP_DIR.exists(): return []
    files = []
    for f in BACKUP_DIR.glob("*.db"):
        try:
            stat = f.stat()
            parts = f.stem.split("_")
            time_part = f"{parts[0]}_{parts[1]}"
            reason_part = "_".join(parts[2:])
            dt = datetime.strptime(time_part, "%Y%m%d_%H%M%S")
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            stat = f.stat()
            time_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            reason_part = "unknown"

        size_kb = round(stat.st_size / 1024, 1)
        files.append({
            "filename": f.name, "path": str(f), "time": time_str,
            "reason": reason_part, "size": f"{size_kb} KB", "timestamp": stat.st_mtime
        })

    # ★ [정렬] timestamp 기준 내림차순 (큰 숫자가 위로 = 최신이 위로)
    files.sort(key=lambda x: x["timestamp"], reverse=True)
    return files


def restore_backup(filename):
    """복구"""
    source_path = BACKUP_DIR / filename
    if not source_path.exists(): return False, "파일 없음"
    try:
        run_backup("restore_safety")
        shutil.copy2(source_path, DB_PATH)
        return True, "복구 성공"
    except Exception as e:
        return False, f"실패: {e}"


def authenticate_gdrive():
    """구글 연동(로그인)"""
    if not HAS_GOOGLE_DRIVE: return False, "PyDrive 미설치"
    if not SECRETS_FILE.exists(): return False, f"파일 없음: {SECRETS_FILE}"

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


def upload_to_gdrive(file_path, filename):
    """실제 업로드 (폴더 지정 기능 추가)"""
    if not HAS_GOOGLE_DRIVE or GoogleAuth is None: return
    if not SECRETS_FILE.exists(): return
    if not CREDS_FILE.exists(): return

    try:
        gauth = GoogleAuth()
        gauth.settings['client_config_file'] = str(SECRETS_FILE)
        gauth.LoadCredentialsFile(str(CREDS_FILE))

        if gauth.credentials is None: return

        if gauth.access_token_expired:
            gauth.Refresh()
            gauth.SaveCredentialsFile(str(CREDS_FILE))
        else:
            gauth.Authorize()

        drive = GoogleDrive(gauth)

        # -------------------------------------------------------
        # ★ [추가] 폴더 확인 및 생성 로직
        # -------------------------------------------------------
        folder_id = None
        # 1. 'timeclock_backup' 이름을 가진 폴더가 있는지 검색 (삭제된 것 제외)
        query = f"title = '{GDRIVE_FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()

        if file_list:
            # 있으면 그 폴더 ID 사용
            folder_id = file_list[0]['id']
        else:
            # 없으면 새로 생성
            folder_metadata = {
                'title': GDRIVE_FOLDER_NAME,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = drive.CreateFile(folder_metadata)
            folder.Upload()
            folder_id = folder['id']
            print(f"[GDrive] 새 폴더 생성됨: {GDRIVE_FOLDER_NAME}")

        # -------------------------------------------------------
        # 파일 업로드 (부모 폴더 지정)
        # -------------------------------------------------------
        gfile = drive.CreateFile({
            'title': filename,
            'parents': [{'id': folder_id}]  # ★ 여기가 핵심: 해당 폴더 안에 넣기
        })
        gfile.SetContentFile(str(file_path))
        gfile.Upload()
        print(f"[GDrive] Uploaded to folder: {filename}")

    except Exception as e:
        print(f"[GDrive Fail] {e}")
        raise e


def test_gdrive_upload():
    """테스트용 업로드"""
    try:
        if not HAS_GOOGLE_DRIVE: return False, "라이브러리 없음"

        test_file = BACKUP_DIR / "gdrive_test.txt"
        if not BACKUP_DIR.exists(): BACKUP_DIR.mkdir()
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(f"구글 드라이브 폴더 테스트 파일입니다.\n{datetime.now()}")

        upload_to_gdrive(test_file, "폴더테스트.txt")
        return True, f"성공! 구글 드라이브 '{GDRIVE_FOLDER_NAME}' 폴더를 확인하세요."
    except Exception as e:
        return False, f"실패: {e}"