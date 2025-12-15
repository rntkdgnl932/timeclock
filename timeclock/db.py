# timeclock/db.py
# -*- coding: utf-8 -*-
import logging
import sqlite3
import shutil
import json
from pathlib import Path
import datetime

from timeclock.auth import pbkdf2_hash_password, pbkdf2_verify_password
from timeclock.utils import now_str, normalize_date_range, ensure_dirs
from timeclock.settings import (
    DEFAULT_OWNER_USER, DEFAULT_OWNER_PASS,
    DEFAULT_WORKER_USER, DEFAULT_WORKER_PASS,
)


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
        self._ensure_indexes()
        self._ensure_defaults()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def vacuum(self):
        self.conn.execute("VACUUM;")
        self.conn.commit()

    def _migrate(self):
        cur = self.conn.cursor()

        # 기존 DB 파일에 is_active, must_change_pw 컬럼이 없는 경우 추가
        def add_column_if_not_exists(table, column_name, column_def):
            try:
                cur.execute(f"SELECT {column_name} FROM {table} LIMIT 1")
            except sqlite3.OperationalError:
                logging.info(f"Adding missing column {column_name} to {table}...")
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_def}")

        # --- users 테이블 생성/마이그레이션 (STEP 4/5 필수) ---
        # 🚨🚨🚨 수정: PRIMARY 키워드 중복 제거 🚨🚨🚨
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                pw_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'worker', 
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,     -- STEP 4/5 컬럼
                must_change_pw INTEGER NOT NULL DEFAULT 0 -- STEP 4/5 컬럼
            )
            """
        )

        # 🚨 users 테이블 컬럼 추가
        add_column_if_not_exists("users", "is_active", "INTEGER NOT NULL DEFAULT 1")
        add_column_if_not_exists("users", "must_change_pw", "INTEGER NOT NULL DEFAULT 0")

        # --- requests 테이블 (기존 로직 유지) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                req_type TEXT NOT NULL, -- CHECK_IN, CHECK_OUT, BREAK_START, BREAK_END
                requested_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        # --- approvals 테이블 (기존 로직 유지) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY,
                request_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                approved_at TEXT NOT NULL,
                reason_code TEXT,
                comment TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (request_id) REFERENCES requests(id),
                FOREIGN KEY (owner_id) REFERENCES users(id)
            )
            """
        )

        # --- signup_requests 테이블 (기존 로직 유지) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS signup_requests (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL,
                birthdate TEXT NOT NULL,
                pw_hash TEXT NOT NULL,

                email TEXT,     
                account TEXT,   
                address TEXT,   

                created_at TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, APPROVED, REJECTED
                decided_at TEXT,
                decided_by INTEGER,
                decision_comment TEXT,

                FOREIGN KEY (decided_by) REFERENCES users(id)
            )
            """
        )

        # 🚨 signup_requests 기존 DB에 누락된 컬럼 추가 (안정성을 위해)
        add_column_if_not_exists("signup_requests", "email", "TEXT")
        add_column_if_not_exists("signup_requests", "account", "TEXT")
        add_column_if_not_exists("signup_requests", "address", "TEXT")

        # --- disputes 테이블 (기존 로직 유지) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS disputes (
                id INTEGER PRIMARY KEY,
                request_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL, -- dispute creator
                dispute_type TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, RESOLVED, REJECTED
                resolved_at TEXT,
                resolved_by INTEGER,
                resolution_comment TEXT,

                FOREIGN KEY (request_id) REFERENCES requests(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (resolved_by) REFERENCES users(id)
            )
            """
        )

        # --- dispute_messages 테이블(대화 히스토리) ---
        # 🚨🚨🚨 수정: 기존 테이블 삭제 후 재생성하여 스키마 충돌 (thread_id 등) 해결 🚨🚨🚨
        cur.execute("DROP TABLE IF EXISTS dispute_messages")
        cur.execute(
            """
            CREATE TABLE dispute_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dispute_id INTEGER NOT NULL,
                sender_user_id INTEGER,
                sender_role TEXT NOT NULL,         -- 'worker' / 'owner'
                message TEXT,
                status_code TEXT,                  -- 상태변경이면 저장(선택)
                created_at TEXT NOT NULL,
                FOREIGN KEY (dispute_id) REFERENCES disputes(id)
            )
            """
        )
        # 이제 ALTER TABLE은 필요 없음

        cur.execute("CREATE INDEX IF NOT EXISTS idx_dispute_messages_dispute_id ON dispute_messages(dispute_id)")

        # 🚨 audit_logs 테이블 생성 코드 추가
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

    def _ensure_indexes(self):
        # 기존 인덱스
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_user_time ON requests(user_id, requested_at);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_time ON requests(requested_at);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_request ON approvals(request_id);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_time ON approvals(approved_at);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_disputes_request ON disputes(request_id);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_disputes_time ON disputes(created_at);")

        # 신규 인덱스 (signup/audit)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_signup_requests_status ON signup_requests(status);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_signup_requests_created ON signup_requests(created_at);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_signup_requests_username ON signup_requests(username);")

        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs(actor_user_id);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);")

        self.conn.commit()

    def _ensure_defaults(self):
        if not self.get_user_by_username(DEFAULT_OWNER_USER):
            self.create_user(DEFAULT_OWNER_USER, "owner", DEFAULT_OWNER_PASS)
            logging.info("Default owner account created: owner/admin1234")
        if not self.get_user_by_username(DEFAULT_WORKER_USER):
            self.create_user(DEFAULT_WORKER_USER, "worker", DEFAULT_WORKER_PASS)
            logging.info("Default worker account created: worker/worker1234")

    # --- Auth/User ---
    def create_user(self, username: str, role: str, password: str):
        pw_hash = pbkdf2_hash_password(password)
        self.conn.execute(
            "INSERT INTO users(username, role, pw_hash, created_at) VALUES(?,?,?,?)",
            (username, role, pw_hash, now_str()),
        )
        self.conn.commit()

    # db.py: get_user_by_username(self, username: str) 메서드 전체 (수정)

    def get_user_by_username(self, username):
        row = self.conn.execute(
            "SELECT * FROM users WHERE username=?",
            (username,),
        ).fetchone()
        return dict(row) if row else None

    def verify_login(self, username: str, password: str):
        try:
            u = self.get_user_by_username(username)
        except Exception as e:
            # 🚨 오류 발생 시 콘솔에 직접 출력
            print(f"===========================================================")
            print(f"🚨🚨 CRITICAL DB ERROR DURING LOGIN (get_user_by_username) 🚨🚨")
            print(f"Error: {e}")
            print(f"===========================================================")
            logging.exception("CRITICAL DB ERROR DURING LOGIN")
            return None  # 로그인 실패 처리

        if not u:
            print(f"DEBUG: User '{username}' not found in DB.")
            return None  # ID/PW 오류 또는 계정 없음

        # 비밀번호 일치 확인
        if not pbkdf2_verify_password(password, u["pw_hash"]):
            print(f"DEBUG: Password verification failed for user '{username}'.")
            return None  # PW 불일치

        # 🚨 STEP 5: 비활성 계정 체크
        if u["is_active"] == 0:
            print(f"DEBUG: User '{username}' is INACTIVE.")
            return {"status": "INACTIVE"}

            # 로그인 성공
        print(f"DEBUG: Login successful for user '{username}'.")
        return u

    def change_password(self, user_id: int, new_password: str):
        pw_hash = pbkdf2_hash_password(new_password)
        self.conn.execute(
            "UPDATE users SET pw_hash=?, must_change_pw=0 WHERE id=?",
            (pw_hash, user_id)
        )
        self.conn.commit()

    # --- Requests/Approvals ---
    def create_request(self, user_id: int, req_type: str, requested_at: str):
        self.conn.execute(
            "INSERT INTO requests(user_id, req_type, requested_at, created_at, status) VALUES(?,?,?,?,?)",
            (user_id, req_type, requested_at, now_str(), "PENDING"),
        )
        self.conn.commit()

    def list_requests_for_user(self, user_id: int, date_from: str, date_to: str, limit: int = 1000):
        date_from, date_to = normalize_date_range(date_from, date_to)
        return self.conn.execute(
            """
            SELECT r.*,
                   u.username as worker_username,
                   a.approved_at, a.reason_code, a.comment as approval_comment,
                   a.created_at as approval_created_at
            FROM requests r
            JOIN users u ON u.id = r.user_id
            LEFT JOIN approvals a ON a.request_id = r.id
            WHERE r.user_id = ?
              AND date(r.requested_at) >= date(?)
              AND date(r.requested_at) <= date(?)
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (user_id, date_from, date_to, limit),
        ).fetchall()

    def list_pending_requests(self, date_from: str, date_to: str, limit: int = 1000):
        date_from, date_to = normalize_date_range(date_from, date_to)
        return self.conn.execute(
            """
            SELECT r.*,
                   u.username as worker_username
            FROM requests r
            JOIN users u ON u.id = r.user_id
            WHERE r.status = 'PENDING'
              AND date(r.requested_at) >= date(?)
              AND date(r.requested_at) <= date(?)
            ORDER BY r.id ASC
            LIMIT ?
            """,
            (date_from, date_to, limit),
        ).fetchall()

    def list_workers(self):
        """
        사업주 화면에서 근로자 목록을 콤보박스로 보여주기 위한 함수
        """
        return self.conn.execute(
            "SELECT id, username FROM users WHERE role='worker' ORDER BY username ASC"
        ).fetchall()

    def list_requests_for_any_user(self, user_id: int, date_from: str, date_to: str, limit: int = 5000):
        """
        특정 근로자(user_id)의 요청/승인(확정) 기록을 기간으로 조회
        - 승인 테이블은 LEFT JOIN이므로 '미승인'도 함께 조회됨
        """
        date_from, date_to = normalize_date_range(date_from, date_to)

        return self.conn.execute(
            """
            SELECT r.*,
                   u.username as worker_username,
                   a.approved_at,
                   a.reason_code,
                   a.comment as approval_comment,
                   ou.username as owner_username
            FROM requests r
            JOIN users u ON u.id = r.user_id
            LEFT JOIN approvals a ON a.request_id = r.id
            LEFT JOIN users ou ON ou.id = a.owner_id
            WHERE r.user_id = ?
              AND date(r.requested_at) >= date(?)
              AND date(r.requested_at) <= date(?)
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (user_id, date_from, date_to, limit),
        ).fetchall()

    def get_request_with_details(self, request_id: int):
        return self.conn.execute(
            """
            SELECT r.*,
                   u.username as worker_username,
                   a.approved_at, a.reason_code, a.comment as approval_comment,
                   a.created_at as approval_created_at,
                   ou.username as owner_username
            FROM requests r
            JOIN users u ON u.id = r.user_id
            LEFT JOIN approvals a ON a.request_id = r.id
            LEFT JOIN users ou ON ou.id = a.owner_id
            WHERE r.id = ?
            """,
            (request_id,),
        ).fetchone()

    def approve_request(self, request_id: int, owner_id: int, approved_at: str, reason_code: str, comment: str):
        try:
            with self.conn:
                # 1. 이미 승인된 요청인지 확인
                existing = self.conn.execute("SELECT 1 FROM approvals WHERE request_id=?", (request_id,)).fetchone()
                if existing:
                    raise ValueError("이미 승인된 요청입니다.")

                # 2. approvals 테이블에 승인 기록 INSERT
                self.conn.execute(
                    "INSERT INTO approvals(request_id, owner_id, approved_at, reason_code, comment, created_at) VALUES(?,?,?,?,?,?)",
                    (request_id, owner_id, approved_at, reason_code, comment, now_str()),
                )

                # 3. requests 테이블의 상태를 APPROVED로 UPDATE
                self.conn.execute("UPDATE requests SET status='APPROVED' WHERE id=?", (request_id,))

                # 4. 감사 로그 기록 (🚨🚨🚨 임시로 제거하여 핵심 기능 충돌 방지 🚨🚨🚨)
                # self.log_audit(
                #     action="REQUEST_APPROVED",
                #     target_type="requests",
                #     target_id=request_id,
                #     actor_user_id=owner_id,
                #     detail={"approved_at": approved_at, "reason_code": reason_code},
                # )

            # with self.conn 블록이 끝날 때 자동으로 commit 됩니다.

        except ValueError:
            raise  # 이미 승인된 경우
        except Exception as e:
            logging.error(f"DB 오류: approve_request 처리 중 실패, req_id={request_id}: {e}")
            raise Exception(f"요청 승인 중 치명적인 DB 오류가 발생했습니다: {e}")

    # --- Disputes ---

    def create_dispute(self, request_id: int, user_id: int, dispute_type: str, comment: str):
        """
        [최종 확정] 같은 request_id + user_id에 대해 가장 최근의 이의를 찾아 누적하고,
        메시지 중복을 피하기 위해 disputes.comment 사용을 최소화합니다.
        """
        comment = (comment or "").strip()
        now = now_str()
        # messages 테이블에 저장할 메시지 내용
        message_content = f"[이의 유형: {dispute_type}]\n{comment}"

        # 1) '처리 상태와 무관하게' 같은 요청ID에 대한 가장 최근의 이의를 찾습니다.
        row = self.conn.execute(
            """
            SELECT id, comment
            FROM disputes
            WHERE request_id=? AND user_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (request_id, user_id),
        ).fetchone()

        if row:
            dispute_id = int(row["id"])
            prev = row["comment"] or ""

            # 1-1) disputes.comment에 누적 (UI 출력용이 아닌, 기존 로직과의 호환성 유지용)
            new_entry_comment = (
                f"\n\n{'=' * 30} [추가 제기: {now}] {'=' * 30}\n"
                f"{message_content}"
            )
            merged_comment = prev + new_entry_comment if prev else new_entry_comment

            # 1-2) disputes Row의 상태 업데이트 (PENDING으로 초기화)
            self.conn.execute(
                """
                UPDATE disputes SET 
                    comment=?,             -- ✅ comment 필드에 누적
                    dispute_type=?,  
                    status='PENDING',      -- 상태를 PENDING으로 강제 초기화
                    resolved_at=NULL,
                    resolution_comment=NULL
                WHERE id=?
                """,
                (merged_comment, dispute_type, dispute_id),
            )
            self.conn.commit()

            # ✅ 수정: message_content를 dispute_messages에 한 번만 추가 (중복 해결)
            self.add_dispute_message(
                dispute_id,
                sender_user_id=user_id,
                sender_role="worker",
                message=message_content,
                status_code=None,
            )
            return dispute_id

        # 2) 없으면 새로 생성 (최초 제기)
        cur = self.conn.execute(
            """
            INSERT INTO disputes(request_id, user_id, dispute_type, comment, created_at, status)
            VALUES(?,?,?,?,?,?)
            """,
            (request_id, user_id, dispute_type, comment, now, "PENDING"),
        )
        dispute_id = cur.lastrowid
        self.conn.commit()

        # ✅ 수정: disputes_messages에 최초 이의 제기 사실도 기록합니다. (중복 해결)
        self.add_dispute_message(
            dispute_id,
            sender_user_id=user_id,
            sender_role="worker",
            message=message_content,
            status_code=None,
        )

        return dispute_id

    # 🚨 수정: request_id 별 최신 이의만 조회하도록 쿼리 변경
    def list_disputes(self, date_from: str, date_to: str, limit: int = 1000):
        """
        (사업주용) 기간 내에 등록된 이의 중, request_id별 최신 이의만 반환합니다.
        """
        date_from, date_to = normalize_date_range(date_from, date_to)

        # request_id별로 가장 큰 id(즉, 가장 최근에 생성된 이의)를 찾기 위한 서브쿼리
        # SQLite는 쿼리 변수를 순서대로 바인딩하므로, 쿼리 내 ? 순서와 튜플의 순서를 일치시켜야 함.
        return self.conn.execute(
            """
            SELECT d.*,
                   u.username as worker_username,
                   r.req_type, r.requested_at, r.status,
                   a.approved_at, a.reason_code, a.comment as approval_comment
            FROM disputes d
            JOIN users u ON u.id = d.user_id
            JOIN requests r ON r.id = d.request_id
            LEFT JOIN approvals a ON a.request_id = r.id
            JOIN (
                SELECT request_id, MAX(id) as max_dispute_id
                FROM disputes
                WHERE date(created_at) >= date(?)  -- 1. date_from
                  AND date(created_at) <= date(?)  -- 2. date_to
                GROUP BY request_id
            ) AS latest_d ON d.id = latest_d.max_dispute_id
            ORDER BY d.id DESC
            LIMIT ?  -- 3. limit
            """,
            (date_from, date_to, limit),  # 바인딩 매개변수 3개로 수정
        ).fetchall()

    # 🚨 수정: user_id와 request_id 별 최신 이의만 조회하도록 쿼리 변경
    def list_my_disputes(self, user_id: int, date_from: str, date_to: str, limit: int = 2000):
        """
        (근로자용) 특정 근로자(user_id)가 제기한 이의 중, request_id별 최신 이의만 반환합니다.
        """
        date_from, date_to = normalize_date_range(date_from, date_to)

        # SQLite는 쿼리 변수를 순서대로 바인딩하므로, 쿼리 내 ? 순서와 튜플의 순서를 일치시켜야 함.
        return self.conn.execute(
            """
            SELECT d.id,
                   d.request_id,
                   r.req_type,
                   r.requested_at,
                   r.status,
                   a.approved_at,
                   d.dispute_type,
                   d.comment,
                   d.created_at,
                   d.status AS dispute_status,
                   d.resolution_comment,
                   d.resolved_at
            FROM disputes d
            JOIN requests r ON r.id = d.request_id
            LEFT JOIN approvals a ON a.request_id = r.id
            JOIN (
                SELECT request_id, MAX(id) as max_dispute_id
                FROM disputes
                WHERE user_id = ?  -- 1. user_id
                  AND date(created_at) >= date(?)  -- 2. date_from
                  AND date(created_at) <= date(?)  -- 3. date_to
                GROUP BY request_id
            ) AS latest_d ON d.id = latest_d.max_dispute_id
            WHERE d.user_id = ?  -- 4. user_id
            ORDER BY d.id DESC
            LIMIT ?  -- 5. limit
            """,
            # 바인딩 매개변수 5개로 수정: 서브쿼리용 (user_id, date_from, date_to) + 메인쿼리용 (user_id, limit)
            (user_id, date_from, date_to, user_id, limit),
        ).fetchall()

    # ==========================================================
    # STEP 3: Signup Requests
    # ==========================================================
    def create_signup_request(
            self,
            username,
            pw_hash,
            phone,
            birth,  # 인자는 birth
            email=None,
            account=None,  # 이 인자를 DB 컬럼 'account'에 매핑
            address=None
    ):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO signup_requests
                (username, pw_hash, phone, birthdate, email, account, address, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                # 🚨 bank_account 컬럼 이름을 account 컬럼 이름으로 수정
                (username, pw_hash, phone, birth, email, account, address, now)
            )

    def is_username_available(self, username):
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM users WHERE username=?
            UNION
            SELECT 1 FROM signup_requests 
            WHERE username=? AND status='PENDING'
            """,
            (username, username)
        )
        return cur.fetchone() is None

    def list_pending_signup_requests(self, limit: int = 1000):
        return self.conn.execute(
            """
            SELECT *
            FROM signup_requests
            WHERE status='PENDING'
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def approve_signup_request(self, request_id: int, owner_id: int, comment: str):
        """
        가입신청 승인: users 테이블에 새 계정을 생성하고, signup_requests 상태를 APPROVED로 업데이트합니다.
        (STEP 4: is_active=1, must_change_pw=1로 설정)
        """
        sr = self.conn.execute(
            "SELECT * FROM signup_requests WHERE id=?",
            (request_id,),
        ).fetchone()

        if not sr:
            raise ValueError("가입신청 내역을 찾을 수 없습니다.")
        if sr["status"] != "PENDING":
            raise ValueError("이미 처리된 가입신청입니다.")

        # users에 같은 username이 있는지 확인 (중복 방지)
        if self.get_user_by_username(sr["username"]):
            raise ValueError("이미 동일 ID가 users에 존재합니다. (중복)")

        try:
            with self.conn:
                # 1. users 테이블에 계정 생성 (role='worker', is_active=1, must_change_pw=1)
                # 🚨 수정: is_active=1, must_change_pw=1 플래그 추가
                self.conn.execute(
                    """
                    INSERT INTO users (username, role, pw_hash, created_at, is_active, must_change_pw) 
                    VALUES (?, ?, ?, ?, 1, 1)
                    """,
                    (sr["username"], "worker", sr["pw_hash"], now_str())
                )
                new_user_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0])

                # 2. signup_requests 상태 업데이트 (사업주가 입력한 comment 사용)
                self.conn.execute(
                    """
                    UPDATE signup_requests
                    SET status='APPROVED',
                        decided_at=?,
                        decided_by=?,
                        decision_comment=? 
                    WHERE id=?
                    """,
                    (now_str(), owner_id, comment, request_id),
                )

                # 3. 감사로그 기록 (최신 log_audit 시그니처에 맞춤)
                # 🚨 수정: 불필요한 actor_username, actor_role 제거
                self.log_audit(
                    action="SIGNUP_APPROVED",
                    target_type="signup_requests",
                    target_id=request_id,
                    actor_user_id=owner_id,
                    detail={
                        "created_user_id": new_user_id,
                        "created_username": sr["username"],
                        "comment": comment,
                    },
                )

            return new_user_id

        except Exception as e:
            self.conn.rollback()
            logging.error(f"가입 승인 처리 실패: {e}")
            raise Exception(f"가입 승인 처리 실패: {e}")

    def reject_signup_request(self, request_id: int, owner_id: int, comment: str = "") -> None:
        """
        가입신청 거절:
        - signup_requests 상태 업데이트(REJECTED)
        - audit_logs 기록
        """
        sr = self.conn.execute(
            "SELECT * FROM signup_requests WHERE id=?",
            (request_id,),
        ).fetchone()
        if not sr:
            raise ValueError("가입신청 내역을 찾을 수 없습니다.")
        if sr["status"] != "PENDING":
            raise ValueError("이미 처리된 가입신청입니다.")

        self.conn.execute(
            """
            UPDATE signup_requests
            SET status='REJECTED',
                decided_at=?,
                decided_by=?,
                decision_comment=?
            WHERE id=?
            """,
            (now_str(), owner_id, comment or "REJECTED", request_id),
        )

        # 감사로그 (최신 log_audit 시그니처에 맞춤 - 불필요한 DB 조회 제거)
        self.log_audit(
            action="REJECT_SIGNUP",
            target_type="signup_requests",
            target_id=request_id,
            actor_user_id=owner_id,
            detail={
                "username": sr["username"],
                "reason": comment or "",
            },
        )

        self.conn.commit()

    # ==========================================================
    # STEP 3: Audit Logs
    # ==========================================================
    def log_audit(
            self,
            action: str,
            *,
            actor_user_id: int = None,
            # actor_username: str = None,  <-- 제거됨
            # actor_role: str = None,      <-- 제거됨
            target_type: str = None,
            target_id: int = None,
            detail: dict = None,
    ) -> None:
        dj = None
        if detail is not None:
            try:
                # 💡 detail 딕셔너리를 JSON 문자열로 저장
                dj = json.dumps(detail, ensure_ascii=False)
            except Exception:
                dj = str(detail)

        self.conn.execute(
            """
            INSERT INTO audit_logs
                (actor_user_id, action, target_type, target_id, detail_json, created_at)
            VALUES
                (?,?,?,?,?,?)
            """,
            # 💡 INSERT 쿼리에서 제거된 컬럼에 해당하는 인자도 제거해야 합니다.
            (actor_user_id, action, target_type, target_id, dj, now_str()),
        )
        self.conn.commit()

    def list_dispute_audit_updates(self, dispute_id: int, limit: int = 2000):
        """
        audit_logs 에 저장된 사업주 처리 이력(action='DISPUTE_UPDATE')을 시간순으로 반환.
        owner_page/worker_page 타임라인 팝업에서 사용.
        """
        return self.conn.execute(
            """
            SELECT a.id,
                   a.actor_user_id,
                   u.username AS actor_username,
                   a.detail_json,
                   a.created_at
            FROM audit_logs a
            LEFT JOIN users u ON u.id = a.actor_user_id
            WHERE a.action = 'DISPUTE_UPDATE'
              AND a.target_type = 'dispute'
              AND a.target_id = ?
            ORDER BY a.id ASC
            LIMIT ?
            """,
            (dispute_id, limit),
        ).fetchall()

    # --- Export/Backup ---
    def export_records_csv(self, out_path: Path, date_from: str = "", date_to: str = ""):
        where = "WHERE r.status='APPROVED'"
        params = []
        if date_from:
            where += " AND date(r.requested_at) >= date(?)"
            params.append(date_from)
        if date_to:
            where += " AND date(r.requested_at) <= date(?)"
            params.append(date_to)

        rows = self.conn.execute(
            f"""
            SELECT r.id as request_id,
                   u.username as worker,
                   r.req_type,
                   r.requested_at,
                   r.created_at as request_created_at,
                   a.approved_at,
                   a.reason_code,
                   a.comment as approval_comment,
                   a.created_at as approval_created_at,
                   ou.username as owner
            FROM requests r
            JOIN users u ON u.id = r.user_id
            JOIN approvals a ON a.request_id = r.id
            JOIN users ou ON ou.id = a.owner_id
            {where}
            ORDER BY r.id ASC
            """,
            tuple(params),
        ).fetchall()

        import csv
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "request_id", "worker", "req_type", "requested_at", "request_created_at",
                "approved_at", "reason_code", "approval_comment", "approval_created_at", "owner"
            ])
            for r in rows:
                w.writerow([r[c] for c in r.keys()])

    def backup_db_copy(self, out_path: Path):
        self.conn.commit()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.db_path, out_path)

        wal = self.db_path.with_suffix(self.db_path.suffix + "-wal")
        shm = self.db_path.with_suffix(self.db_path.suffix + "-shm")
        if wal.exists():
            shutil.copy2(wal, out_path.with_suffix(out_path.suffix + "-wal"))
        if shm.exists():
            shutil.copy2(shm, out_path.with_suffix(out_path.suffix + "-shm"))

    def archive_approved_before_copyonly(self, cutoff_date: str, archive_path: Path) -> int:
        """
        안전을 위해 '복사만' 수행(운영 DB 삭제 없음).
        cutoff_date: YYYY-MM-DD (<=)
        """
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        aconn = sqlite3.connect(str(archive_path))
        aconn.row_factory = sqlite3.Row
        aconn.execute("PRAGMA foreign_keys = OFF;")
        aconn.execute("PRAGMA journal_mode = WAL;")

        # 스키마 생성(동일 + 신규 테이블도 포함)
        for ddl in [
            """CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, role TEXT NOT NULL, pw_hash TEXT NOT NULL, created_at TEXT NOT NULL);""",
            """CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, req_type TEXT NOT NULL, requested_at TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL);""",
            """CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, approved_at TEXT NOT NULL, reason_code TEXT NOT NULL, comment TEXT, created_at TEXT NOT NULL);""",
            """CREATE TABLE IF NOT EXISTS disputes (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, user_id INTEGER NOT NULL, dispute_type TEXT NOT NULL, comment TEXT, created_at TEXT NOT NULL);""",
            """CREATE TABLE IF NOT EXISTS signup_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, pw_hash TEXT NOT NULL, phone TEXT NOT NULL, birthdate TEXT NOT NULL, email TEXT, bank_account TEXT, address TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT, decided_by INTEGER, decision_comment TEXT);""",
            """CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER, actor_username TEXT, actor_role TEXT, action TEXT NOT NULL, target_type TEXT, target_id INTEGER, detail_json TEXT, created_at TEXT NOT NULL);""",
        ]:
            aconn.execute(ddl)
        aconn.commit()

        rows = self.conn.execute(
            """
            SELECT r.id as request_id, r.user_id, r.req_type, r.requested_at, r.created_at, r.status,
                   a.owner_id, a.approved_at, a.reason_code, a.comment, a.created_at as approval_created_at
            FROM requests r
            JOIN approvals a ON a.request_id = r.id
            WHERE date(r.requested_at) <= date(?)
            ORDER BY r.id ASC
            """,
            (cutoff_date,),
        ).fetchall()

        if not rows:
            aconn.close()
            return 0

        users = self.conn.execute("SELECT * FROM users").fetchall()
        for u in users:
            exists = aconn.execute("SELECT 1 FROM users WHERE username=?", (u["username"],)).fetchone()
            if not exists:
                aconn.execute(
                    "INSERT INTO users(username, role, pw_hash, created_at) VALUES(?,?,?,?)",
                    (u["username"], u["role"], u["pw_hash"], u["created_at"]),
                )
        aconn.commit()

        a_users = {r["username"]: r["id"] for r in aconn.execute("SELECT id, username FROM users").fetchall()}
        o_users = {r["id"]: r["username"] for r in users}

        copied = 0
        for r in rows:
            worker_un = o_users.get(r["user_id"])
            owner_un = o_users.get(r["owner_id"])
            if not worker_un or not owner_un:
                continue
            a_worker = a_users.get(worker_un)
            a_owner = a_users.get(owner_un)
            if not a_worker or not a_owner:
                continue

            aconn.execute(
                "INSERT INTO requests(user_id, req_type, requested_at, created_at, status) VALUES(?,?,?,?,?)",
                (a_worker, r["req_type"], r["requested_at"], r["created_at"], r["status"]),
            )
            new_req_id = aconn.execute("SELECT last_insert_rowid()").fetchone()[0]

            aconn.execute(
                "INSERT INTO approvals(request_id, owner_id, approved_at, reason_code, comment, created_at) VALUES(?,?,?,?,?,?)",
                (new_req_id, a_owner, r["approved_at"], r["reason_code"], r["comment"], r["approval_created_at"]),
            )

            drows = self.conn.execute("SELECT * FROM disputes WHERE request_id=? ORDER BY id ASC",
                                      (r["request_id"],)).fetchall()
            for d in drows:
                w_un = o_users.get(d["user_id"])
                if not w_un:
                    continue
                a_wid = a_users.get(w_un)
                if not a_wid:
                    continue
                aconn.execute(
                    "INSERT INTO disputes(request_id, user_id, dispute_type, comment, created_at) VALUES(?,?,?,?,?)",
                    (new_req_id, a_wid, d["dispute_type"], d["comment"], d["created_at"]),
                )

            copied += 1

        aconn.commit()
        aconn.close()
        return copied

    def check_username_available(self, username: str):
        if self.get_user_by_username(username):
            return False, "이미 승인된 계정입니다."

        dup = self.conn.execute(
            "SELECT 1 FROM signup_requests WHERE username=? AND status IN ('PENDING','APPROVED')",
            (username,),
        ).fetchone()
        if dup:
            return False, "이미 가입신청이 진행 중인 ID입니다."

        return True, ""

    def resolve_dispute(self, dispute_id: int, resolved_by_id: int, status_code: str, resolution_comment: str):
        """
        상태 변경 + 처리 코멘트 저장(최신값) + dispute_messages에 사업주 메시지로 누적.
        """
        now = now_str()
        resolution_comment = (resolution_comment or "").strip()

        cur = self.conn.execute(
            """
            UPDATE disputes
            SET status=?,
                resolved_at=?,
                resolved_by=?,
                resolution_comment=? -- 최신 처리 코멘트로 덮어씀 (목록 화면에 보임)
            WHERE id=?
            """,
            (status_code, now, resolved_by_id, resolution_comment, dispute_id),
        )

        if cur.rowcount == 0:
            self.conn.rollback()
            raise ValueError("해당 이의ID를 찾을 수 없습니다.")

        self.conn.commit()

        # ✅ 히스토리 누적: resolution_comment는 disputes 테이블에 덮어쓰지만,
        # dispute_messages에는 아래 함수를 통해 누적됩니다.
        self.add_dispute_message(
            dispute_id,
            sender_user_id=resolved_by_id,
            sender_role="owner",
            message=resolution_comment,
            status_code=status_code,
        )


    def get_dispute_timeline(self, dispute_id: int):
        """
        [최종 복구/수정] disputes(최초 원문/누적)와 dispute_messages(사업주 메시지)를 합쳐서 시간 순서대로 반환합니다.
        - 중복되는 최초 Worker 메시지를 건너뜁니다.
        """

        # 1) disputes에서 근로자 원문/등록시각 (최초 이벤트이자 누적된 히스토리)
        base = self.conn.execute(
            """
            SELECT d.id,
                   d.user_id,
                   u.username AS worker_username,
                   d.comment AS worker_comment,
                   d.created_at AS worker_created_at
            FROM disputes d
            JOIN users u ON u.id = d.user_id
            WHERE d.id = ?
            """,
            (dispute_id,),
        ).fetchone()

        if not base:
            return []

        events = []

        # A. 근로자 최초 이의 (disputes.comment 전체)를 첫 번째 이벤트로 추가 (기존 히스토리 보존)
        events.append({
            "who": "worker",
            "username": base["worker_username"],
            "at": base["worker_created_at"],
            "status_code": None,
            "status_label": None,
            "comment": (base["worker_comment"] or "").strip(),
            "sort_key": base["worker_created_at"]
        })

        # 2) dispute_messages에서 모든 메시지/처리 이력 가져오기
        messages = self.conn.execute(
            """
            SELECT m.created_at,
                   m.sender_role,
                   m.message,
                   m.status_code,
                   u.username AS sender_username
            FROM dispute_messages m
            LEFT JOIN users u ON u.id = m.sender_user_id
            WHERE m.dispute_id = ?
            ORDER BY m.id ASC
            """,
            (dispute_id,),
        ).fetchall()

        # ✅ 수정: 첫 번째 메시지가 근로자의 최초 이의 제기인 경우 건너뛰어 중복을 방지합니다.
        # (첫 번째 메시지는 항상 worker의 최초 이의 제기입니다.)
        for i, row in enumerate(messages):
            # 첫 번째 메시지이고 sender_role이 worker인 경우 (최초 이의제기 중복) 건너뜁니다.
            if i == 0 and row["sender_role"] == "worker":
                continue

            events.append({
                "who": row["sender_role"],
                "username": row["sender_username"] or "System",
                "at": row["created_at"],
                "status_code": row["status_code"],
                "status_label": None,
                "comment": (row["message"] or "").strip(),
                "sort_key": row["created_at"]
            })

        # 시간 순서대로 정렬 (SQLite 날짜 포맷은 텍스트 정렬이 시간 정렬과 동일)
        events.sort(key=lambda x: x['sort_key'])

        return events


    def add_dispute_message(
            self,
            dispute_id: int,
            *,
            sender_user_id: int = None,
            sender_role: str,
            message: str = "",
            status_code: str = None,
    ):
        self.conn.execute(
            """
            INSERT INTO dispute_messages(dispute_id, sender_user_id, sender_role, message, status_code, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (dispute_id, sender_user_id, sender_role, (message or "").strip(), status_code, now_str()),
        )
        self.conn.commit()

    def list_dispute_messages(self, dispute_id: int, limit: int = 2000):
        return self.conn.execute(
            """
            SELECT id, dispute_id, sender_user_id, sender_role, message, status_code, created_at
            FROM dispute_messages
            WHERE dispute_id=?
            ORDER BY id ASC
            LIMIT ?
            """,
            (dispute_id, limit),
        ).fetchall()

    def get_open_dispute_id(self, request_id: int, user_id: int):
        row = self.conn.execute(
            """
            SELECT id
            FROM disputes
            WHERE request_id=? AND user_id=?
              AND status IN ('PENDING', 'IN_REVIEW')
            ORDER BY id DESC
            LIMIT 1
            """,
            (request_id, user_id),
        ).fetchone()
        return int(row["id"]) if row else None

    def list_disputes_open(self, limit: int = 2000):
        # 기간 무관: 미처리/검토
        return self.conn.execute(
            """
            SELECT d.id,
                   u.username AS worker_username,
                   d.request_id,
                   r.req_type,
                   r.requested_at,
                   a.approved_at,
                   d.dispute_type,
                   d.comment,
                   d.created_at,
                   d.status,
                   d.resolution_comment,
                   d.resolved_at
            FROM disputes d
            JOIN users u ON u.id = d.user_id
            JOIN requests r ON r.id = d.request_id
            LEFT JOIN approvals a ON a.request_id = r.id
            WHERE d.status IN ('PENDING','IN_REVIEW')
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def list_disputes_closed(self, date_from: str, date_to: str, limit: int = 2000):
        # 기간 지정: 기각/처리완료
        date_from, date_to = normalize_date_range(date_from, date_to)
        return self.conn.execute(
            """
            SELECT d.id,
                   u.username AS worker_username,
                   d.request_id,
                   r.req_type,
                   r.requested_at,
                   a.approved_at,
                   d.dispute_type,
                   d.comment,
                   d.created_at,
                   d.status,
                   d.resolution_comment,
                   d.resolved_at
            FROM disputes d
            JOIN users u ON u.id = d.user_id
            JOIN requests r ON r.id = d.request_id
            LEFT JOIN approvals a ON a.request_id = r.id
            WHERE d.status IN ('RESOLVED','REJECTED')
              AND date(d.resolved_at) >= date(?)
              AND date(d.resolved_at) <= date(?)
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (date_from, date_to, limit),
        ).fetchall()