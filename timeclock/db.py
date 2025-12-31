# timeclock/db.py
# -*- coding: utf-8 -*-
import sqlite3
import shutil
import json
from pathlib import Path
import datetime
import csv
import threading
import logging
import time
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

        # UI/스레드/동기화 상황에서 잠금/NoneType 방지
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row

        try:
            self.conn.execute("PRAGMA foreign_keys = ON;")
            self.conn.execute("PRAGMA journal_mode = WAL;")
            self.conn.commit()
        except Exception:
            pass

        self._migrate()
        self._ensure_defaults()

    def _save_and_sync(self, tag: str):
        """
        [핵심 안정화]
        - UI가 DB를 사용 중인 상태에서 self.conn을 close/reconnect 하지 않는다.
        - 대신 현재 DB 파일을 스냅샷으로 복사한 뒤(짧은 재시도 포함),
          그 스냅샷 파일을 백그라운드 스레드로 업로드한다.
        - 업로드 성공/실패는 로그로 남긴다.
        """
        try:
            print(f"🔄 [AutoSync] '{tag}' 동기화 시작...")

            # 1) 변경사항 커밋
            try:
                self.conn.commit()
            except Exception:
                pass

            # 2) 스냅샷 파일 생성 (DB 연결 유지한 채 파일 복사)
            snap_dir = self.db_path.parent / "_sync_tmp"
            snap_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            snap_path = snap_dir / f"{self.db_path.stem}.snapshot_{ts}{self.db_path.suffix}"

            # WAL 환경이면 checkpoint로 파일 상태를 최대한 안정화
            try:
                self.conn.execute("PRAGMA wal_checkpoint(FULL);")
                self.conn.commit()
            except Exception:
                pass

            last_err = None
            for _ in range(3):
                try:
                    shutil.copy2(str(self.db_path), str(snap_path))
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.15)

            if last_err is not None:
                print(f"❌ [AutoSync] snapshot copy failed: {last_err}")
                return

            # 3) 백그라운드 업로드 (UI 블로킹/크래시 방지)
            def _worker():
                try:
                    # 로컬/드라이브 백업은 기존 정책 유지
                    try:
                        if 'backup_manager' in globals():
                            backup_manager.run_backup(tag)
                    except Exception as e:
                        print(f"⚠️ [AutoSync] backup failed: {e}")

                    # 스냅샷 업로드
                    try:
                        ok = sync_manager.upload_current_db(db_path=snap_path)
                        if ok:
                            print(f"✅ [AutoSync] '{tag}' 업로드 완료")
                        else:
                            print(f"⚠️ [AutoSync] '{tag}' 업로드 실패/차단")
                    except Exception as e:
                        print(f"❌ [AutoSync] upload failed: {e}")

                finally:
                    try:
                        snap_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            t = threading.Thread(target=_worker, daemon=True)
            t.start()

        except Exception as e:
            print(f"❌ [AutoSync] _save_and_sync failed: {e}")

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

                -- 레거시/신규 혼재 방지용(둘 다 살아있을 수 있음)
                decided_at TEXT,
                decided_by INTEGER,
                decision_comment TEXT,

                -- 현재 코드에서 사용하는 resolved_* (없으면 추가 마이그레이션으로 보강)
                resolved_at TEXT,
                resolved_by INTEGER,

                comment TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(decided_by) REFERENCES users(id)
            )
            """
        )

        # ✅ disputes 컬럼 보강(기존 DB 스키마 불일치 방지)
        for sql in [
            "ALTER TABLE disputes ADD COLUMN work_log_id INTEGER",
            "ALTER TABLE disputes ADD COLUMN comment TEXT",

            # 현재 코드가 쓰는 컬럼(없으면 no such column 터짐)
            "ALTER TABLE disputes ADD COLUMN resolved_at TEXT",
            "ALTER TABLE disputes ADD COLUMN resolved_by INTEGER",

            # 레거시 호환(혹시 누락된 DB 대비)
            "ALTER TABLE disputes ADD COLUMN decided_at TEXT",
            "ALTER TABLE disputes ADD COLUMN decided_by INTEGER",
            "ALTER TABLE disputes ADD COLUMN decision_comment TEXT",
        ]:
            try:
                cur.execute(sql)
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

        # disputes 테이블 컬럼 확인 (스키마 불일치 안전 처리)
        dcols = {r[1] for r in self.conn.execute("PRAGMA table_info(disputes)").fetchall()}
        has_decision = ("decision_comment" in dcols)
        has_decided_by = ("decided_by" in dcols)
        has_decided_at = ("decided_at" in dcols)

        row = self.conn.execute(
            "SELECT * FROM disputes WHERE work_log_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
            (work_log_id, user_id),
        ).fetchone()

        if row:
            dispute_id = int(row["id"])

            # 기존 disputes 행에 남아있는 "사업주 답변"이 있다면 dispute_messages로 보강(레거시 데이터 보정)
            old_owner_comment = ""
            old_owner_by = None
            old_owner_at = None

            if has_decision:
                old_owner_comment = (row["decision_comment"] or "").strip()
            else:
                # 과거 DB에서 resolution_comment가 있을 수 있으므로 안전 접근
                try:
                    old_owner_comment = (row["resolution_comment"] or "").strip()
                except Exception:
                    old_owner_comment = ""

            if has_decided_by:
                old_owner_by = row["decided_by"]
            else:
                try:
                    old_owner_by = row["resolved_by"]
                except Exception:
                    old_owner_by = None

            if has_decided_at:
                old_owner_at = row["decided_at"]
            else:
                try:
                    old_owner_at = row["resolved_at"]
                except Exception:
                    old_owner_at = None

            if old_owner_comment:
                exists = self.conn.execute(
                    "SELECT 1 FROM dispute_messages WHERE dispute_id=? AND message=? AND sender_role='owner'",
                    (dispute_id, old_owner_comment)
                ).fetchone()
                if not exists:
                    # status_code는 당시 disputes.status를 넣어둠
                    self.conn.execute(
                        "INSERT INTO dispute_messages(dispute_id, sender_user_id, sender_role, message, status_code, created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (dispute_id, old_owner_by, "owner", old_owner_comment, row["status"], old_owner_at or now)
                    )

            # 기존 이의제기는 "재오픈" 개념으로 상태를 PENDING으로 되돌림
            self.conn.execute(
                "UPDATE disputes SET dispute_type=?, status='PENDING' WHERE id=?",
                (dispute_type, dispute_id)
            )

            # 근로자 메시지 추가
            if comment:
                self.add_dispute_message(dispute_id, user_id, "worker", comment, None)

            self.conn.commit()
            return dispute_id

        # 신규 이의제기 생성
        wl = self.conn.execute("SELECT work_date FROM work_logs WHERE id=?", (work_log_id,)).fetchone()
        w_date = wl["work_date"] if wl else now.split(" ")[0]

        cur = self.conn.execute(
            "INSERT INTO disputes(work_log_id, user_id, work_date, dispute_type, comment, created_at, status) VALUES(?,?,?,?,?,?,?)",
            (work_log_id, user_id, w_date, dispute_type, comment, now, "PENDING")
        )
        dispute_id = cur.lastrowid

        if comment:
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

        # disputes 테이블 컬럼 확인 (스키마 불일치 안전 처리)
        dcols = {r[1] for r in self.conn.execute("PRAGMA table_info(disputes)").fetchall()}
        has_resolved_at = ("resolved_at" in dcols)
        has_resolved_by = ("resolved_by" in dcols)
        has_decided_at = ("decided_at" in dcols)
        has_decided_by = ("decided_by" in dcols)
        has_decision_comment = ("decision_comment" in dcols)

        # 1) 상태 업데이트 (가능한 컬럼만)
        sets = ["status=?"]
        params = [new_status]

        if has_resolved_at:
            sets.append("resolved_at=?")
            params.append(now)
        elif has_decided_at:
            sets.append("decided_at=?")
            params.append(now)

        if has_resolved_by:
            sets.append("resolved_by=?")
            params.append(int(owner_id))
        elif has_decided_by:
            sets.append("decided_by=?")
            params.append(int(owner_id))

        if has_decision_comment and resolution_comment:
            sets.append("decision_comment=?")
            params.append(resolution_comment)

        sql = "UPDATE disputes SET " + ", ".join(sets) + " WHERE id=?"
        params.append(int(dispute_id))
        self.conn.execute(sql, tuple(params))

        # 2) 메시지가 있다면 추가(이 안에서 _save_and_sync 호출됨)
        if resolution_comment:
            self.add_dispute_message(dispute_id, owner_id, "owner", resolution_comment, new_status)
        else:
            self.conn.commit()
            self._save_and_sync("dispute_resolve")

    def add_dispute_message(self, dispute_id, sender_user_id, sender_role, message, status_code=None):
        message = (message or "").strip()
        if not message:
            return

        self.conn.execute(
            "INSERT INTO dispute_messages(dispute_id, sender_user_id, sender_role, message, status_code, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (dispute_id, int(sender_user_id), str(sender_role), message, status_code, now_str())
        )
        self.conn.commit()

        # ✅ 이의제기 메시지는 즉시 서버 업로드 트리거
        self._save_and_sync("dispute_message")

    def get_dispute_timeline(self, dispute_id):
        req_row = self.conn.execute("SELECT work_log_id FROM disputes WHERE id=?", (dispute_id,)).fetchone()
        if not req_row:
            return []
        target_id = req_row["work_log_id"]

        # disputes 테이블 컬럼 확인 (스키마 불일치 안전 처리)
        dcols = {r[1] for r in self.conn.execute("PRAGMA table_info(disputes)").fetchall()}
        has_decision = ("decision_comment" in dcols)
        has_decided_at = ("decided_at" in dcols)

        events = []
        seen = set()

        # 1) dispute_messages(채팅 로그)가 있으면 그게 1순위
        msgs = self.conn.execute(
            """
            SELECT m.*, u.username AS sender_username
            FROM dispute_messages m
            LEFT JOIN users u ON u.id = m.sender_user_id
            WHERE m.dispute_id IN (SELECT id FROM disputes WHERE work_log_id=?)
            ORDER BY m.id ASC
            """,
            (target_id,)
        ).fetchall()

        for row in msgs:
            txt = (row["message"] or "").strip()
            if not txt:
                continue
            role = row["sender_role"]
            if (role, txt) in seen:
                continue

            events.append({
                "who": role,
                "username": row["sender_username"] or ("Owner" if role == "owner" else "Worker"),
                "at": row["created_at"],
                "status_code": row["status_code"],
                "comment": txt,
                "sort_key": row["created_at"]
            })
            seen.add((role, txt))

        # 2) disputes 테이블에만 저장돼 있던 레거시(초기 comment / decision_comment)도 보강
        legacy = self.conn.execute(
            """
            SELECT d.*, u.username
            FROM disputes d
            JOIN users u ON u.id = d.user_id
            WHERE d.work_log_id=? ORDER BY d.id ASC
            """,
            (target_id,)
        ).fetchall()

        for row in legacy:
            # worker 최초 사유(comment)
            w_c = (row["comment"] or "").strip()
            if w_c and ("worker", w_c) not in seen:
                events.append({
                    "who": "worker",
                    "username": row["username"],
                    "at": row["created_at"],
                    "comment": w_c,
                    "sort_key": row["created_at"]
                })
                seen.add(("worker", w_c))

            # owner 답변(decision_comment 우선)
            o_c = ""
            o_at = None

            if has_decision:
                o_c = (row["decision_comment"] or "").strip()
            else:
                # 과거 DB에서 resolution_comment가 있을 수 있으므로 안전 접근
                try:
                    o_c = (row["resolution_comment"] or "").strip()
                except Exception:
                    o_c = ""

            if has_decided_at:
                o_at = row["decided_at"]
            else:
                try:
                    o_at = row["resolved_at"]
                except Exception:
                    o_at = None

            if o_c and ("owner", o_c) not in seen:
                events.append({
                    "who": "owner",
                    "username": "Owner",
                    "at": o_at or row["created_at"],
                    "comment": o_c,
                    "sort_key": o_at or row["created_at"]
                })
                seen.add(("owner", o_c))

        events.sort(key=lambda x: x["sort_key"])
        return events

        # timeclock/db.py 내 sync_dispute_thread_from_cloud 함수 전체입니다.

    def sync_dispute_thread_from_cloud(self, dispute_id: int):
            """
            클라우드 DB를 '스냅샷 다운로드'만 한 뒤,
            dispute_messages / disputes(상태)만 로컬 DB에 merge한다.
            - 로컬 DB 파일 교체 없음 (conn 안정)
            - 채팅 실시간 수신용
            """
            try:
                # 1. 클라우드 스냅샷 다운로드
                temp_db_path, _remote_ts = sync_manager.download_latest_db_snapshot()
                if not temp_db_path:
                    return False

                rconn = sqlite3.connect(str(temp_db_path))
                rconn.row_factory = sqlite3.Row

                # 로컬/원격 disputes 컬럼 목록 확인
                lcols = {r[1] for r in self.conn.execute("PRAGMA table_info(disputes)").fetchall()}
                rcols = {r[1] for r in rconn.execute("PRAGMA table_info(disputes)").fetchall()}

                def pick_time_col(cols):
                    return "resolved_at" if "resolved_at" in cols else ("decided_at" if "decided_at" in cols else None)

                def pick_by_col(cols):
                    return "resolved_by" if "resolved_by" in cols else ("decided_by" if "decided_by" in cols else None)

                r_time = pick_time_col(rcols)
                r_by = pick_by_col(rcols)
                l_time = pick_time_col(lcols)
                l_by = pick_by_col(lcols)

                # 1) disputes 상태 merge
                sel_cols = ["id", "status"]
                if r_time:
                    sel_cols.append(r_time)
                if r_by:
                    sel_cols.append(r_by)
                if "comment" in rcols:
                    sel_cols.append("comment")

                r_dispute = rconn.execute(
                    f"SELECT {', '.join(sel_cols)} FROM disputes WHERE id=?",
                    (int(dispute_id),)
                ).fetchone()

                if r_dispute:
                    sets = ["status=?"]
                    params = [r_dispute["status"]]

                    if l_time and r_time:
                        sets.append(f"{l_time}=?")
                        params.append(r_dispute[r_time])
                    if l_by and r_by:
                        sets.append(f"{l_by}=?")
                        params.append(r_dispute[r_by])

                    if "comment" in lcols and "comment" in rcols:
                        sets.append("comment=?")
                        params.append(r_dispute["comment"])

                    params.append(int(dispute_id))
                    self.conn.execute(
                        "UPDATE disputes SET " + ", ".join(sets) + " WHERE id=?",
                        tuple(params)
                    )

                # 2) 메시지 merge: id(PK) 기준 INSERT OR IGNORE
                rows = rconn.execute(
                    "SELECT id, dispute_id, sender_user_id, sender_role, message, status_code, created_at "
                    "FROM dispute_messages WHERE dispute_id=? ORDER BY id ASC",
                    (int(dispute_id),)
                ).fetchall()

                inserted = 0
                for r in rows:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO dispute_messages(id, dispute_id, sender_user_id, sender_role, message, status_code, created_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (r["id"], r["dispute_id"], r["sender_user_id"], r["sender_role"], r["message"],
                         r["status_code"],
                         r["created_at"])
                    )
                    if cur.rowcount and cur.rowcount > 0:
                        inserted += 1

                self.conn.commit()

                # ✅ [핵심 수정] 병합 성공 시, 클라우드 시각 마커를 갱신하여 내 컴퓨터가 최신임을 선언합니다.
                if _remote_ts and _remote_ts > 0:
                    sync_manager._save_last_sync_ts(_remote_ts)

                try:
                    rconn.close()
                except Exception:
                    pass

                try:
                    temp_db_path.unlink(missing_ok=True)
                except Exception:
                    pass

                return inserted > 0

            except Exception as e:
                logging.error(f"sync_dispute_thread_from_cloud failed: {e}")
                return False

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
        try:
            from timeclock import sync_manager
            sync_manager.apply_pending_db_if_exists()
        except Exception:
            pass

        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        try:
            self.conn.execute("PRAGMA foreign_keys = ON;")
            self.conn.execute("PRAGMA journal_mode = WAL;")
            self.conn.commit()
        except Exception:
            pass

    def ensure_connection(self):
        """
        UI(사업주/근로자)에서 동기화 버튼을 누르면 close_connection()으로 conn이 None이 될 수 있다.
        이때 대화방 등 다른 화면이 같은 DB 인스턴스를 공유하면 NoneType.execute가 터진다.
        모든 DB 작업 직전에 이 함수로 연결을 보장한다.
        """
        if self.conn is None:
            self.reconnect()

