# timeclock/ui/worker_page.py
# -*- coding: utf-8 -*-
import logging
from PyQt5 import QtWidgets, QtCore

from timeclock.settings import REQ_STATUS, REASON_CODES, DISPUTE_STATUS

from timeclock.utils import now_str, Message
from timeclock.settings import REQ_TYPES
from ui.widgets import DateRangeBar, Table
from ui.dialogs import DisputeDialog


class WorkerPage(QtWidgets.QWidget):
    logout_requested = QtCore.pyqtSignal()

    def __init__(self, db, session, parent=None):
        super().__init__(parent)
        self.db = db
        self.session = session

        header = QtWidgets.QLabel(f"근로자 화면 - {session.username}")
        f = header.font()
        f.setPointSize(13)
        f.setBold(True)
        header.setFont(f)

        self.filter = DateRangeBar(label="조회기간")
        self.filter.applied.connect(lambda *_: self.refresh())

        self.btn_in = QtWidgets.QPushButton("출근 요청")
        self.btn_out = QtWidgets.QPushButton("퇴근 요청")
        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_dispute = QtWidgets.QPushButton("선택 항목 이의 제기")
        self.btn_logout = QtWidgets.QPushButton("로그아웃")

        self.btn_in.clicked.connect(lambda: self.create_request("IN"))
        self.btn_out.clicked.connect(lambda: self.create_request("OUT"))
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_dispute.clicked.connect(self.open_dispute)
        self.btn_logout.clicked.connect(self.logout_requested.emit)



        self.table = Table(["요청ID","유형","요청시각","상태","승인시각(확정)","정정사유","코멘트"])

        note = QtWidgets.QLabel(
            "안내: 출·퇴근은 '요청'이며, 실제 근로시간은 사업주 승인 시 확정됩니다.\n"
            "승인/정정 내역은 삭제·덮어쓰기 없이 이력으로 보존되며 언제든 열람할 수 있습니다.\n"
            "과거 날짜 기록도 선택 후 이의 제기가 가능합니다."
        )
        note.setWordWrap(True)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.btn_in)
        top.addWidget(self.btn_out)
        top.addWidget(self.btn_refresh)
        top.addStretch(1)
        top.addWidget(self.btn_dispute)
        top.addWidget(self.btn_logout)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(header)
        layout.addWidget(self.filter)
        layout.addLayout(top)
        layout.addWidget(note)
        layout.addWidget(self.table)

        # ----------------------------
        # 내 이의제기 목록(근로자 열람용)
        # ----------------------------
        self.filter_my_disputes = DateRangeBar(label="내 이의제기 조회기간")
        self.filter_my_disputes.applied.connect(lambda *_: self.refresh_my_disputes())

        self.btn_my_disputes_refresh = QtWidgets.QPushButton("내 이의제기 새로고침")
        self.btn_my_disputes_refresh.clicked.connect(self.refresh_my_disputes)

        self.btn_my_dispute_view = QtWidgets.QPushButton("선택 이의내용 전체보기")
        self.btn_my_dispute_view.clicked.connect(self.open_selected_dispute_comment)

        self.my_dispute_table = Table([
            "이의ID", "요청ID", "유형", "요청시각", "상태",
            "승인시각(확정)", "이의유형", "이의내용", "등록시각",
            "처리상태", "처리코멘트", "처리시각"
        ])

        QtCore.QTimer.singleShot(0, self._wire_my_dispute_doubleclick)


        # 더블클릭으로 이의내용 전체 보기(이의내용 컬럼)


        layout.addWidget(QtWidgets.QLabel("내 이의 제기 목록"))
        layout.addWidget(self.filter_my_disputes)
        layout.addWidget(self.btn_my_disputes_refresh)
        layout.addWidget(self.my_dispute_table)
        layout.addWidget(self.btn_my_dispute_view)

        self.setLayout(layout)

        self.refresh()
        self.refresh_my_disputes()


    def create_request(self, req_type: str):
        ts = now_str()
        try:
            self.db.create_request(self.session.user_id, req_type, ts)
            logging.info(f"Worker request created: user={self.session.username} type={req_type} at={ts}")
            Message.info(self, "요청 완료", f"{dict(REQ_TYPES).get(req_type)} 요청이 등록되었습니다.\n요청시각: {ts}")
            self.refresh()
        except Exception as e:
            logging.exception("create_request failed")
            Message.err(self, "오류", f"요청 등록 중 오류: {e}")

    def refresh(self):
        d1, d2 = self.filter.get_range()
        rows = self.db.list_requests_for_user(self.session.user_id, d1, d2)
        out = []


        for r in rows:

            req_type_label = dict(REQ_TYPES).get(r["req_type"], r["req_type"])
            status_label = REQ_STATUS.get(r["status"], r["status"])
            reason_label = REASON_CODES.get(r["reason_code"], r["reason_code"] or "")

            out.append([
                str(r["id"]),
                req_type_label,
                r["requested_at"],
                status_label,  # ✔ 한글
                r["approved_at"] or "",
                reason_label,  # ✔ 한글
                r["approval_comment"] or "",
            ])
        self.table.set_rows(out)

    def open_dispute(self):
        import traceback
        try:
            row_idx = self.table.selected_first_row_index()
            if row_idx < 0:
                Message.warn(self, "이의 제기", "테이블에서 항목을 선택하세요.")
                return

            cell = self.table.get_cell(row_idx, 0)
            if cell is None or str(cell).strip() == "":
                Message.err(self, "이의 제기", "선택한 행에서 요청ID를 읽을 수 없습니다.")
                return

            request_id = int(str(cell).strip())

            # DB 조회도 예외가 날 수 있으니 전체 try 안에 둠
            detail = self.db.get_request_with_details(request_id)
            if not detail:
                Message.err(self, "이의 제기", "요청 정보를 불러올 수 없습니다.")
                return

            # 여기(다이얼로그 생성/exec)에서 예외 나면 기존 코드는 앱이 꺼질 수 있었음
            dlg = DisputeDialog(self, detail)
            rc = dlg.exec_()
            if rc != QtWidgets.QDialog.Accepted:
                return

            dtype, comment = dlg.get_values()

            dtype = (dtype or "").strip()
            comment = (comment or "").strip()

            if not dtype or not comment:
                Message.warn(self, "이의 제기", "이의 내용을 입력하세요.")
                return

            self.db.create_dispute(request_id, self.session.user_id, dtype, comment)
            logging.info(f"Dispute created: request_id={request_id} user={self.session.username}")
            Message.info(self, "제출 완료", "이의 제기가 등록되었습니다.")

            # 제출 후 화면 갱신(사용자 체감 개선)
            self.refresh()

        except Exception as e:
            logging.exception("open_dispute failed")
            Message.err(
                self,
                "오류",
                "선택 항목 이의 제기 처리 중 오류가 발생했습니다.\n"
                f"{e}\n\n{traceback.format_exc()}"
            )

    def refresh_my_disputes(self):
        d1, d2 = self.filter_my_disputes.get_range()
        try:
            rows = self.db.list_my_disputes(self.session.user_id, d1, d2)

            # 더블클릭 팝업에서 "원문"을 보여주기 위해 보관
            self._my_dispute_rows = rows

            out = []
            for r in rows:
                # sqlite Row / dict 모두 대응
                rr = dict(r)

                req_type_label = dict(REQ_TYPES).get(rr.get("req_type"), rr.get("req_type", ""))
                # ✅ 요청 상태 한글화 (APPROVED/PENDING)
                req_status_label = dict(REQ_STATUS).get(rr.get("status"), rr.get("status", ""))

                # 테이블에는 한 줄로 보이게(줄바꿈 제거), 팝업은 원문 사용
                comment_one_line = (rr.get("comment", "") or "").replace("\n", " ")

                # ✅ 이의 처리 상태/코멘트/시각 (사장 처리 결과)
                # DB 쿼리에 따라 dispute_status 라는 별칭을 쓰거나 status를 그대로 쓸 수 있으니 둘 다 대응
                dispute_status_code = rr.get("dispute_status") or rr.get("d_status") or rr.get(
                    "status_dispute") or rr.get("status")
                dispute_status_label = DISPUTE_STATUS.get(dispute_status_code, dispute_status_code or "")

                resolution_comment_one_line = (rr.get("resolution_comment", "") or "").replace("\n", " ")
                resolved_at = rr.get("resolved_at", "") or ""

                out.append([
                    str(rr.get("id", "")),
                    str(rr.get("request_id", "")),
                    req_type_label,
                    rr.get("requested_at", "") or "",
                    req_status_label,
                    rr.get("approved_at", "") or "",
                    rr.get("dispute_type", "") or "",
                    comment_one_line,
                    rr.get("created_at", "") or "",

                    # ✅ 추가 3컬럼
                    dispute_status_label,
                    resolution_comment_one_line,
                    resolved_at,
                ])

            self.my_dispute_table.set_rows(out)
            QtCore.QTimer.singleShot(0, self._wire_my_dispute_doubleclick)

        except Exception as e:
            logging.exception("refresh_my_disputes failed")
            Message.err(self, "오류", f"내 이의제기 조회 중 오류: {e}")

    def _wire_my_dispute_doubleclick(self):
        """
        my_dispute_table 내부의 '본문 테이블'에만 더블클릭 이벤트를 연결한다.
        헤더(QHeaderView)가 먼저 잡히는 문제를 방지한다.
        """
        logging.info("--- _wire_my_dispute_doubleclick 호출됨 (수정 시도 1) ---")

        # 1) Table 위젯 자체가 QTableWidget/QTableView를 상속받았거나 동일 시그널을 제공한다고 가정하고 연결 시도
        table_obj = self.my_dispute_table

        # QTableWidget의 시그널 연결 시도 (row, col 인자를 받음)
        if hasattr(table_obj, 'cellDoubleClicked'):
            try:
                table_obj.cellDoubleClicked.disconnect(self.on_my_dispute_double_clicked_cell)
            except Exception:
                pass
            table_obj.cellDoubleClicked.connect(self.on_my_dispute_double_clicked_cell)
            logging.info("my_dispute_table: Table 객체에 cellDoubleClicked 연결 완료 (경로 A)")
            return

        # QTableView의 시그널 연결 시도 (QModelIndex 인자를 받음)
        if hasattr(table_obj, 'doubleClicked'):
            try:
                table_obj.doubleClicked.disconnect(self.on_my_dispute_double_clicked_index)  # type: ignore
            except Exception:
                pass
            table_obj.doubleClicked.connect(self.on_my_dispute_double_clicked_index)  # type: ignore
            logging.info("my_dispute_table: Table 객체에 doubleClicked 연결 완료 (경로 B)")
            return

        # 2) 기존 로직: Table 위젯 내부에서 QTableWidget/QTableView를 찾음

        # QTableWidget 찾기 (경로 C - 기존 경로 1)
        tw_list = self.my_dispute_table.findChildren(QtWidgets.QTableWidget)
        if tw_list:
            tw = tw_list[0]
            try:
                tw.cellDoubleClicked.disconnect(self.on_my_dispute_double_clicked_cell)
            except Exception:
                pass
            tw.cellDoubleClicked.connect(self.on_my_dispute_double_clicked_cell)
            logging.info("my_dispute_table: QTableWidget cellDoubleClicked 연결 완료 (경로 C)")
            return

        # QTableView 찾기 (경로 D - 기존 경로 2)
        tv_list = [v for v in self.my_dispute_table.findChildren(QtWidgets.QTableView)
                   if not isinstance(v, QtWidgets.QHeaderView)]
        if tv_list:
            tv = tv_list[0]
            try:
                tv.doubleClicked.disconnect(self.on_my_dispute_double_clicked_index)  # type: ignore
            except Exception:
                pass
            tv.doubleClicked.connect(self.on_my_dispute_double_clicked_index)  # type: ignore
            logging.info("my_dispute_table: QTableView doubleClicked 연결 완료 (경로 D)")
            return

        logging.warning("my_dispute_table: 본문 테이블을 찾지 못했습니다. (경로 E - 기존 경로 3)")

    def on_my_dispute_double_clicked_index(self, index: QtCore.QModelIndex):
        # QTableView (모델 기반) 처리
        if not index.isValid():
            return
        if index.column() != 7: # 이의내용 컬럼(인덱스 7)만 처리
            return
        self._show_my_dispute_comment_popup(index.row()) # 전체보기 팝업 호출

    def _show_my_dispute_comment_popup(self, row: int):
        rows = getattr(self, "_my_dispute_rows", None)
        if not rows or not (0 <= row < len(rows)):
            Message.warn(self, "이의 내용", "표시할 항목이 없습니다.")
            return

        rr = dict(rows[row])

        user_comment = (rr.get("comment") or "").strip()
        status_code = rr.get("dispute_status") or ""
        status_label = DISPUTE_STATUS.get(status_code, status_code)
        owner_comment = (rr.get("resolution_comment") or "").strip()
        resolved_at = (rr.get("resolved_at") or "").strip()

        full_text = (
            f"[나의 이의 내용]\n{user_comment}\n\n"
            f"[처리 상태]\n{status_label}\n\n"
            f"[사장 코멘트]\n{owner_comment or '(없음)'}\n\n"
            f"[처리 시각]\n{resolved_at or '(없음)'}"
        )

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("이의 내용/처리 결과")
        dlg.resize(700, 450)

        layout = QtWidgets.QVBoxLayout(dlg)
        edit = QtWidgets.QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(full_text)

        btn = QtWidgets.QPushButton("닫기")
        btn.clicked.connect(dlg.accept)

        layout.addWidget(edit)
        layout.addWidget(btn)
        dlg.exec_()

    def on_my_dispute_double_click(self, row: int, col: int):
        COMMENT_COL = 7
        if col != COMMENT_COL:
            return
        self._show_my_dispute_comment_popup(row)

    def on_my_dispute_double_clicked_cell(self, row: int, col: int):
        # QTableWidget (셀 기반) 처리
        COMMENT_COL = 7
        if col != COMMENT_COL: # 이의내용 컬럼(인덱스 7)만 처리
            return
        self._show_my_dispute_comment_popup(row) # 전체보기 팝업 호출

    def open_selected_dispute_comment(self):
        row = self.my_dispute_table.selected_first_row_index()
        if row < 0:
            Message.warn(self, "이의 내용", "내 이의 제기 목록에서 항목을 선택하세요.")
            return
        self._show_my_dispute_comment_popup(row)


