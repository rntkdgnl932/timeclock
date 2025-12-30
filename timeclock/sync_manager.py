# timeclock/sync_manager.py
# -*- coding: utf-8 -*-
import os
import shutil
import logging
from pathlib import Path
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
    """인증 객체 생성 및 토큰 갱신 (에러 시 자동 삭제 및 재로그인 시도)"""
    if not HAS_GOOGLE_DRIVE:
        return None

    if not SECRETS_FILE.exists():
        logging.error(f"[Sync] client_secrets.json 없음: {SECRETS_FILE}")
        return None

    try:
        gauth = GoogleAuth()
        gauth.settings['client_config_file'] = str(SECRETS_FILE)

        # 1. 기존 토큰 로드 시도
        if CREDS_FILE.exists():
            try:
                gauth.LoadCredentialsFile(str(CREDS_FILE))
            except Exception:
                gauth.credentials = None

        # 2. 토큰 상태 확인 및 갱신
        if gauth.credentials is None:
            # 토큰이 없으면 바로 웹 로그인
            print("[Sync] 토큰 없음. 웹 로그인 시도...")
            gauth.LocalWebserverAuth()

        elif gauth.access_token_expired:
            # 만료된 경우 갱신 시도
            try:
                gauth.Refresh()
            except Exception as e:
                # 갱신 실패 (invalid_grant 등) -> 파일 삭제 후 재로그인 강제
                print(f"[Sync] 토큰 갱신 실패 ({e}). 재인증을 진행합니다.")

                # 파일 삭제
                if CREDS_FILE.exists():
                    try:
                        os.remove(str(CREDS_FILE))
                    except:
                        pass

                # ★ 핵심: 메모리 상의 죽은 자격증명 초기화
                gauth.credentials = None

                # 재로그인 창 띄우기
                gauth.LocalWebserverAuth()
        else:
            # 정상 토큰
            gauth.Authorize()

        # 3. 성공한 토큰 저장
        gauth.SaveCredentialsFile(str(CREDS_FILE))
        return gauth

    except Exception as e:
        logging.error(f"[Sync] 인증 치명적 오류: {e}")
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
        # 폴더 생성
        folder_metadata = {
            'title': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        folder = drive.CreateFile(folder_metadata)
        folder.Upload()
        return folder['id']


def download_latest_db():
    """
    구글 드라이브에서 '가장 최신' DB를 다운로드하고,
    중복된 옛날 파일이 있다면 정리합니다.
    """
    if not HAS_GOOGLE_DRIVE:
        return False, "PyDrive 미설치"

    try:
        drive = _get_drive()
        if not drive:
            return False, "구글 인증 실패"

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)

        # 해당 폴더 안에 있는 db 파일 찾기
        query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()

        if not file_list:
            return True, "서버에 DB 없음 (초기 상태)"

        # [핵심 수정] modifiedDate 기준으로 정렬 (최신순)
        # 구글 드라이브 API는 정렬을 보장하지 않으므로 수동 정렬 필수
        file_list.sort(key=lambda x: x['modifiedDate'], reverse=True)

        # 가장 최신 파일 가져오기
        latest_file = file_list[0]

        # [자동 청소] 만약 파일이 2개 이상이라면, 최신 파일 1개 빼고 나머지는 휴지통으로 보냄
        if len(file_list) > 1:
            logging.warning(f"[Sync] 중복 파일 {len(file_list)}개 발견. 최신본 제외하고 정리합니다.")
            for old_file in file_list[1:]:
                try:
                    old_file.Trash()  # 휴지통 이동
                except:
                    pass

        # 다운로드 (임시 파일로 먼저 받음)
        temp_path = str(DB_PATH) + ".temp"
        latest_file.GetContentFile(temp_path)

        # 안전하게 교체
        if os.path.exists(temp_path):
            # 기존 DB가 있다면 덮어쓰기
            shutil.move(temp_path, str(DB_PATH))
            logging.info(f"[Sync] 최신 DB 다운로드 완료 (Date: {latest_file['modifiedDate']})")
            return True, "동기화 완료"

    except Exception as e:
        logging.error(f"[Sync] 다운로드 실패: {e}")
        return False, str(e)

    return False, "알 수 없는 오류"


def upload_current_db():
    """
    현재 로컬 DB를 구글 드라이브에 강제 업로드 (덮어쓰기)
    """
    if not HAS_GOOGLE_DRIVE:
        return False

    try:
        drive = _get_drive()
        if not drive:
            return False

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)

        # 기존 파일 찾기
        query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()

        # [핵심 수정] 업로드 시에도 최신순 정렬해서 '가장 최신 파일'을 업데이트 타겟으로 삼음
        gfile = None
        if file_list:
            file_list.sort(key=lambda x: x['modifiedDate'], reverse=True)
            gfile = file_list[0]  # 업데이트할 파일 (최신본)

            # 나머지 중복 파일 정리
            if len(file_list) > 1:
                for old_f in file_list[1:]:
                    try:
                        old_f.Trash()
                    except:
                        pass
        else:
            # 없으면 신규 생성
            gfile = drive.CreateFile({'title': GDRIVE_DB_FILENAME, 'parents': [{'id': folder_id}]})

        # 내용 업로드
        gfile.SetContentFile(str(DB_PATH))
        gfile.Upload()
        logging.info("[Sync] DB 업로드 완료")
        return True

    except Exception as e:
        logging.error(f"[Sync] 업로드 실패: {e}")
        return False