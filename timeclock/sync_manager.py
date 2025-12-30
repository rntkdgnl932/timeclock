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


def download_latest_db():
    """
    [핵심 수정 사항]
    1. 구글 드라이브 파일 목록을 '수정일(modifiedDate)' 기준으로 내림차순 정렬
    2. 무조건 가장 최신 파일(0번 인덱스)을 다운로드
    3. 나머지 중복 파일은 휴지통으로 이동 (꼬임 방지)
    """
    if not HAS_GOOGLE_DRIVE:
        return False, "PyDrive 미설치"

    try:
        drive = _get_drive()
        if not drive:
            return False, "구글 인증 실패"

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)

        # 폴더 내 DB 파일 검색
        query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()

        if not file_list:
            return True, "서버에 DB 없음 (초기 상태)"

        # ✅ [수정됨] 최신순 정렬 (이게 없어서 옛날 파일을 가져왔던 것임)
        file_list.sort(key=lambda x: x['modifiedDate'], reverse=True)

        # 가장 최신 파일
        latest_file = file_list[0]

        # ✅ [청소] 중복 파일 정리
        if len(file_list) > 1:
            logging.warning(f"[Sync] 중복 파일 {len(file_list)}개 발견. 정리 중...")
            for old_file in file_list[1:]:
                try:
                    old_file.Trash()
                except:
                    pass

        # 다운로드 (임시 파일)
        temp_path = str(DB_PATH) + ".temp"
        latest_file.GetContentFile(temp_path)

        # 덮어쓰기 시도
        if os.path.exists(temp_path):
            try:
                shutil.move(temp_path, str(DB_PATH))
                logging.info(f"[Sync] DB 업데이트 완료 (Date: {latest_file['modifiedDate']})")
                return True, "동기화 완료"
            except PermissionError:
                # 윈도우 파일 잠금 문제 발생 시
                return False, "파일이 사용 중입니다. 프로그램을 완전히 껐다가 켜주세요."

    except Exception as e:
        logging.error(f"[Sync] 다운로드 실패: {e}")
        return False, str(e)

    return False, "알 수 없는 오류"


def is_cloud_newer():
    """
    [신규 함수] 구글 드라이브(Cloud)의 파일이 내 PC(Local)보다 최신인지 확인
    True 리턴 시: 클라우드가 더 최신임 -> 업로드 중단해야 함
    """
    if not HAS_GOOGLE_DRIVE:
        return False

    try:
        drive = _get_drive()
        if not drive:
            return False

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)
        query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()

        if not file_list:
            # 클라우드에 파일이 없으면 내께 최신(또는 최초)이라고 판단
            return False

        # 1. 클라우드 파일 시간 파싱 (가장 최신 파일 기준)
        file_list.sort(key=lambda x: x['modifiedDate'], reverse=True)
        remote_file = file_list[0]
        remote_time_str = remote_file['modifiedDate']  # 예: '2025-01-01T12:00:00.123Z'

        # 문자열 -> datetime 변환 (소수점 제거 후 파싱)
        # 구글 드라이브 시간 포맷은 ISO 8601 (UTC)
        dt_part = remote_time_str.split('.')[0]
        remote_dt = datetime.datetime.strptime(dt_part, "%Y-%m-%dT%H:%M:%S")

        # UTC 타임스탬프로 변환
        remote_ts = remote_dt.replace(tzinfo=datetime.timezone.utc).timestamp()

        # 2. 로컬 파일 시간 확인
        if not DB_PATH.exists():
            return True  # 로컬 파일 없으면 클라우드가 최신 취급

        local_ts = os.path.getmtime(DB_PATH)

        # 3. 비교 (클라우드가 5초 이상 미래면 최신으로 인정 - 시간차 오차 고려)
        if remote_ts > local_ts + 5:
            logging.warning(f"[Sync] 클라우드가 더 최신임 (Cloud: {remote_ts} > Local: {local_ts})")
            return True

        return False

    except Exception as e:
        logging.error(f"[Sync] 시간 비교 실패: {e}")
        # 에러 나면 안전을 위해 업로드 하지 않도록 True 반환하거나, 정책에 따라 변경 가능
        return False

def upload_current_db():
    """
    [핵심 수정 사항]
    1. 업로드 전 is_cloud_newer() 체크 -> 구글이 더 최신이면 업로드 중단 (덮어쓰기 방지)
    2. 업로드 할 때도 '가장 최신 파일'을 찾아서 덮어쓰기 (중복 생성 방지)
    """
    # -----------------------------------------------------------
    # [방어 로직] 클라우드가 더 최신이면 업로드 절대 금지
    # -----------------------------------------------------------
    if is_cloud_newer():
        logging.warning("[Sync] 클라우드 DB가 더 최신입니다. 업로드를 취소합니다. (먼저 다운로드 필요)")
        return False

    if not HAS_GOOGLE_DRIVE:
        return False

    try:
        drive = _get_drive()
        if not drive:
            return False

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)

        # 기존 파일 검색
        query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()

        gfile = None
        if file_list:
            # 최신 파일 찾아서 업데이트 타겟으로 설정
            file_list.sort(key=lambda x: x['modifiedDate'], reverse=True)
            gfile = file_list[0]

            # 나머지 찌꺼기 정리
            if len(file_list) > 1:
                for old_f in file_list[1:]:
                    try:
                        old_f.Trash()
                    except:
                        pass
        else:
            # 없으면 새로 생성
            gfile = drive.CreateFile({'title': GDRIVE_DB_FILENAME, 'parents': [{'id': folder_id}]})

        # 업로드 수행
        gfile.SetContentFile(str(DB_PATH))
        gfile.Upload()
        logging.info("[Sync] DB 업로드 완료")
        return True

    except Exception as e:
        logging.error(f"[Sync] 업로드 실패: {e}")
        return False








    #