# timeclock/sync_manager.py
# -*- coding: utf-8 -*-
import os
import shutil
import logging
from pathlib import Path
import datetime
from timeclock.settings import DB_PATH, APP_DIR, _MIN_CALL_INTERVAL_SEC
from timeclock.utils import now_str
import requests  # [추가] 다운로드 통신용
import time      # [추가] 캐시방지 시간생성용
import threading

# ✅ 동기화(다운/업) 동시 실행 방지 + 폭주 방지(크래시 방어)
_SYNC_LOCK = threading.RLock()
_LAST_DL_CALL_TS = 0.0
_LAST_UL_CALL_TS = 0.0
_MIN_CALL_INTERVAL_SEC = 3.0



# [설정] 구글 드라이브 경로 및 설정
SECRETS_FILE = APP_DIR / "client_secrets.json"
CREDS_FILE = APP_DIR / "mycreds.txt"
GDRIVE_SYNC_FOLDER_NAME = "timeclock_sync_data_v2"
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
    중복 파일이 있으면 오래된 것들은 Trash 처리.
    반환: (gfile, remote_ts_epoch) 또는 (None, 0)
    """
    try:
        query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()

        if not file_list:
            return None, 0

        # 최신 modifiedDate 우선
        file_list.sort(key=lambda x: x.get('modifiedDate', ''), reverse=True)
        gfile = file_list[0]

        # 중복 정리(최신 1개만 남김)
        if len(file_list) > 1:
            for old_f in file_list[1:]:
                try:
                    old_f.Trash()
                except Exception:
                    pass

        remote_ts = _iso_to_epoch(gfile.get("modifiedDate"))
        return gfile, remote_ts
    except Exception:
        return None, 0


def _find_file_in_folder(drive, folder_id: str, filename: str):
    """
    특정 폴더(folder_id) 안에서 title=filename 인 파일을 1개 찾아 반환.
    없으면 None.
    중복이면 최신(modifiedDate) 1개만 남기고 나머지는 Trash 처리.
    """
    try:
        query = f"'{folder_id}' in parents and title = '{filename}' and trashed = false"
        file_list = drive.ListFile({'q': query}).GetList()
        if not file_list:
            return None

        # 최신 1개 선택
        file_list.sort(key=lambda x: x.get('modifiedDate', ''), reverse=True)
        gfile = file_list[0]

        # 중복 정리
        if len(file_list) > 1:
            for old_f in file_list[1:]:
                try:
                    old_f.Trash()
                except Exception:
                    pass

        return gfile
    except Exception:
        return None


def cloud_changed_since_last_sync() -> bool:
    """
    '마지막으로 내가 받아온 클라우드 버전' 이후에 클라우드가 바뀌었는지 검사.
    True면 업로드 금지(덮어쓰기 위험).

    [수정]
    - last_cloud_sync_ts.txt(마커)가 없더라도,
      클라우드에 DB가 "아예 없는 경우(remote_ts==0)"에는 업로드를 허용한다.
    - 클라우드 DB가 존재하는데 마커가 없으면(동기화 이력 불명) -> 안전을 위해 업로드 금지.
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

        # ✅ 클라우드에 DB가 아예 없으면: 초기 업로드 허용
        if remote_ts <= 0:
            return False

        # ✅ 클라우드 DB는 있는데 마커가 없으면: 덮어쓰기 위험 -> 업로드 금지
        if last_ts <= 0:
            return True

        return remote_ts > last_ts
    except Exception:
        # 실패 시 업로드를 막아야 안전
        return True


def download_latest_db_snapshot():
    """
    클라우드의 timeclock.db를 '임시 스냅샷 파일'로만 다운로드하고,
    로컬 DB_PATH를 교체하지 않는다.
    반환: (Path(temp_db_path), remote_ts_epoch) 또는 (None, 0)
    """
    if not HAS_GOOGLE_DRIVE:
        return None, 0

    try:
        drive = _get_drive()
        if not drive:
            return None, 0

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)
        gfile, remote_ts = _get_latest_db_file_and_ts(drive, folder_id)
        if not gfile:
            return None, 0

        tmp_dir = Path(DB_PATH).parent / "_sync_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        temp_path = tmp_dir / f"{Path(DB_PATH).stem}.cloudsnap_{ts}{Path(DB_PATH).suffix}"

        # access token으로 v3 download (캐시 방지)
        access_token = drive.auth.credentials.access_token
        timestamp = int(time.time())
        file_id = gfile['id']
        download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&t={timestamp}"
        headers = {"Authorization": f"Bearer {access_token}"}

        logging.info(f"[Sync] snapshot download: {download_url}")
        resp = requests.get(download_url, headers=headers)

        if resp.status_code == 200:
            temp_path.write_bytes(resp.content)
            return temp_path, remote_ts

        # 실패 시 PyDrive fallback
        gfile.GetContentFile(str(temp_path))
        return temp_path, remote_ts

    except Exception as e:
        logging.error(f"[Sync] snapshot download failed: {e}")
        return None, 0

def _iso_to_epoch(iso_str: str) -> int:
    """
    2025-12-31T10:11:12.345Z 같은 ISO 시간을 epoch seconds로 변환.
    (안전: 파싱 실패 시 0)
    """
    try:
        from datetime import datetime, timezone
        s = (iso_str or "").strip()
        if not s:
            return 0
        # Z 처리
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0


def _get_latest_db_file_and_ts(*args, **kwargs):
    """
    구버전 코드 호환용 alias.
    기존 구현 함수명이 _get_cloud_db_file_and_ts 라면 그걸 호출.
    """
    fn = globals().get("_get_cloud_db_file_and_ts")
    if callable(fn):
        return fn(*args, **kwargs)
    raise NameError("_get_cloud_db_file_and_ts is not defined (cannot resolve latest db file)")


def download_latest_db(apply_replace: bool = True, temp_path: str = None):
    """
    - apply_replace=True (기존 동작): 다운로드 후 로컬 DB(DB_PATH)를 교체
    - apply_replace=False (안전 모드): 다운로드만 해서 temp_path(또는 자동 temp)로 저장하고,
      로컬 DB는 절대 건드리지 않음. (대화창 실시간 수신용)

    return:
      (True, <msg_or_path>) / (False, <error_message>)
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

        # temp_path 결정
        if not temp_path:
            # 안전 모드면 _sync_tmp에 저장하는 걸 권장
            if not apply_replace:
                sync_tmp = DB_PATH.parent / "_sync_tmp"
                sync_tmp.mkdir(parents=True, exist_ok=True)
                temp_path = str(sync_tmp / f"timeclock.dl_{now_str()}.db")
            else:
                temp_path = str(DB_PATH) + ".temp"

        # -------------------------------------------------------------
        # ✅ PyDrive의 GetContentFile 대신 requests 사용(캐시 무시)
        # -------------------------------------------------------------
        try:
            # PyDrive 인증 세션(토큰) 기반으로 직접 다운로드 URL 구성
            access_token = drive.auth.credentials.access_token
            # v3 alt=media 방식
            url = f"https://www.googleapis.com/drive/v3/files/{gfile['id']}?alt=media&t={int(time.time())}"
            headers = {"Authorization": f"Bearer {access_token}"}

            r = requests.get(url, headers=headers, stream=True, timeout=60)
            if r.status_code != 200:
                return False, f"다운로드 실패 HTTP {r.status_code}: {r.text[:200]}"

            with open(temp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        except Exception as e:
            return False, f"다운로드 예외: {e}"

        # -------------------------------------------------------------
        # ✅ 안전 모드: temp_path만 반환하고 끝 (로컬 DB 교체 금지)
        # -------------------------------------------------------------
        if not apply_replace:
            return True, temp_path

        # -------------------------------------------------------------
        # 기존 모드: 로컬 DB 교체
        # -------------------------------------------------------------
        try:
            # 교체는 원자적으로
            os.replace(temp_path, str(DB_PATH))

            # ✅ [핵심] “내가 이 클라우드 버전을 기반으로 작업한다” 마커 저장
            # 업로드 차단 루프를 끊기 위해 반드시 필요
            if remote_ts and remote_ts > 0:
                _save_last_sync_ts(remote_ts)

        except Exception as e:
            # 교체 실패 시 temp 보관
            try:
                pending_dir = DB_PATH.parent / "_sync_tmp"
                pending_dir.mkdir(parents=True, exist_ok=True)
                pending_path = str(pending_dir / f"timeclock.dl_pending_{now_str()}.db")
                os.replace(temp_path, pending_path)
                return False, f"로컬 DB 교체 실패(잠김). pending으로 보관: {e}"
            except Exception:
                return False, f"로컬 DB 교체 실패: {e}"

        return True, "클라우드 최신 DB 다운로드 완료"


    except Exception as e:
        return False, str(e)



def apply_pending_db_if_exists():
    """
    download_latest_db()가 DB 잠김으로 교체를 못 했을 때 생성한
    timeclock.db.pending 파일을, 잠금이 풀린 시점에 원자적으로 교체한다.
    """
    try:
        pending_path = DB_PATH.parent / f"{DB_PATH.name}.pending"
        if not pending_path.exists():
            return False

        # 잠깐 재시도
        for _ in range(6):
            try:
                os.replace(str(pending_path), str(DB_PATH))
                logging.info("[Sync] pending DB 적용 완료")
                return True
            except Exception:
                time.sleep(0.25)
        return False
    except Exception:
        return False


def is_cloud_newer():
    """
    기존 로직(로컬 mtime vs 클라우드 modifiedDate) 기반의 '신규 여부' 판단은
    덮어쓰기 사고를 유발하므로,
    여기서는 '클라우드가 마지막 동기화 이후 변경되었는지'로 대체합니다.
    """
    return cloud_changed_since_last_sync()


def upload_current_db(db_path: Path = None):
    """
    - db_path가 주어지면 해당 파일(스냅샷)을 업로드
    - 주어지지 않으면 기본 DB_PATH 업로드
    """
    global _LAST_UL_CALL_TS

    with _SYNC_LOCK:
        now = time.time()
        # ✅ settings.py에서 가져온 전역 설정값을 사용합니다.
        if now - _LAST_UL_CALL_TS < _MIN_CALL_INTERVAL_SEC:
            print(f"[Sync] 업로드 간격이 너무 짧습니다. (대기: {now - _LAST_UL_CALL_TS:.1f}s)")
            return False

        if not HAS_GOOGLE_DRIVE:
            return False

        try:
            drive = _get_drive()
            if not drive:
                return False

            folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)

            # 충돌 감지(덮어쓰기 방지)
            if cloud_changed_since_last_sync():
                logging.warning(
                    "[Sync] 업로드 차단: 클라우드 DB가 마지막 동기화 이후 변경되었습니다. "
                    "먼저 최신 DB를 다운로드(병합)한 뒤 다시 시도하세요."
                )
                return False

            _LAST_UL_CALL_TS = now
            upload_path = Path(db_path) if db_path else Path(DB_PATH)

            query = f"'{folder_id}' in parents and title = '{GDRIVE_DB_FILENAME}' and trashed = false"
            file_list = drive.ListFile({'q': query}).GetList()

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

            gfile.SetContentFile(str(upload_path))
            gfile.Upload()

            # 마커 저장
            try:
                gfile.FetchMetadata(fields="modifiedDate")
                remote_ts = _parse_gdrive_modified_date(gfile.get("modifiedDate", ""))
                if remote_ts and remote_ts > 0:
                    _save_last_sync_ts(remote_ts)
            except Exception:
                pass

            logging.info(f"[Sync] 업로드 완료: {GDRIVE_DB_FILENAME}")
            return True

        except Exception as e:
            logging.error(f"[Sync] 업로드 실패: {e}")
            return False



def run_startup_sync():
    """
    [핵심] 프로그램 시작 시 실행.
    구글 드라이브(Cloud) 시간이 내 컴퓨터(Local) 시간보다 최신이면
    묻지도 따지지도 않고 다운로드하여 DB를 덮어쓴다.
    """
    if not HAS_GOOGLE_DRIVE:
        print("[Startup] 구글 드라이브 모듈 없음.")
        return

    try:
        print("[Startup] 구글 드라이브 상태 확인 중...")
        drive = _get_drive()
        if not drive:
            print("[Startup] 인증 실패.")
            return

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)
        gfile, remote_ts = _get_cloud_db_file_and_ts(drive, folder_id)

        if not gfile:
            print("[Startup] 클라우드에 DB 파일이 없습니다. (첫 실행으로 간주)")
            return

        last_ts = _load_last_sync_ts()

        # ★ 비교 로직: 클라우드가 더 최신인가?
        if remote_ts > last_ts:
            print(f"[Startup] 새 데이터 발견! (Cloud: {remote_ts} > Local: {last_ts})")
            print("[Startup] 최신 DB를 다운로드합니다...")

            # 다운로드 실행
            success, msg = download_latest_db()
            if success:
                print(f"[Startup] 동기화 완료: {msg}")
            else:
                print(f"[Startup] 동기화 실패: {msg}")
        else:
            print("[Startup] 현재 데이터가 최신입니다. 다운로드 안 함.")

    except Exception as e:
        print(f"[Startup] 오류 발생: {e}")


# timeclock/sync_manager.py 기존 코드 맨 아래에 추가

def get_debug_info():
    """
    [UI 표시용] 로컬 DB와 클라우드 DB의 파일명/수정시간 정보를 조회하여 반환.
    (다운로드 로직과는 별개로 '정보 조회'만 수행)
    """
    import datetime

    info = {
        "local_name": "-", "local_time": "-",
        "cloud_name": "-", "cloud_time": "-",
        "status": "Check Failed"
    }

    # 1. 로컬 정보 조회
    if DB_PATH.exists():
        info["local_name"] = DB_PATH.name
        # timestamp -> datetime string
        ts = DB_PATH.stat().st_mtime
        dt = datetime.datetime.fromtimestamp(ts)
        info["local_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        info["local_name"] = "파일 없음"

    # 2. 클라우드 정보 조회
    if not HAS_GOOGLE_DRIVE:
        info["status"] = "Google Drive 모듈 없음"
        return info

    try:
        drive = _get_drive()
        if not drive:
            info["status"] = "인증 실패"
            return info

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)
        gfile, remote_ts = _get_cloud_db_file_and_ts(drive, folder_id)

        if gfile:
            info["cloud_name"] = gfile['title']
            # epoch seconds -> datetime string
            if remote_ts > 0:
                dt = datetime.datetime.fromtimestamp(remote_ts)
                info["cloud_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            else:
                info["cloud_time"] = "시간 정보 없음"
            info["status"] = "OK"
        else:
            info["cloud_name"] = "클라우드 파일 없음"
            info["status"] = "Cloud Empty"

    except Exception as e:
        info["status"] = f"Error: {e}"

    return info




#