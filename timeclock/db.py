# timeclock/db.py
# -*- coding: utf-8 -*-
import sqlite3
import shutil
import json
from pathlib import Path
import datetime
import csv
import threading

from timeclock import backup_manager
from timeclock import sync_manager
from timeclock.auth import pbkdf2_hash_password, pbkdf2_verify_password
from timeclock.utils import now_str, normalize_date_range, ensure_dirs
from timeclock.settings import (
    DEFAULT_OWNER_USER, DEFAULT_OWNER_PASS,
    DEFAULT_WORKER_USER, DEFAULT_WORKER_PASS,
)

# [추가] 백그라운드 스레드 실행 함수 (파일 맨 끝에 붙여넣기)
def run_sync_background(tag):
    """
    DB 변경 직후 백그라운드로:
    1) 로컬 백업
    2) 구글드라이브 업로드

    단, 클라우드 DB가 마지막 동기화 이후 변경되었으면(충돌 위험)
    upload_current_db()가 False를 반환하며 업로드가 차단됩니다.
    """
    def _worker():
        try:
            backup_manager.run_backup(tag)
            ok = sync_manager.upload_current_db()
            if ok:
                print(f"[Thread] '{tag}' 동기화 완료")
            else:
                print(f"[Thread] '{tag}' 업로드 차단(클라우드 변경 감지). 재시작 후 최신 DB 다운로드 필요.")
        except Exception as e:
            print(f"[Thread] 오류: {e}")

    t = threading.Thread(target=_worker)
    t.daemon = True
    t.start()


class DB:
    def __init__(self, db_path: Path):
        ensure_dirs()
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.commit()

        self._migrate()
        self._ensure_defaults()

    # timeclock/db.py 의 DB 클래스 내부에 추가하세요.

    def _save_and_sync(self, tag):
        """
        [핵심 수정] 모든 DB 작업 후 공통으로 호출되는 함수
        스레드 없이 '저장(Close) -> 업로드 -> 재연결'을 강제로 수행합니다.
        """
        print(f"🔄 [AutoSync] '{tag}' 동기화 시작...")

        # 1. DB 연결 해제 (임시 데이터를 파일에 꽉 눌러 담기)
        self.close_connection()

        try:
            # 2. 백업 및 구글 드라이브 업로드
            # (이제 파일이 완벽한 상태이므로 업로드하면 100% 반영됨)
            if 'backup_manager' in globals():
                backup_manager.run_backup(tag)

            ok = sync_manager.upload_current_db()
            if ok:
                print(f"✅ [AutoSync] '{tag}' 업로드 완료")
            else:
                print(f"⚠️ [AutoSync] '{tag}' 업로드 실패/건너뜀")

        except Exception as e:
            print(f"❌ [AutoSync] 오류 발생: {e}")

        finally:
            # 3. DB 재연결 (화면이 멈추지 않고 계속 작동하도록)
            self.reconnect()


    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _migrate(self):
        cur = self.conn.cursor()

        # 1. users 테이블 생성
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                name TEXT,
                pw_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'worker',
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_pw INTEGER NOT NULL DEFAULT 0,
                hourly_wage INTEGER DEFAULT 9860,
                job_title TEXT NOT NULL DEFAULT '사원'
            )
            """
        )

        # 1-1. users 테이블 컬럼 확장
        try:
            cur.execute("ALTER TABLE users ADD COLUMN name TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN must_change_pw INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN hourly_wage INTEGER DEFAULT 9860")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN birthdate TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN job_title TEXT NOT NULL DEFAULT '사원'")
        except Exception:
            pass

        # 개인정보 확장 컬럼
        try:
            cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN account TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN address TEXT")
        except Exception:
            pass

        # 기존 owner 계정 직급 보정
        try:
            cur.execute(
                """
                UPDATE users
                SET job_title='대표'
                WHERE username='owner' AND (job_title IS NULL OR TRIM(job_title)='')
                """
            )
        except Exception:
            pass

        # 2. work_logs 테이블 생성
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS work_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                break_minutes INTEGER DEFAULT 0,
                memo TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                approved_at TEXT,
                approved_by INTEGER,
                reject_reason TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(approved_by) REFERENCES users(id)
            )
            """
        )

        # 🔴 [FIX] work_logs 테이블 누락 컬럼 추가 (이 부분이 없어서 KeyError 발생함)
        try:
            cur.execute("ALTER TABLE work_logs ADD COLUMN approved_start TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE work_logs ADD COLUMN approved_end TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE work_logs ADD COLUMN owner_comment TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE work_logs ADD COLUMN approver_id INTEGER")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE work_logs ADD COLUMN updated_at TEXT")
        except Exception:
            pass


        # 3. disputes 테이블 생성 (work_log_id 포함)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_log_id INTEGER,
                user_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                dispute_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'IN_REVIEW',
                created_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by INTEGER,
                decision_comment TEXT,
                comment TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(decided_by) REFERENCES users(id)
            )
            """
        )

        # disputes 테이블에 work_log_id가 없으면 강제로 추가
        try:
            cur.execute("ALTER TABLE disputes ADD COLUMN work_log_id INTEGER")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE disputes ADD COLUMN comment TEXT")
        except Exception:
            pass

        # 4. dispute_messages 테이블 생성
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dispute_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dispute_id INTEGER NOT NULL,
                sender_user_id INTEGER,
                sender_role TEXT NOT NULL,
                message TEXT,
                status_code TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (dispute_id) REFERENCES disputes(id)
            )
            """
        )

        # 5. signup_requests 테이블 생성
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS signup_requests (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                name TEXT,
                phone TEXT NOT NULL,
                birthdate TEXT NOT NULL,
                pw_hash TEXT NOT NULL,
                email TEXT, account TEXT, address TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                decided_at TEXT, decided_by INTEGER, decision_comment TEXT,
                FOREIGN KEY (decided_by) REFERENCES users(id)
            )
            """
        )
        # signup_requests 확장 컬럼
        try:
            cur.execute("ALTER TABLE signup_requests ADD COLUMN name TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE signup_requests ADD COLUMN email TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE signup_requests ADD COLUMN account TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE signup_requests ADD COLUMN address TEXT")
        except Exception:
            pass

        # 6. audit_logs 테이블 생성
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY,
                actor_user_id INTEGER, 
                action TEXT NOT NULL,
                target_type TEXT,
                target_id INTEGER,
                detail_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(actor_user_id) REFERENCES users(id)
            );
            """
        )

        self.conn.commit()

    def _ensure_defaults(self):
        if not self.get_user_by_username(DEFAULT_OWNER_USER):
            self.create_user(DEFAULT_OWNER_USER, "owner", DEFAULT_OWNER_PASS)
        if not self.get_user_by_username(DEFAULT_WORKER_USER):
            self.create_user(DEFAULT_WORKER_USER, "worker", DEFAULT_WORKER_PASS)

    # ----------------------------------------------------------------
    # User / Auth / Member Management
    # ----------------------------------------------------------------
    def create_user(self, username, role, password):
        pw_hash = pbkdf2_hash_password(password)
        self.conn.execute(
            "INSERT INTO users(username, role, pw_hash, created_at) VALUES(?,?,?,?)",
            (username, role, pw_hash, now_str()),
        )
        self.conn.commit()

    def get_user_by_username(self, username):
        row = self.conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def verify_login(self, username, password):
        u = self.get_user_by_username(username)
        if not u: return None
        if not pbkdf2_verify_password(password, u["pw_hash"]): return None
        if u["is_active"] == 0: return {"status": "INACTIVE"}
        return u

    def change_password(self, user_id, new_password):
        pw_hash = pbkdf2_hash_password(new_password)
        self.conn.execute("UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?", (pw_hash, user_id))
        self.conn.commit()
        self._save_and_sync("change_password")

    def verify_user_password(self, user_id: int, password: str) -> bool:
        row = self.conn.execute("SELECT pw_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return False
        return pbkdf2_verify_password(password or "", row["pw_hash"])

    def get_user_profile(self, user_id: int) -> dict | None:
        # users에 컬럼이 항상 존재한다는 보장이 없으므로 PRAGMA로 안전 조회
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(users)").fetchall()}

        want = ["id", "username", "name", "phone", "birthdate", "email", "account", "address"]
        use = [c for c in want if c in cols]
        if not use:
            return None

        sql = "SELECT " + ", ".join(use) + " FROM users WHERE id=?"
        row = self.conn.execute(sql, (user_id,)).fetchone()
        return dict(row) if row else None

    def update_user_profile(
            self,
            user_id: int,
            *,
            name=None,
            phone=None,
            birthdate=None,
            email=None,
            account=None,
            address=None,
    ) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(users)").fetchall()}

        updates = []
        params = []

        def add(col, val):
            if col in cols:
                updates.append(f"{col}=?")
                params.append(val)

        # 아이디(username)는 절대 업데이트하지 않음
        add("name", name)
        add("phone", phone)
        add("birthdate", birthdate)
        add("email", email)
        add("account", account)
        add("address", address)

        if not updates:
            return

        params.append(int(user_id))
        sql = "UPDATE users SET " + ", ".join(updates) + " WHERE id=?"
        self.conn.execute(sql, tuple(params))
        self.conn.commit()
        self._save_and_sync("admin_update_profile")

    def list_workers(self, keyword=None, status_filter="ACTIVE"):
        sql = "SELECT id, username, name, phone, birthdate, job_title, hourly_wage, created_at, is_active FROM users WHERE role='worker'"

        params = []

        if status_filter == "ACTIVE":
            sql += " AND is_active = 1"
        elif status_filter == "INACTIVE":
            sql += " AND is_active = 0"

        if keyword:
            sql += " AND (username LIKE ? OR name LIKE ?)"
            params.append(f"%{keyword}%")
            params.append(f"%{keyword}%")

        sql += " ORDER BY username ASC"
        return self.conn.execute(sql, tuple(params)).fetchall()

    def resign_user(self, user_id):
        self.conn.execute(
            "UPDATE users SET is_active=0 WHERE id=?",
            (user_id,)
        )
        self.conn.commit()
        self._save_and_sync("admin_resign_user")

    def update_user_wage(self, user_id, new_wage):
        self.conn.execute(
            "UPDATE users SET hourly_wage=? WHERE id=?",
            (new_wage, user_id)
        )
        self.conn.commit()
        self._save_and_sync("admin_update_wage")

    def update_user_job_title(self, user_id: int, job_title: str):
        self.conn.execute(
            "UPDATE users SET job_title=? WHERE id=?",
            (job_title, user_id)
        )
        self.conn.commit()
        self._save_and_sync("admin_update_job")

    # ----------------------------------------------------------------
    # Work Logs (출퇴근 로직)
    # ----------------------------------------------------------------
    def get_today_work_log(self, user_id):
        today = datetime.date.today().strftime("%Y-%m-%d")
        return self.conn.execute(
            "SELECT * FROM work_logs WHERE user_id=? AND work_date=? ORDER BY id DESC LIMIT 1",
            (user_id, today)
        ).fetchone()

    def start_work(self, user_id):
        today = datetime.date.today().strftime("%Y-%m-%d")
        now = now_str()

        # 오늘 날짜의 '유효한(Active)' 근무 기록이 있는지 확인 (반려된 건은 제외)
        sql_check = """
            SELECT 1 FROM work_logs 
            WHERE user_id = ? AND work_date = ? AND status IN ('PENDING', 'WORKING', 'APPROVED')
        """
        row = self.conn.execute(sql_check, (user_id, today)).fetchone()

        if row:
            raise ValueError("이미 처리 중이거나 완료된 근무 기록이 있습니다.")

        self.conn.execute(
            """
            INSERT INTO work_logs (user_id, work_date, start_time, status, created_at)
            VALUES (?, ?, ?, 'PENDING', ?)
            """,
            (user_id, today, now, now)
        )
        self.conn.commit()
        # self._save_and_sync("start_work")

    def end_work(self, user_id):
        row = self.conn.execute(
            "SELECT * FROM work_logs WHERE user_id=? AND status='WORKING' ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()

        if not row:
            raise ValueError("현재 근무 중인 기록이 없습니다.")

        now = now_str()
        self.conn.execute(
            "UPDATE work_logs SET end_time=?, status='PENDING' WHERE id=?",
            (now, row["id"])
        )
        self.conn.commit()
        # self._save_and_sync("end_work")

    def reject_work_log(self, log_id):
        """
        작업 기록을 삭제하지 않고 'REJECTED' 상태로 변경하여 기록을 남김.
        """
        sql = "UPDATE work_logs SET status = 'REJECTED' WHERE id = ?"
        self.conn.execute(sql, (log_id,))
        self.conn.commit()
        self._save_and_sync("reject_work_log")

    def list_work_logs(self, user_id, date_from, date_to, limit=1000):
        date_from, date_to = normalize_date_range(date_from, date_to)
        return self.conn.execute(
            """
            SELECT * FROM work_logs
            WHERE user_id=? AND work_date >= ? AND work_date <= ?
            ORDER BY work_date DESC, id DESC
            LIMIT ?
            """,
            (user_id, date_from, date_to, limit)
        ).fetchall()

    def list_all_work_logs(self, worker_id, date_from, date_to, limit=2000, status_filter=None):
        date_from, date_to = normalize_date_range(date_from, date_to)

        sql = """
            SELECT w.*, u.username as worker_username, u.name as worker_name
            FROM work_logs w
            JOIN users u ON u.id = w.user_id
            WHERE w.work_date >= ? AND w.work_date <= ?
        """
        params = [date_from, date_to]

        if worker_id and isinstance(worker_id, int) and worker_id > 0:
            sql += " AND w.user_id = ?"
            params.append(str(worker_id))

        if status_filter and status_filter != "ALL":
            sql += " AND w.status = ?"
            params.append(status_filter)

        sql += " ORDER BY w.work_date DESC, w.id DESC LIMIT ?"
        params.append(str(limit))

        return self.conn.execute(sql, tuple(params)).fetchall()

    def approve_work_log(self, work_log_id, owner_id, app_start, app_end, comment):
        with self.conn:
            # 1. 상태 결정 로직
            if app_end:
                new_status = 'APPROVED'
            else:
                new_status = 'WORKING'

            # 2. 업데이트 수행
            self.conn.execute(
                """
                UPDATE work_logs
                SET approved_start=?, approved_end=?, owner_comment=?, status=?, 
                    approver_id=?, updated_at=?
                WHERE id=?
                """,
                (app_start, app_end, comment, new_status, owner_id, now_str(), work_log_id)
            )
            self._save_and_sync("approve")

    # ----------------------------------------------------------------
    # Disputes (이의 제기)
    # ----------------------------------------------------------------
    def create_dispute(self, work_log_id, user_id, dispute_type, comment):
        comment = (comment or "").strip()
        now = now_str()

        # disputes 컬럼 호환(구버전/신버전 혼재 대응)
        d_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(disputes)").fetchall()}

        # 신버전(현 스키마) 컬럼명
        decided_at_col = "decided_at" if "decided_at" in d_cols else None
        decided_by_col = "decided_by" if "decided_by" in d_cols else None
        decision_comment_col = "decision_comment" if "decision_comment" in d_cols else None

        # 구버전 컬럼명(혹시 남아있을 경우)
        resolved_at_col = "resolved_at" if "resolved_at" in d_cols else None
        resolved_by_col = "resolved_by" if "resolved_by" in d_cols else None
        resolution_comment_col = "resolution_comment" if "resolution_comment" in d_cols else None

        row = self.conn.execute(
            "SELECT * FROM disputes WHERE work_log_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
            (work_log_id, user_id),
        ).fetchone()

        if row:
            dispute_id = int(row["id"])

            # (1) 과거 “결정/처리 코멘트”가 disputes 테이블에만 남아있던 경우,
            #     dispute_messages 로 한 번만 옮겨심기
            old_owner_comment = ""

            if decision_comment_col:
                old_owner_comment = (
                    (row[decision_comment_col] or "") if decision_comment_col in row.keys() else "").strip()
            if not old_owner_comment and resolution_comment_col:
                old_owner_comment = (
                    (row[resolution_comment_col] or "") if resolution_comment_col in row.keys() else "").strip()

            old_owner_id = None
            if decided_by_col:
                old_owner_id = row[decided_by_col] if decided_by_col in row.keys() else None
            if old_owner_id is None and resolved_by_col:
                old_owner_id = row[resolved_by_col] if resolved_by_col in row.keys() else None

            old_status = row["status"] if "status" in row.keys() else None
            old_owner_at = None
            if decided_at_col:
                old_owner_at = row[decided_at_col] if decided_at_col in row.keys() else None
            if old_owner_at is None and resolved_at_col:
                old_owner_at = row[resolved_at_col] if resolved_at_col in row.keys() else None

            if old_owner_comment:
                exists = self.conn.execute(
                    "SELECT 1 FROM dispute_messages WHERE dispute_id=? AND message=? AND sender_role='owner'",
                    (dispute_id, old_owner_comment),
                ).fetchone()
                if not exists:
                    # add_dispute_message가 created_at을 now_str()로 박으므로,
                    # 과거 시각(old_owner_at)을 완벽히 재현하진 않지만 최소한 기록은 남긴다.
                    # (원하면 add_dispute_message에 created_at 인자를 추가하는 확장도 가능)
                    self.add_dispute_message(dispute_id, old_owner_id, "owner", old_owner_comment, old_status)

            # (2) 기존 dispute를 “다시 진행중”으로 되돌림 + 최신 worker 첫 메시지 반영
            #     - 컬럼명은 신스키마(decided_*, decision_comment)를 기준으로 NULL 처리
            sets = ["dispute_type=?", "status='PENDING'"]
            params = [dispute_type]

            # disputes.comment는 “요약/대표 문구”로 UI에서 쓰이므로 최신 메시지로 교체
            if "comment" in d_cols:
                sets.append("comment=?")
                params.append(comment)

            # 결정 관련 컬럼(신스키마)이 있으면 초기화
            if decided_at_col:
                sets.append(f"{decided_at_col}=NULL")
            if decided_by_col:
                sets.append(f"{decided_by_col}=NULL")
            if decision_comment_col:
                sets.append(f"{decision_comment_col}=NULL")

            # 구버전 컬럼이 살아있다면 그것도 같이 초기화(혼재 DB 방어)
            if resolved_at_col:
                sets.append(f"{resolved_at_col}=NULL")
            if resolved_by_col:
                sets.append(f"{resolved_by_col}=NULL")
            if resolution_comment_col:
                sets.append(f"{resolution_comment_col}=NULL")

            sql = "UPDATE disputes SET " + ", ".join(sets) + " WHERE id=?"
            params.append(dispute_id)
            self.conn.execute(sql, tuple(params))

            # (3) worker 메시지를 타임라인에 추가(중복 방지)
            if comment:
                exists_w = self.conn.execute(
                    "SELECT 1 FROM dispute_messages WHERE dispute_id=? AND message=? AND sender_role='worker'",
                    (dispute_id, comment),
                ).fetchone()
                if not exists_w:
                    self.add_dispute_message(dispute_id, user_id, "worker", comment, None)

            self.conn.commit()
            return dispute_id

        # 신규 생성 경로
        wl = self.conn.execute("SELECT work_date FROM work_logs WHERE id=?", (work_log_id,)).fetchone()
        w_date = wl["work_date"] if wl else now.split(" ")[0]

        cur = self.conn.execute(
            "INSERT INTO disputes(work_log_id, user_id, work_date, dispute_type, comment, created_at, status) VALUES(?,?,?,?,?,?,?)",
            (work_log_id, user_id, w_date, dispute_type, comment, now, "PENDING"),
        )
        dispute_id = cur.lastrowid
        self.add_dispute_message(dispute_id, user_id, "worker", comment, None)
        self.conn.commit()

        return dispute_id

    def list_my_disputes(self, user_id, date_from, date_to, filter_type="ACTIVE", limit=2000):
        date_from, date_to = normalize_date_range(date_from, date_to)
        status_cond = "d.status IN ('RESOLVED','REJECTED')" if filter_type == "CLOSED" else "d.status IN ('PENDING','IN_REVIEW')"

        return self.conn.execute(
            f"""
            SELECT d.*, w.work_date, w.status as work_status
            FROM disputes d
            JOIN work_logs w ON w.id = d.work_log_id
            JOIN (
                SELECT work_log_id, MAX(id) as max_id FROM disputes WHERE user_id=? GROUP BY work_log_id
            ) AS latest ON d.id = latest.max_id
            WHERE d.user_id=? AND date(d.created_at) >= date(?) AND date(d.created_at) <= date(?) AND {status_cond}
            ORDER BY d.id DESC LIMIT ?
            """,
            (user_id, user_id, date_from, date_to, limit)
        ).fetchall()

    def list_disputes(self, date_from, date_to, filter_type="ACTIVE", limit=1000):
        date_from, date_to = normalize_date_range(date_from, date_to)
        status_cond = "d.status IN ('RESOLVED','REJECTED')" if filter_type == "CLOSED" else "d.status IN ('PENDING','IN_REVIEW')"

        return self.conn.execute(
            f"""
            SELECT d.*, u.username as worker_username, w.work_date, w.start_time, w.end_time
            FROM disputes d
            JOIN users u ON u.id = d.user_id
            JOIN work_logs w ON w.id = d.work_log_id
            JOIN (
                SELECT work_log_id, MAX(id) as max_id FROM disputes 
                WHERE date(created_at) >= date(?) AND date(created_at) <= date(?) GROUP BY work_log_id
            ) AS latest ON d.id = latest.max_id
            WHERE {status_cond}
            ORDER BY d.id DESC LIMIT ?
            """,
            (date_from, date_to, limit)
        ).fetchall()

    def resolve_dispute(self, dispute_id, owner_id, new_status, resolution_comment):
        now = now_str()
        resolution_comment = (resolution_comment or "").strip()

        # disputes 컬럼 호환(구버전/신버전 혼재 대응)
        d_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(disputes)").fetchall()}

        # 신스키마
        decided_at_col = "decided_at" if "decided_at" in d_cols else None
        decided_by_col = "decided_by" if "decided_by" in d_cols else None
        decision_comment_col = "decision_comment" if "decision_comment" in d_cols else None

        # 구스키마(혹시 존재하면 같이 업데이트)
        resolved_at_col = "resolved_at" if "resolved_at" in d_cols else None
        resolved_by_col = "resolved_by" if "resolved_by" in d_cols else None
        resolution_comment_col = "resolution_comment" if "resolution_comment" in d_cols else None

        sets = ["status=?"]
        params = [new_status]

        if decided_at_col:
            sets.append(f"{decided_at_col}=?")
            params.append(now)
        if decided_by_col:
            sets.append(f"{decided_by_col}=?")
            params.append(owner_id)
        if decision_comment_col:
            sets.append(f"{decision_comment_col}=?")
            params.append(resolution_comment)

        # 혼재 DB 방어: 구컬럼이 있으면 동일 값 반영
        if resolved_at_col:
            sets.append(f"{resolved_at_col}=?")
            params.append(now)
        if resolved_by_col:
            sets.append(f"{resolved_by_col}=?")
            params.append(owner_id)
        if resolution_comment_col:
            sets.append(f"{resolution_comment_col}=?")
            params.append(resolution_comment)

        sql = "UPDATE disputes SET " + ", ".join(sets) + " WHERE id=?"
        params.append(dispute_id)

        self.conn.execute(sql, tuple(params))

        # 메시지가 있다면 타임라인에도 남김(상태코드 포함)
        if resolution_comment:
            self.add_dispute_message(dispute_id, owner_id, "owner", resolution_comment, new_status)

        # UI 쪽이 업로드를 담당하므로 여기서는 commit만
        self.conn.commit()

    def add_dispute_message(self, dispute_id, sender_user_id, sender_role, message, status_code=None):
        self.conn.execute(
            "INSERT INTO dispute_messages(dispute_id, sender_user_id, sender_role, message, status_code, created_at) VALUES(?,?,?,?,?,?)",
            (dispute_id, sender_user_id, sender_role, (message or "").strip(), status_code, now_str())
        )
        self.conn.commit()

    def get_dispute_timeline(self, dispute_id):
        req_row = self.conn.execute("SELECT work_log_id FROM disputes WHERE id=?", (dispute_id,)).fetchone()
        if not req_row:
            return []
        target_id = req_row["work_log_id"]

        # disputes 컬럼 호환(구버전/신버전 혼재 대응)
        d_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(disputes)").fetchall()}
        decided_at_col = "decided_at" if "decided_at" in d_cols else None
        decision_comment_col = "decision_comment" if "decision_comment" in d_cols else None
        resolved_at_col = "resolved_at" if "resolved_at" in d_cols else None
        resolution_comment_col = "resolution_comment" if "resolution_comment" in d_cols else None

        events = []
        seen = set()

        # 1) dispute_messages 기반 이벤트(정식 타임라인)
        msgs = self.conn.execute(
            """
            SELECT m.*, u.username AS sender_username
            FROM dispute_messages m
            LEFT JOIN users u ON u.id = m.sender_user_id
            WHERE m.dispute_id IN (SELECT id FROM disputes WHERE work_log_id=?)
            ORDER BY m.id ASC
            """,
            (target_id,),
        ).fetchall()

        for row in msgs:
            txt = (row["message"] or "").strip()
            if not txt:
                continue
            role = row["sender_role"]
            if (role, txt) in seen:
                continue
            events.append(
                {
                    "who": role,
                    "username": row["sender_username"] or ("Owner" if role == "owner" else "Worker"),
                    "at": row["created_at"],
                    "status_code": row["status_code"],
                    "comment": txt,
                    "sort_key": row["created_at"],
                }
            )
            seen.add((role, txt))

        # 2) disputes 테이블에만 남아있던 레거시 코멘트(혹시 누락된 경우 보완)
        legacy = self.conn.execute(
            """
            SELECT d.*, u.username
            FROM disputes d
            JOIN users u ON u.id = d.user_id
            WHERE d.work_log_id=? ORDER BY d.id ASC
            """,
            (target_id,),
        ).fetchall()

        for row in legacy:
            # worker 대표 코멘트(comment)
            w_c = (row["comment"] or "").strip()
            if w_c and ("worker", w_c) not in seen:
                events.append(
                    {
                        "who": "worker",
                        "username": row["username"],
                        "at": row["created_at"],
                        "comment": w_c,
                        "sort_key": row["created_at"],
                    }
                )
                seen.add(("worker", w_c))

            # owner 결정 코멘트(신/구 컬럼 모두 방어)
            o_c = ""
            if decision_comment_col and decision_comment_col in row.keys():
                o_c = (row[decision_comment_col] or "").strip()
            if not o_c and resolution_comment_col and resolution_comment_col in row.keys():
                o_c = (row[resolution_comment_col] or "").strip()

            o_at = None
            if decided_at_col and decided_at_col in row.keys():
                o_at = row[decided_at_col]
            if not o_at and resolved_at_col and resolved_at_col in row.keys():
                o_at = row[resolved_at_col]

            if o_c and ("owner", o_c) not in seen:
                at_val = o_at or row["created_at"]
                events.append(
                    {
                        "who": "owner",
                        "username": "Owner",
                        "at": at_val,
                        "comment": o_c,
                        "sort_key": at_val,
                    }
                )
                seen.add(("owner", o_c))

        events.sort(key=lambda x: x.get("sort_key") or "")
        return events

    # ----------------------------------------------------------------
    # Signup / Audit / Export
    # ----------------------------------------------------------------
    def create_signup_request(self, username, pw_hash, name, phone, birth, email=None, account=None, address=None):
        with self.conn:
            self.conn.execute(
                "INSERT INTO signup_requests (username, pw_hash, name, phone, birthdate, email, account, address, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                (username, pw_hash, name, phone, birth, email, account, address,
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
        self._save_and_sync("signup_request")

    def is_username_available(self, username):
        u = self.conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if u: return False
        s = self.conn.execute("SELECT 1 FROM signup_requests WHERE username=? AND status='PENDING'",
                              (username,)).fetchone()
        if s: return False
        return True

    def list_pending_signup_requests(self, limit=1000):
        return self.conn.execute("SELECT * FROM signup_requests WHERE status='PENDING' ORDER BY id ASC LIMIT ?",
                                 (limit,)).fetchall()

    def approve_signup_request(self, request_id, owner_id, comment):
        sr = self.conn.execute("SELECT * FROM signup_requests WHERE id=?", (request_id,)).fetchone()
        if not sr or sr["status"] != "PENDING": raise ValueError("처리할 수 없는 요청입니다.")

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO users (username, role, pw_hash, name, phone, birthdate, created_at, is_active, must_change_pw, hourly_wage) 
                VALUES (?, 'worker', ?, ?, ?, ?, ?, 1, 1, 9860)
                """,
                (sr["username"], sr["pw_hash"], sr["name"], sr["phone"], sr["birthdate"], now_str())
            )
            self.conn.execute(
                "UPDATE signup_requests SET status='APPROVED', decided_at=?, decided_by=?, decision_comment=? WHERE id=?",
                (now_str(), owner_id, comment, request_id))
        self._save_and_sync("signup_approve")

    def reject_signup_request(self, request_id, owner_id, comment=""):
        self.conn.execute(
            "UPDATE signup_requests SET status='REJECTED', decided_at=?, decided_by=?, decision_comment=? WHERE id=?",
            (now_str(), owner_id, comment, request_id))
        self.conn.commit()
        self._save_and_sync("reject_signup")

    def log_audit(self, action, actor_user_id=None, target_type=None, target_id=None, detail=None):
        dj = json.dumps(detail, ensure_ascii=False) if detail else None
        self.conn.execute(
            "INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, detail_json, created_at) VALUES (?,?,?,?,?,?)",
            (actor_user_id, action, target_type, target_id, dj, now_str())
        )
        self.conn.commit()

    def export_records_csv(self, out_path: Path, date_from="", date_to=""):
        sql = """
            SELECT w.work_date, u.username, w.start_time, w.end_time, 
                   w.status, w.approved_start, w.approved_end, w.owner_comment
            FROM work_logs w
            JOIN users u ON u.id = w.user_id
            WHERE w.status='APPROVED'
        """
        params = []
        if date_from:
            sql += " AND w.work_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND w.work_date <= ?"
            params.append(date_to)

        rows = self.conn.execute(sql, tuple(params)).fetchall()

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["일자", "근로자", "출근", "퇴근", "상태", "확정출근", "확정퇴근", "비고"])
            for r in rows:
                w.writerow([r[c] for c in
                            ["work_date", "username", "start_time", "end_time", "status", "approved_start",
                             "approved_end", "owner_comment"]])

    def backup_db_copy(self, out_path: Path):
        self.conn.commit()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.db_path, out_path)

    # ----------------------------------------------------------------
    # [신규] 대기 중인 항목 개수 조회 (배지 알림용)
    # ----------------------------------------------------------------
    def get_pending_counts(self):
        """
        근무승인대기, 이의제기진행중, 가입승인대기 건수를 딕셔너리로 반환
        """
        # 1. 근무 승인 대기 (PENDING 상태)
        cnt_work = self.conn.execute(
            "SELECT COUNT(*) FROM work_logs WHERE status='PENDING'"
        ).fetchone()[0]

        # 2. 이의제기 진행 중 (PENDING 또는 IN_REVIEW)
        cnt_dispute = self.conn.execute(
            "SELECT COUNT(*) FROM disputes WHERE status IN ('PENDING', 'IN_REVIEW')"
        ).fetchone()[0]

        # 3. 가입 승인 대기 (PENDING)
        cnt_signup = self.conn.execute(
            "SELECT COUNT(*) FROM signup_requests WHERE status='PENDING'"
        ).fetchone()[0]

        return {"work": cnt_work, "dispute": cnt_dispute, "signup": cnt_signup}



    def get_user_by_id(self, user_id: int):
        row = self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def close_connection(self):
        """DB 연결 해제 (파일 덮어쓰기 전 필수)"""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    def reconnect(self):
        """DB 다시 연결 (파일 덮어쓴 후 필수)"""
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row