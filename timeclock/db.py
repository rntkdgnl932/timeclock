# timeclock/db.py
# -*- coding: utf-8 -*-
import logging
import sqlite3
import shutil
from pathlib import Path

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
        self._ensure_dispute_resolution_columns()
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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('worker','owner')),
                pw_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                req_type TEXT NOT NULL CHECK(req_type IN ('IN','OUT')),
                requested_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED')) DEFAULT 'PENDING',
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                approved_at TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES requests(id),
                FOREIGN KEY(owner_id) REFERENCES users(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                dispute_type TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES requests(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS owner_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dispute_id INTEGER NOT NULL,
                owner_id INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(dispute_id) REFERENCES disputes(id),
                FOREIGN KEY(owner_id) REFERENCES users(id)
            );
            """
        )
        self.conn.commit()

    def _ensure_indexes(self):
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_user_time ON requests(user_id, requested_at);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_time ON requests(requested_at);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_request ON approvals(request_id);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_time ON approvals(approved_at);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_disputes_request ON disputes(request_id);")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_disputes_time ON disputes(created_at);")
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

    def get_user_by_username(self, username: str):
        return self.conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def verify_login(self, username: str, password: str):
        u = self.get_user_by_username(username)
        if not u:
            return None
        if pbkdf2_verify_password(password, u["pw_hash"]):
            return u
        return None

    def change_password(self, user_id: int, new_password: str):
        pw_hash = pbkdf2_hash_password(new_password)
        self.conn.execute("UPDATE users SET pw_hash=? WHERE id=?", (pw_hash, user_id))
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

    # timeclock/db.py 안, class DB: 내부에 추가

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
        existing = self.conn.execute("SELECT 1 FROM approvals WHERE request_id=?", (request_id,)).fetchone()
        if existing:
            raise ValueError("이미 승인된 요청입니다. (승인 로그는 덮어쓰기하지 않습니다)")
        self.conn.execute(
            "INSERT INTO approvals(request_id, owner_id, approved_at, reason_code, comment, created_at) VALUES(?,?,?,?,?,?)",
            (request_id, owner_id, approved_at, reason_code, comment, now_str()),
        )
        self.conn.execute("UPDATE requests SET status='APPROVED' WHERE id=?", (request_id,))
        self.conn.commit()

    # --- Disputes ---
    def create_dispute(self, request_id: int, user_id: int, dispute_type: str, comment: str):
        self.conn.execute(
            """
            INSERT INTO disputes (request_id, user_id, dispute_type, comment, created_at)
            VALUES (?, ?, ?, ?, datetime('now','localtime'))
            """,
            (request_id, user_id, dispute_type, comment),
        )
        self.conn.commit()

    def list_disputes(self, date_from: str, date_to: str, limit: int = 1000):
        date_from, date_to = normalize_date_range(date_from, date_to)
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
            WHERE date(d.created_at) >= date(?)
              AND date(d.created_at) <= date(?)
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (date_from, date_to, limit),
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
                "request_id","worker","req_type","requested_at","request_created_at",
                "approved_at","reason_code","approval_comment","approval_created_at","owner"
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

        # 스키마 생성(동일)
        for ddl in [
            """CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, role TEXT NOT NULL, pw_hash TEXT NOT NULL, created_at TEXT NOT NULL);""",
            """CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, req_type TEXT NOT NULL, requested_at TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL);""",
            """CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, approved_at TEXT NOT NULL, reason_code TEXT NOT NULL, comment TEXT, created_at TEXT NOT NULL);""",
            """CREATE TABLE IF NOT EXISTS disputes (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, user_id INTEGER NOT NULL, dispute_type TEXT NOT NULL, comment TEXT, created_at TEXT NOT NULL);""",
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

            drows = self.conn.execute("SELECT * FROM disputes WHERE request_id=? ORDER BY id ASC", (r["request_id"],)).fetchall()
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

    def list_my_disputes(self, user_id: int, date_from: str, date_to: str, limit: int = 2000):
        date_from, date_to = normalize_date_range(date_from, date_to)
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
                   d.created_at
            FROM disputes d
            JOIN requests r ON r.id = d.request_id
            LEFT JOIN approvals a ON a.request_id = r.id
            WHERE d.user_id = ?
              AND date(d.created_at) >= date(?)
              AND date(d.created_at) <= date(?)
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (user_id, date_from, date_to, limit),
        ).fetchall()

    def _ensure_dispute_resolution_columns(self):
        cur = self.conn.cursor()
        # sqlite는 컬럼 IF NOT EXISTS가 없어서 try/except로 안전 처리
        try:
            cur.execute("ALTER TABLE disputes ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE disputes ADD COLUMN resolved_at TEXT")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE disputes ADD COLUMN resolved_by INTEGER")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE disputes ADD COLUMN resolution_comment TEXT")
        except Exception:
            pass
        self.conn.commit()

    def resolve_dispute(self, dispute_id: int, owner_id: int, status: str, resolution_comment: str):
        """
        status: 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'REJECTED'
        """
        self.conn.execute(
            """
            UPDATE disputes
               SET status = ?,
                   resolution_comment = ?,
                   resolved_by = ?,
                   resolved_at = datetime('now','localtime')
             WHERE id = ?
            """,
            (status, resolution_comment, owner_id, dispute_id),
        )
        self.conn.commit()



