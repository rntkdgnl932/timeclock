# timeclock/sync_manager.py
# -*- coding: utf-8 -*-
import os
import shutil
import logging
from pathlib import Path
import datetime
from timeclock.settings import DB_PATH, APP_DIR
from timeclock.utils import now_str
import requests  # [추가] 다운로드 통신용
import time      # [추가] 캐시방지 시간생성용

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


def download_latest_db(force_cache_bust: bool = True):
    """
    클라우드 최신 DB를 다운로드하여 로컬 DB로 반영한다.

    [개선]
    - 기존 DB 파일을 직접 삭제하지 않는다(WinError 32 방지).
    - tmp로 다운로드 → os.replace(원자적 교체) 시도.
    - 교체 실패(잠김) 시 tmp를 .pending으로 보관하고, 다음에 다시 시도할 수 있게 한다.
    """
    if not HAS_GOOGLE_DRIVE:
        return False, "Google Drive 미사용"

    try:
        drive = _get_drive()
        if not drive:
            return False, "Google Drive 인증 실패"

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)
        gfile, remote_ts = _get_cloud_db_file_and_ts(drive, folder_id)
        if not gfile or remote_ts <= 0:
            return False, "클라우드 DB 없음"

        # 로컬 DB 경로
        local_path = DB_PATH
        local_dir = local_path.parent
        local_dir.mkdir(parents=True, exist_ok=True)

        # 다운로드 tmp/pending 경로
        tmp_dir = local_dir / "_sync_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        tmp_path = tmp_dir / f"{local_path.stem}.dl_{ts}{local_path.suffix}"
        pending_path = local_dir / f"{local_path.name}.pending"

        # 이미 pending이 있으면(과거 교체 실패 잔재) 우선 정리/백업
        if pending_path.exists():
            try:
                pending_path.unlink(missing_ok=True)
            except Exception:
                pass

        # ---- 다운로드: tmp_path로만 받는다 ----
        if force_cache_bust:
            try:
                # drive v3에서 종종 캐시 이슈가 있어 URL에 t 붙이는 로그를 남기던 흐름 유지
                print(f"[Sync] 캐시 무시 다운로드 요청: {gfile.get('downloadUrl', '')}&t={int(time.time())}")
            except Exception:
                pass

        try:
            gfile.GetContentFile(str(tmp_path))
        except Exception as e:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False, f"다운로드 실패: {e}"

        # ---- 교체 시도: os.replace(원자적) ----
        # Windows에서 sqlite가 파일을 잡고 있으면 replace도 막힐 수 있다.
        # 짧게 재시도 후에도 실패하면 pending으로 보관.
        last_err = None
        for _ in range(6):
            try:
                # 기존 파일이 있어도, 없어도 동작
                os.replace(str(tmp_path), str(local_path))
                last_err = None
                break
            except PermissionError as e:
                last_err = e
                time.sleep(0.25)
            except OSError as e:
                # WinError 32 포함
                last_err = e
                time.sleep(0.25)

        if last_err is not None:
            # 교체 실패: pending으로 보관 (사용자는 입력/업무 계속 가능)
            try:
                os.replace(str(tmp_path), str(pending_path))
            except Exception:
                # pending 저장도 실패하면 tmp만 남기고 종료
                pass

            logging.error(f"[Sync] 로컬 DB 교체 실패(잠김). pending으로 보관: {last_err}")
            return True, "클라우드 DB 다운로드 완료(대기중: DB 사용 중이라 교체 보류)"

        # 교체 성공 → 마커 저장
        _save_last_sync_ts(remote_ts)
        logging.info(f"[Sync] 클라우드 최신 DB 다운로드 완료 ({gfile.get('modifiedDate', '')})")
        return True, f"클라우드 최신 DB 다운로드 완료 ({gfile.get('modifiedDate', '')})"

    except Exception as e:
        logging.error(f"[Sync] 다운로드 실패: {e}")
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


def upload_current_db():
    """
    [중요] 업로드 전에 충돌 검사:
    - last_cloud_sync_ts.txt(내가 마지막으로 받은 클라우드 버전) 이후에
      클라우드 DB가 변경되었으면 업로드를 막는다.
    - 막았을 때는 사용자가 먼저 download_latest_db()를 수행해야 한다.

    [개선] DB 파일을 직접 업로드하지 않고, 로컬 DB를 임시 스냅샷으로 복사한 뒤 업로드한다.
    - SQLite가 열려 있어도(=프로그램 사용 중이어도) 업로드가 안정적으로 동작
    - UI에서 DB 연결을 끊을 필요가 없어져, 채팅 입력이 막히지 않는다.
    """
    if not HAS_GOOGLE_DRIVE:
        return False

    try:
        drive = _get_drive()
        if not drive:
            return False

        folder_id = _get_folder_id(drive, GDRIVE_SYNC_FOLDER_NAME)

        # ★ 핵심: 충돌 감지(덮어쓰기 방지)
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

        # -------------------------------
        # ✅ DB 스냅샷(임시 복사본) 만들어 업로드
        # -------------------------------
        snap_dir = DB_PATH.parent / "_sync_tmp"
        snap_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        snap_path = snap_dir / f"{DB_PATH.stem}.snapshot_{ts}{DB_PATH.suffix}"

        # Windows 파일 잠금/간헐 실패 대비: 짧게 재시도
        last_err = None
        for _ in range(3):
            try:
                shutil.copy2(str(DB_PATH), str(snap_path))
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(0.15)

        if last_err is not None:
            logging.error(f"[Sync] DB 스냅샷 생성 실패: {last_err}")
            return False

        try:
            gfile.SetContentFile(str(snap_path))
            gfile.Upload()
        finally:
            try:
                snap_path.unlink(missing_ok=True)
            except Exception:
                pass

        # ★ 업로드 성공 후: 방금 업로드된 클라우드 modifiedDate를 마커로 저장
        remote_ts = _parse_gdrive_modified_date(gfile.get('modifiedDate', ''))
        if remote_ts > 0:
            _save_last_sync_ts(remote_ts)

        logging.info(f"[Sync] 업로드 완료: {GDRIVE_DB_FILENAME}")
        return True

    except Exception as e:
        logging.error(f"[Sync] 업로드 실패: {e}")
        return False


# timeclock/sync_manager.py 맨 아래에 추가

# timeclock/sync_manager.py 파일의 맨 끝에 아래 내용을 붙여넣으세요.

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