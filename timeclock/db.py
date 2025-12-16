# timeclock/db.py
# -*- coding: utf-8 -*-
import logging
import sqlite3
import shutil
import json
from pathlib import Path
import datetime
import csv

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
        self._ensure_defaults()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _migrate(self):
        cur = self.conn.cursor()

        # 1. users 테이블
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                pw_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'worker', 
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_pw INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # 2. work_logs (출퇴근 통합 테이블) - 기존 requests 대체
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS work_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,       -- 근무 일자 (YYYY-MM-DD)

                start_time TEXT,               -- 출근 시각
                end_time TEXT,                 -- 퇴근 시각

                status TEXT DEFAULT 'WORKING', -- WORKING, PENDING, APPROVED

                approved_start TEXT,           -- 확정 출근
                approved_end TEXT,             -- 확정 퇴근
                owner_comment TEXT,            -- 비고/코멘트

                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_work_logs_user_date ON work_logs(user_id, work_date)")

        # 3. disputes (이의 제기) - work_log_id 기준
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_log_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL, 
                dispute_type TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'PENDING',
                resolved_at TEXT,
                resolved_by INTEGER,
                resolution_comment TEXT,

                FOREIGN KEY (work_log_id) REFERENCES work_logs(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (resolved_by) REFERENCES users(id)
            )
            """
        )

        # 4. dispute_messages (대화 내역)
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

        # 5. signup_requests (가입 신청)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS signup_requests (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
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

        # 6. audit_logs (감사 로그)
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
    # User / Auth
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

    def list_workers(self):
        return self.conn.execute("SELECT id, username FROM users WHERE role='worker' ORDER BY username ASC").fetchall()

    # ----------------------------------------------------------------
    # Work Logs (출퇴근 로직)
    # ----------------------------------------------------------------
    def get_today_work_log(self, user_id):
        """오늘 날짜의 근무 기록 조회 (스마트 버튼용)"""
        today = datetime.date.today().strftime("%Y-%m-%d")
        # 오늘 날짜이면서, 아직 퇴근 안했거나(WORKING) 퇴근해서 대기중인(PENDING) 기록을 찾음
        return self.conn.execute(
            "SELECT * FROM work_logs WHERE user_id=? AND work_date=? ORDER BY id DESC LIMIT 1",
            (user_id, today)
        ).fetchone()

    def start_work(self, user_id):
        """출근"""
        today = datetime.date.today().strftime("%Y-%m-%d")
        now = now_str()

        # 오늘 이미 출근했는지 확인 (중복 출근 방지)
        existing = self.get_today_work_log(user_id)
        if existing and existing["status"] == "WORKING":
            raise ValueError("이미 근무 중입니다.")

        # 새 출근 기록 생성
        self.conn.execute(
            """
            INSERT INTO work_logs (user_id, work_date, start_time, status, created_at)
            VALUES (?, ?, ?, 'WORKING', ?)
            """,
            (user_id, today, now, now)
        )
        self.conn.commit()

    def end_work(self, user_id):
        """퇴근"""
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

    def list_work_logs(self, user_id, date_from, date_to, limit=1000):
        """근로자용 조회"""
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

    def list_all_work_logs(self, worker_id, date_from, date_to, limit=2000):
        """사업주용 조회 (수정됨: limit 타입 오류 해결)"""
        date_from, date_to = normalize_date_range(date_from, date_to)
        sql = """
            SELECT w.*, u.username as worker_username
            FROM work_logs w
            JOIN users u ON u.id = w.user_id
            WHERE w.work_date >= ? AND w.work_date <= ?
        """
        params = [date_from, date_to]

        # worker_id가 있을 경우 추가
        if worker_id and isinstance(worker_id, int) and worker_id > 0:
            sql += " AND w.user_id = ?"
            params.append(str(worker_id))  # 안전하게 문자열로 변환

        sql += " ORDER BY w.work_date DESC, w.id DESC LIMIT ?"

        # ★ 수정: limit(숫자)를 str(문자)로 감싸서 리스트에 넣음 -> 타입 에러 해결
        params.append(str(limit))

        return self.conn.execute(sql, tuple(params)).fetchall()

    def get_work_log_detail(self, work_log_id):
        """상세 정보 조회 (이의제기 팝업 등에서 사용)"""
        return self.conn.execute(
            """
            SELECT w.*, u.username as worker_username
            FROM work_logs w
            JOIN users u ON u.id = w.user_id
            WHERE w.id = ?
            """,
            (work_log_id,)
        ).fetchone()

    def approve_work_log(self, work_log_id, owner_id, app_start, app_end, comment):
        """
        [수정됨] 사업주 승인 및 수정
        - 퇴근 시간(app_end)이 없으면 -> 상태를 'WORKING'으로 유지 (단순 출근시간 정정)
        - 퇴근 시간이 있으면 -> 상태를 'APPROVED'로 변경 (최종 승인)
        """
        # 승인(확정) 상태 결정 로직
        if not app_end:
            new_status = 'WORKING' # 퇴근 시간이 없으면 아직 근무 중임
        else:
            new_status = 'APPROVED' # 퇴근 시간까지 있으면 최종 승인

        with self.conn:
            self.conn.execute(
                """
                UPDATE work_logs
                SET approved_start=?, 
                    approved_end=?, 
                    owner_comment=?, 
                    status=?
                WHERE id=?
                """,
                (app_start, app_end, comment, new_status, work_log_id)
            )

    # ----------------------------------------------------------------
    # Disputes (이의 제기)
    # ----------------------------------------------------------------
    def create_dispute(self, work_log_id, user_id, dispute_type, comment):
        comment = (comment or "").strip()
        now = now_str()

        row = self.conn.execute(
            "SELECT * FROM disputes WHERE work_log_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
            (work_log_id, user_id),
        ).fetchone()

        if row:
            dispute_id = int(row["id"])
            # 백업 로직
            old_res = (row["resolution_comment"] or "").strip()
            if old_res:
                exists = self.conn.execute(
                    "SELECT 1 FROM dispute_messages WHERE dispute_id=? AND message=? AND sender_role='owner'",
                    (dispute_id, old_res)
                ).fetchone()
                if not exists:
                    self.add_dispute_message(dispute_id, row["resolved_by"], "owner", old_res, row["status"])

            self.conn.execute("UPDATE disputes SET dispute_type=?, status='PENDING' WHERE id=?",
                              (dispute_type, dispute_id))
            self.add_dispute_message(dispute_id, user_id, "worker", comment, None)
            self.conn.commit()
            return dispute_id

        # 신규
        cur = self.conn.execute(
            "INSERT INTO disputes(work_log_id, user_id, dispute_type, comment, created_at, status) VALUES(?,?,?,?,?,?)",
            (work_log_id, user_id, dispute_type, comment, now, "PENDING")
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

    def resolve_dispute(self, dispute_id, resolved_by_id, status_code, resolution_comment):
        now = now_str()
        resolution_comment = (resolution_comment or "").strip()

        row = self.conn.execute("SELECT resolution_comment, resolved_by, status FROM disputes WHERE id=?",
                                (dispute_id,)).fetchone()
        if row:
            old_c = (row["resolution_comment"] or "").strip()
            if old_c and old_c != resolution_comment:
                exists = self.conn.execute(
                    "SELECT 1 FROM dispute_messages WHERE dispute_id=? AND message=? AND sender_role='owner'",
                    (dispute_id, old_c)).fetchone()
                if not exists:
                    self.add_dispute_message(dispute_id, row["resolved_by"] or resolved_by_id, "owner", old_c,
                                             row["status"])

        self.conn.execute(
            "UPDATE disputes SET status=?, resolved_at=?, resolved_by=?, resolution_comment=? WHERE id=?",
            (status_code, now, resolved_by_id, resolution_comment, dispute_id)
        )
        self.conn.commit()
        self.add_dispute_message(dispute_id, resolved_by_id, "owner", resolution_comment, status_code)

    def add_dispute_message(self, dispute_id, sender_user_id, sender_role, message, status_code=None):
        self.conn.execute(
            "INSERT INTO dispute_messages(dispute_id, sender_user_id, sender_role, message, status_code, created_at) VALUES(?,?,?,?,?,?)",
            (dispute_id, sender_user_id, sender_role, (message or "").strip(), status_code, now_str())
        )
        self.conn.commit()

    def get_dispute_timeline(self, dispute_id):
        # Timeline 로직 (이전과 동일하게 최적화)
        req_row = self.conn.execute("SELECT work_log_id FROM disputes WHERE id=?", (dispute_id,)).fetchone()
        if not req_row: return []
        target_id = req_row["work_log_id"]

        events = []
        seen = set()

        msgs = self.conn.execute(
            """
            SELECT m.*, u.username AS sender_username
            FROM dispute_messages m
            LEFT JOIN users u ON u.id = m.sender_user_id
            WHERE m.dispute_id IN (SELECT id FROM disputes WHERE work_log_id=?)
            ORDER BY m.id ASC
            """, (target_id,)
        ).fetchall()

        for row in msgs:
            txt = (row["message"] or "").strip()
            if not txt: continue
            role = row["sender_role"]
            if (role, txt) in seen: continue
            events.append({
                "who": role,
                "username": row["sender_username"] or ("Owner" if role == "owner" else "Worker"),
                "at": row["created_at"],
                "status_code": row["status_code"],
                "comment": txt,
                "sort_key": row["created_at"]
            })
            seen.add((role, txt))

        legacy = self.conn.execute(
            """
            SELECT d.*, u.username
            FROM disputes d
            JOIN users u ON u.id = d.user_id
            WHERE d.work_log_id=? ORDER BY d.id ASC
            """, (target_id,)
        ).fetchall()

        for row in legacy:
            w_c = (row["comment"] or "").strip()
            if w_c and ('worker', w_c) not in seen:
                events.append({"who": "worker", "username": row["username"], "at": row["created_at"], "comment": w_c,
                               "sort_key": row["created_at"]})
                seen.add(('worker', w_c))
            o_c = (row["resolution_comment"] or "").strip()
            if o_c and ('owner', o_c) not in seen:
                events.append(
                    {"who": "owner", "username": "Owner", "at": row["resolved_at"] or row["created_at"], "comment": o_c,
                     "sort_key": row["resolved_at"] or row["created_at"]})
                seen.add(('owner', o_c))

        events.sort(key=lambda x: x['sort_key'])
        return events

    # ----------------------------------------------------------------
    # Signup
    # ----------------------------------------------------------------
    def create_signup_request(self, username, pw_hash, phone, birth, email=None, account=None, address=None):
        with self.conn:
            self.conn.execute(
                "INSERT INTO signup_requests (username, pw_hash, phone, birthdate, email, account, address, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                (username, pw_hash, phone, birth, email, account, address,
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )

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
                "INSERT INTO users (username, role, pw_hash, created_at, is_active, must_change_pw) VALUES (?, 'worker', ?, ?, 1, 1)",
                (sr["username"], sr["pw_hash"], now_str())
            )
            self.conn.execute(
                "UPDATE signup_requests SET status='APPROVED', decided_at=?, decided_by=?, decision_comment=? WHERE id=?",
                (now_str(), owner_id, comment, request_id))

    def reject_signup_request(self, request_id, owner_id, comment=""):
        self.conn.execute(
            "UPDATE signup_requests SET status='REJECTED', decided_at=?, decided_by=?, decision_comment=? WHERE id=?",
            (now_str(), owner_id, comment, request_id))
        self.conn.commit()

    # ----------------------------------------------------------------
    # Audit / Export / Backup
    # ----------------------------------------------------------------
    def log_audit(self, action, actor_user_id=None, target_type=None, target_id=None, detail=None):
        dj = json.dumps(detail, ensure_ascii=False) if detail else None
        self.conn.execute(
            "INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, detail_json, created_at) VALUES (?,?,?,?,?,?)",
            (actor_user_id, action, target_type, target_id, dj, now_str())
        )
        self.conn.commit()

    def export_records_csv(self, out_path: Path, date_from="", date_to=""):
        # work_logs 기반 내보내기
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