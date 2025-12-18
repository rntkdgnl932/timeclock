# timeclock/ui/owner_page.py
# -*- coding: utf-8 -*-
import logging
from PyQt5 import QtWidgets, QtCore, QtGui
from timeclock import backup_manager
from datetime import datetime
import os
from pathlib import Path
from timeclock.settings import DATA_DIR
import sys
import subprocess
import git

from timeclock.excel_maker import generate_payslip, create_default_template
from ui.dialogs import ConfirmPasswordDialog, ProfileEditDialog
from ui.async_helper import run_job_with_progress_async

from timeclock.utils import Message
from ui.widgets import DateRangeBar, Table
from timeclock.settings import WORK_STATUS, SIGNUP_STATUS
from ui.dialogs import ChangePasswordDialog, DisputeTimelineDialog, DateRangeDialog
from timeclock.salary import SalaryCalculator
from ui.dialogs import PersonalInfoDialog


class OwnerPage(QtWidgets.QWidget):
    logout_requested = QtCore.pyqtSignal()

    def __init__(self, db, session, parent=None):
        super().__init__(parent)
        self.db = db
        self.session = session

        self._dispute_rows = []
        self._work_rows = []
        self._member_rows = []

        self._btn_min_h = 34

        # 테마 적용
        self._apply_owner_theme()

        # ----------------------------------------------------------
        # Header (brand + actions)
        # ----------------------------------------------------------
        header_panel = QtWidgets.QFrame()
        header_panel.setObjectName("OwnerHeader")
        header_panel.setFixedHeight(110)

        header_layout = QtWidgets.QHBoxLayout(header_panel)
        header_layout.setContentsMargins(28, 18, 28, 18)
        header_layout.setSpacing(12)

        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)

        logo_label = QtWidgets.QLabel("HobbyBrown")
        logo_label.setObjectName("OwnerBrand")
        subtitle_label = QtWidgets.QLabel(f"사업주 관리 모드 | {session.username} 사장님")
        subtitle_label.setObjectName("OwnerSubtitle")

        title_box.addStretch(1)
        title_box.addWidget(logo_label)
        title_box.addWidget(subtitle_label)
        title_box.addStretch(1)

        header_layout.addLayout(title_box)
        header_layout.addStretch(1)

        self.btn_change_pw = QtWidgets.QPushButton("개인정보 변경")
        self.btn_logout = QtWidgets.QPushButton("로그아웃")

        self._set_btn_variant(self.btn_change_pw, "ghost")
        self._set_btn_variant(self.btn_logout, "danger_outline")

        self.btn_change_pw.clicked.connect(self.open_personal_info)
        self.btn_logout.clicked.connect(self.logout_requested.emit)

        header_layout.addWidget(self.btn_change_pw)
        header_layout.addWidget(self.btn_logout)

        # ----------------------------------------------------------
        # KPI cards row
        # ----------------------------------------------------------
        kpi_row = QtWidgets.QHBoxLayout()
        kpi_row.setContentsMargins(0, 0, 0, 0)
        kpi_row.setSpacing(12)

        self.kpi_work = self._mk_stat_card("근무 승인 대기", "0", "승인/반려 처리 필요")
        self.kpi_dispute = self._mk_stat_card("이의제기 진행", "0", "대화/처리 진행 필요")
        self.kpi_signup = self._mk_stat_card("가입 승인 대기", "0", "직원 가입 요청")

        kpi_row.addWidget(self.kpi_work["frame"])
        kpi_row.addWidget(self.kpi_dispute["frame"])
        kpi_row.addWidget(self.kpi_signup["frame"])

        # ----------------------------------------------------------
        # Main tabs
        # ----------------------------------------------------------
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("OwnerTabs")

        self.tabs.addTab(self._build_work_log_tab(), "근무 승인")
        self.tabs.addTab(self._build_dispute_tab(), "이의 제기")
        self.tabs.addTab(self._build_signup_tab(), "직원 가입 승인")
        self.tabs.addTab(self._build_member_tab(), "직원 관리")
        self.tabs.addTab(self._build_restore_tab(), "백업/복구")

        # ✅ [추가된 부분] 시스템 업데이트 탭
        self.tabs.addTab(self._build_update_tab(), "시스템 업데이트")

        self._tune_owner_tabbar()

        # Tabs Container
        tabs_card = QtWidgets.QFrame()
        tabs_card.setObjectName("OwnerTabsCard")
        tabs_card_layout = QtWidgets.QVBoxLayout(tabs_card)
        tabs_card_layout.setContentsMargins(14, 14, 14, 14)
        tabs_card_layout.addWidget(self.tabs)

        # ----------------------------------------------------------
        # Root layout
        # ----------------------------------------------------------
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 24)
        root.setSpacing(14)

        root.addWidget(header_panel)
        root.addLayout(kpi_row)
        root.addWidget(tabs_card, 1)

        # Initial load
        self.refresh_work_logs()
        self.refresh_members()
        self.refresh_disputes()
        self.refresh_signup_requests()
        self.update_badges()

        QtCore.QTimer.singleShot(0, self._refresh_kpis)

    # --------------------------------------------------------------
    # Theme helpers
    # --------------------------------------------------------------
    def _apply_owner_theme(self) -> None:
        # Window base
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#FCFBF8"))
        self.setPalette(pal)

        # A single stylesheet for OwnerPage (keeps UI consistent)
        self.setStyleSheet("""
            QWidget { font-family: 'Malgun Gothic', 'Segoe UI', sans-serif; font-size: 12px; color: #2b2b2b; }
            QLabel#OwnerBrand { font-size: 26px; font-weight: 900; letter-spacing: 0.5px; color: #5D4037; }
            QLabel#OwnerSubtitle { font-size: 13px; color: #6f6f6f; }

            QFrame#OwnerHeader {
                background: #ffffff;
                border: 1px solid #ececec;
                border-radius: 16px;
            }
            QFrame#OwnerTabsCard {
                background: #ffffff;
                border: 1px solid #ececec;
                border-radius: 16px;
            }

            /* Tabs */
            QTabWidget#OwnerTabs::pane { border: none; }
            QTabBar::tab {
                background: transparent;
                color: #6a6a6a;
            
                /* 글자 잘림 체감 줄이기: 높이/패딩 균형 */
                padding: 10px 18px;
                min-height: 34px;
            
                /* 탭 간격 */
                margin-right: 8px;
            
                border-radius: 12px;
                font-weight: 700;
                font-size: 12px;  /* 글자 크기 살짝 안정화 */
            
                /* 탭 폭은 내용 길이에 따라 자연스럽게 늘어나게 두되,
                   너무 작아지지 않도록 하한만 줌 */
                min-width: 120px;
            }
            
            QFrame#OwnerToolbarCard {
                background: #fafafa;
                border: 1px solid #eeeeee;
                border-radius: 14px;
            }
            QLabel#OwnerHint {
                color: #8a8a8a;
                font-weight: 700;
            }
            
            QTabBar::tab:selected {
                background: #FFF3E0;
                color: #5D4037;
            }
            
            QTabBar::tab:hover {
                background: #f5f5f5;
            }


            /* Inputs */
            QLineEdit, QComboBox, QDateEdit {
                background: #ffffff;
                border: 1px solid #dcdcdc;
                border-radius: 10px;
                padding: 6px 10px;
                min-height: 28px;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus { border: 1px solid #caa57a; }

            /* GroupBox */
            QGroupBox {
                border: 1px solid #ececec;
                border-radius: 14px;
                margin-top: 12px;
                padding: 12px;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #5D4037;
                font-weight: 800;
            }

            /* Buttons (variant by dynamic property) */
            QPushButton {
                border-radius: 10px;
                padding: 8px 14px;
                font-weight: 800;
            }
            QPushButton[variant="primary"] { background: #6D4C41; color: #ffffff; border: 1px solid #6D4C41; }
            QPushButton[variant="primary"]:hover { background: #5e4036; border-color: #5e4036; }
            QPushButton[variant="secondary"] { background: #f3f3f3; color: #333; border: 1px solid #e2e2e2; }
            QPushButton[variant="secondary"]:hover { background: #ededed; }

            QPushButton[variant="ghost"] { background: #ffffff; color: #5D4037; border: 1px solid #e7e7e7; }
            QPushButton[variant="ghost"]:hover { background: #fafafa; }

            QPushButton[variant="danger_outline"] { background: #ffffff; color: #b71c1c; border: 1px solid #f0c7c7; }
            QPushButton[variant="danger_outline"]:hover { background: #fff5f5; }

            QPushButton[variant="warn"] { background: #FFF3E0; color: #E65100; border: 1px solid #FFE0B2; }
            QPushButton[variant="warn"]:hover { background: #FFE0B2; }

            /* Tables (QTableWidget) */
            QTableWidget {
                background: #ffffff;
                border: 1px solid #e9e9e9;
                border-radius: 12px;
                gridline-color: #f1f1f1;
                selection-background-color: #FFE0B2;
                selection-color: #2b2b2b;
            }
            QHeaderView::section {
                background: #fafafa;
                border: none;
                border-bottom: 1px solid #e9e9e9;
                padding: 8px 10px;
                font-weight: 900;
                color: #5D4037;
            }
            QTableWidget::item { padding-left: 6px; padding-right: 6px; }
            QTableWidget::item:selected { background: #FFE0B2; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
            QScrollBar::handle:vertical { background: #dcdcdc; border-radius: 5px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background: #cfcfcf; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            
            
            

        """)

    def _set_btn_variant(self, btn: "QtWidgets.QPushButton", variant: str) -> None:
        btn.setProperty("variant", variant)

        # 예: OwnerPage에서 버튼 공통 최소 높이를 관리하고 싶을 때
        btn.setMinimumHeight(getattr(self, "_btn_min_h", 34))

        btn.style().unpolish(btn)
        btn.style().polish(btn)
        btn.update()

    @staticmethod
    def _mk_stat_card(title: str, value: str, hint: str = "") -> dict:
        """
        KPI 카드 생성.
        기존 코드가 self.kpi_work["frame"], self.kpi_work["value"] 형태를 쓰므로
        dict 형태로 반환해야 함.
        """
        card = QtWidgets.QFrame()
        card.setObjectName("OwnerStatCard")

        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        lb_title = QtWidgets.QLabel(title)
        lb_title.setObjectName("OwnerStatTitle")

        lb_value = QtWidgets.QLabel(str(value))
        lb_value.setObjectName("OwnerStatValue")

        lay.addWidget(lb_title)
        lay.addWidget(lb_value)

        lb_hint = None
        if hint:
            lb_hint = QtWidgets.QLabel(hint)
            lb_hint.setObjectName("OwnerStatSub")
            lay.addWidget(lb_hint)

        lay.addStretch(1)

        return {
            "frame": card,
            "title": lb_title,
            "value": lb_value,
            "hint": lb_hint,
        }

    def _refresh_kpis(self) -> None:
        try:
            counts = self.db.get_pending_counts() or {}
            self.kpi_work["value"].setText(str(int(counts.get("work", 0) or 0)))
            self.kpi_dispute["value"].setText(str(int(counts.get("dispute", 0) or 0)))
            self.kpi_signup["value"].setText(str(int(counts.get("signup", 0) or 0)))
        except Exception:
            # KPI는 UI 보조 정보이므로 실패해도 화면이 죽지 않게 처리
            logging.exception("refresh_kpis failed")

    def _tune_owner_tabbar(self) -> None:
        """
        탭 글자 잘림 방지:
        - 탭은 내용 길이대로(width) 잡고
        - 공간이 부족하면 스크롤 버튼으로 좌/우 이동
        - 글자 생략(… ) 금지
        """
        if not hasattr(self, "tabs") or self.tabs is None:
            return

        tabs = self.tabs
        bar = tabs.tabBar()

        # 핵심: 탭을 강제로 균등분할(expand)하지 않게 → 내용 길이대로
        bar.setExpanding(False)

        # 공간 부족 시 좌/우 스크롤 버튼 표시
        tabs.setUsesScrollButtons(True)

        # …(엘리드)로 잘라먹지 않게
        # noinspection PyUnresolvedReferences
        bar.setElideMode(QtCore.Qt.ElideNone)

        # 문서모드: 탭 상단 UI가 더 깔끔해지는 경향
        tabs.setDocumentMode(True)

        # 탭 클릭 영역/레이아웃 안정화
        bar.setMovable(False)
        bar.setDrawBase(False)

    @staticmethod
    def _mk_toolbar_card() -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setObjectName("OwnerToolbarCard")

        lay = QtWidgets.QHBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        return frame

    def _apply_tab_action_variants(self) -> None:
        """
        기존 탭별 개별 setStyleSheet()를 걷어내고, Owner 테마(variant)로 통일.
        호출은 각 탭 빌드 함수 내부에서 필요한 버튼에 대해 직접 _set_btn_variant로 처리해도 됨.
        """
        pass

    # ==========================================================
    # 1. 근무 기록 관리 탭
    # ==========================================================
    def _build_work_log_tab(self):
        self.filter_work = DateRangeBar(label="조회기간")
        self.filter_work.applied.connect(lambda *_: self.refresh_work_logs())

        self.cb_work_status = QtWidgets.QComboBox()
        self.cb_work_status.addItem("승인 대기 (요청 확인필요)", "PENDING")
        self.cb_work_status.addItem("근무 중 (작업 승인됨)", "WORKING")
        self.cb_work_status.addItem("승인 완료 (퇴근 확정됨)", "APPROVED")
        self.cb_work_status.addItem("전체 보기", "ALL")
        self.cb_work_status.currentIndexChanged.connect(lambda *_: self.refresh_work_logs())

        self.btn_work_refresh = QtWidgets.QPushButton("🔄 새로고침")
        self.btn_work_refresh.clicked.connect(self.refresh_work_logs)
        self._set_btn_variant(self.btn_work_refresh, "secondary")

        # 작업시작 승인 / 반려 / 퇴근 승인
        self.btn_edit_start = QtWidgets.QPushButton("✅ 작업시작 승인(시간정정)")
        self.btn_edit_start.clicked.connect(lambda: self.approve_selected_log(mode="START"))
        self._set_btn_variant(self.btn_edit_start, "primary")

        self.btn_reject_start = QtWidgets.QPushButton("⛔ 작업시작 반려")
        self.btn_reject_start.clicked.connect(self.reject_start_request)
        self._set_btn_variant(self.btn_reject_start, "secondary")

        self.btn_edit_end = QtWidgets.QPushButton("🧾 퇴근 승인(마감)")
        self.btn_edit_end.clicked.connect(lambda: self.approve_selected_log(mode="END"))
        self._set_btn_variant(self.btn_edit_end, "warn")

        self.work_table = Table([
            "ID", "일자", "근로자",
            "작업시작요청시간", "작업종료요청시간", "상태",
            "작업시작확정시간", "작업종료확정시간", "비고(코멘트)"
        ])
        self.work_table.setColumnWidth(0, 0)

        # 상단 툴바(카드)
        toolbar = self._mk_toolbar_card()
        tlay = toolbar.layout()
        tlay.addWidget(self.filter_work)
        tlay.addWidget(self.cb_work_status)
        tlay.addWidget(self.btn_work_refresh)
        # noinspection PyUnresolvedReferences
        tlay.addStretch(1)
        tlay.addWidget(self.btn_edit_start)
        tlay.addWidget(self.btn_reject_start)
        tlay.addWidget(self.btn_edit_end)

        hint = QtWidgets.QLabel("※ ‘반려’ 시 기록은 보존되며, 근로자는 다시 요청할 수 있습니다.")
        hint.setObjectName("OwnerHint")

        l = QtWidgets.QVBoxLayout()
        l.setSpacing(10)
        l.addWidget(toolbar)
        l.addWidget(hint)
        l.addWidget(self.work_table)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w

    def refresh_work_logs(self):
        d1, d2 = self.filter_work.get_range()
        status_filter = self.cb_work_status.currentData()

        try:
            rows = self.db.list_all_work_logs(None, d1, d2, status_filter=status_filter)
            self._work_rows = rows

            out = []
            for r in rows:
                rr = dict(r)
                st = rr["status"]
                st_str = WORK_STATUS.get(st, st)

                # [수정] 근로자 이름 표시 형식: 성함(ID) 또는 ID(ID)
                name = rr.get("worker_name")
                uid = rr["worker_username"]

                if name:
                    display_name = f"{name} ({uid})"
                else:
                    display_name = f"{uid} ({uid})"

                out.append([
                    str(rr["id"]),
                    rr["work_date"],
                    display_name,  # 변경된 이름 형식 적용
                    rr["start_time"] or "",
                    rr["end_time"] or "",
                    st_str,
                    rr["approved_start"] or "",
                    rr["approved_end"] or "",
                    rr["owner_comment"] or ""
                ])
            self.work_table.set_rows(out)

            self.update_badges()

        except Exception as e:
            logging.exception("refresh_work_logs failed")
            Message.err(self, "오류", f"근무 기록 조회 실패: {e}")

    def update_badges(self):
        """DB에서 대기 건수를 가져와 탭 제목과 색상을 변경"""
        counts = self.db.get_pending_counts() or {}

        def set_tab_style(index: int, title: str, count: int):
            if index >= self.tabs.count():
                return
            if count and int(count) > 0:
                self.tabs.setTabText(index, f"{title} ({int(count)})")
                self.tabs.tabBar().setTabTextColor(index, QtGui.QColor("#D32F2F"))  # red
            else:
                self.tabs.setTabText(index, title)
                self.tabs.tabBar().setTabTextColor(index, QtGui.QColor("#6a6a6a"))

        # 탭 순서(현재 코드 기준):
        # 0 근무 승인 / 1 이의 제기 / 2 직원 가입 승인 / 3 직원 관리 / 4 백업/복구
        set_tab_style(0, "근무 승인", counts.get("work", 0))
        set_tab_style(1, "이의 제기", counts.get("dispute", 0))
        set_tab_style(2, "직원 가입 승인", counts.get("signup", 0))
        # 직원 관리(3), 백업/복구(4)는 배지 없음 (원하면 추가 가능)

        self._refresh_kpis()

    # ----------------------------------------------------------------
    # [수정] 작업/퇴근 승인 (오류 수정됨: now_str -> datetime 사용)
    # ----------------------------------------------------------------
    def approve_selected_log(self, mode="START"):
        row_idx = self.work_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "알림", "승인할 항목을 선택하세요.")
            return

        target_row = dict(self._work_rows[row_idx])
        log_id = target_row["id"]

        if target_row["status"] == "APPROVED" and mode == "START":
            Message.warn(self, "알림", "이미 완료된 건입니다.")
            return

        dialog = WorkLogApproveDialog(self, target_row, mode)

        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            app_start, app_end, final_comment = dialog.get_data()

            # 1) DB 업데이트 (즉시 처리)
            try:
                self.db.approve_work_log(
                    log_id,
                    self.session.user_id,
                    app_start,
                    app_end,
                    final_comment
                )
            except Exception as e:
                Message.err(self, "오류", f"승인 실패: {e}")
                return

            # 2) 백업 수행 (비동기 진행바)
            def job_fn(progress_callback):
                if 'backup_manager' in globals():
                    return backup_manager.run_backup("approve", progress_callback)
                return True, "백업 매니저 없음"

            def on_done(ok, res, err):
                # 백업까지 다 끝나면 메시지 띄우고 목록 갱신
                # (성공 시 메시지는 async_helper가 '완료' 표시 후 자동 닫힘 처리하므로
                #  추가 메시지가 필요하면 여기서 띄웁니다.)
                if ok:
                    # Message.info(self, "완료", "승인 처리가 완료되었습니다.") # 너무 팝업이 많으면 생략 가능
                    pass
                self.refresh_work_logs()

            run_job_with_progress_async(
                self,
                "승인 데이터 백업 중...",
                job_fn,
                on_done=on_done
            )

    # [추가] 작업 시작 반려(삭제) 기능
    def reject_start_request(self):
        row_idx = self.work_table.selected_first_row_index()
        if row_idx < 0:
            Message.warn(self, "알림", "반려할 요청을 선택하세요.")
            return

        target_row = dict(self._work_rows[row_idx])

        if target_row["status"] in ["WORKING", "APPROVED"]:
            if not Message.confirm(self, "경고", "이미 승인된 작업입니다. 반려 처리하시겠습니까?\n(기록은 남지만 근무 시간에서는 제외됩니다.)"):
                return
        else:
            if not Message.confirm(self, "반려 확인", "해당 작업 요청을 반려하시겠습니까?\n근로자는 다시 요청을 보낼 수 있게 되며,\n이 기록은 '반려' 상태로 남습니다."):
                return

        # 1) DB 반려 처리 (즉시)
        try:
            self.db.reject_work_log(target_row["id"])
        except Exception as e:
            Message.err(self, "오류", f"반려 처리 실패: {e}")
            return

        # 2) 백업 수행 (비동기)
        def job_fn(progress_callback):
            return backup_manager.run_backup("reject_log", progress_callback)

        def on_done(ok, res, err):
            # 완료 후 목록 갱신
            if ok:
                # Message.info(self, "완료", "반려되었습니다.") # 필요 시 주석 해제
                pass
            self.refresh_work_logs()

        run_job_with_progress_async(
            self,
            "반려 데이터 백업 중...",
            job_fn,
            on_done=on_done
        )

    # ==========================================================
    # 2. 회원(급여) 관리 탭
    # ==========================================================
    def _build_member_tab(self):
        self.le_member_search = QtWidgets.QLineEdit()
        self.le_member_search.setPlaceholderText("이름 검색...")
        self.le_member_search.returnPressed.connect(self.refresh_members)

        self.cb_member_filter = QtWidgets.QComboBox()
        self.cb_member_filter.addItem("재직자 보기", "ACTIVE")
        self.cb_member_filter.addItem("퇴사자 보기", "INACTIVE")
        self.cb_member_filter.addItem("전체 보기", "ALL")
        self.cb_member_filter.currentIndexChanged.connect(self.refresh_members)

        self.btn_member_search = QtWidgets.QPushButton("🔍 검색")
        self.btn_member_search.clicked.connect(self.refresh_members)
        self._set_btn_variant(self.btn_member_search, "secondary")

        self.btn_edit_wage = QtWidgets.QPushButton("💳 시급 변경")
        self.btn_edit_wage.clicked.connect(self.edit_wage)
        self._set_btn_variant(self.btn_edit_wage, "secondary")

        self.btn_edit_job_title = QtWidgets.QPushButton("🏷 직급 변경")
        self.btn_edit_job_title.clicked.connect(self.edit_job_title)


        self.btn_calc_salary = QtWidgets.QPushButton("🧮 급여 정산")
        self.btn_calc_salary.clicked.connect(self.calculate_salary)
        self._set_btn_variant(self.btn_calc_salary, "warn")

        self.btn_export_payslip = QtWidgets.QPushButton("📄 명세서 발급(Excel)")
        try:
            self.btn_export_payslip.clicked.disconnect()
        except:
            pass
        self.btn_export_payslip.clicked.connect(self.export_payslip)
        self._set_btn_variant(self.btn_export_payslip, "primary")

        self.btn_resign = QtWidgets.QPushButton("🧯 퇴사 처리")
        self.btn_resign.clicked.connect(self.resign_worker)
        self._set_btn_variant(self.btn_resign, "danger_outline")

        self.member_table = Table([
            "ID", "아이디", "성함", "직급", "전화번호", "생년월일", "시급", "가입일", "상태"
        ])
        self.member_table.setColumnWidth(0, 0)
        self.member_table.itemDoubleClicked.connect(self.edit_wage)

        toolbar = self._mk_toolbar_card()
        tlay = toolbar.layout()
        tlay.addWidget(self.le_member_search)
        tlay.addWidget(self.cb_member_filter)
        tlay.addWidget(self.btn_member_search)
        # noinspection PyUnresolvedReferences
        tlay.addStretch(1)
        tlay.addWidget(self.btn_edit_wage)
        tlay.addWidget(self.btn_edit_job_title)
        tlay.addWidget(self.btn_calc_salary)
        tlay.addWidget(self.btn_export_payslip)
        tlay.addWidget(self.btn_resign)

        l = QtWidgets.QVBoxLayout()
        l.setSpacing(10)
        l.addWidget(toolbar)
        l.addWidget(self.member_table)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w

    def refresh_members(self):
        keyword = self.le_member_search.text().strip()
        status_filter = self.cb_member_filter.currentData()

        try:
            rows = self.db.list_workers(keyword=keyword, status_filter=status_filter)
            self._member_rows = rows
            out = []
            for r in rows:
                rr = dict(r)
                wage_str = f"{rr['hourly_wage']:,}" if rr['hourly_wage'] else "0"
                status = "재직중" if rr['is_active'] else "퇴사"

                out.append([
                    str(rr['id']),
                    rr['username'],
                    rr.get('name') or "",
                    rr.get('job_title') or "사원",
                    rr.get('phone') or "",
                    rr.get('birthdate') or "",
                    wage_str,
                    rr['created_at'],
                    status
                ])

            self.member_table.set_rows(out)
        except Exception as e:
            Message.err(self, "오류", f"회원 목록 로드 실패: {e}")

    def resign_worker(self):
        row = self.member_table.selected_first_row_index()
        if row < 0:
            Message.warn(self, "알림", "퇴사 처리할 직원을 선택하세요.")
            return

        rr = dict(self._member_rows[row])
        user_id = rr['id']
        username = rr['username']
        is_active = rr['is_active']

        if is_active == 0:
            Message.warn(self, "알림", "이미 퇴사 처리된 직원입니다.")
            return

        if Message.confirm(self, "퇴사 확인", f"정말 '{username}' 님을 퇴사 처리하시겠습니까?\n(계정은 삭제되지 않고 비활성화됩니다)"):
            try:
                self.db.resign_user(user_id)
                Message.info(self, "완료", "퇴사 처리가 완료되었습니다.")
                self.refresh_members()
            except Exception as e:
                Message.err(self, "오류", str(e))

    def edit_wage(self):
        row = self.member_table.selected_first_row_index()
        if row < 0:
            Message.warn(self, "알림", "시급을 변경할 회원을 선택하세요.")
            return

        rr = dict(self._member_rows[row])
        user_id = rr['id']
        username = rr['username']
        current_wage = rr['hourly_wage'] or 9860

        val, ok = QtWidgets.QInputDialog.getInt(
            self, "시급 변경",
            f"'{username}' 님의 새로운 시급을 입력하세요:",
            current_wage, 0, 1000000, 10
        )
        if ok:
            try:
                self.db.update_user_wage(user_id, val)
                Message.info(self, "완료", f"{username}님의 시급이 {val:,}원으로 변경되었습니다.")
                self.refresh_members()
            except Exception as e:
                Message.err(self, "오류", str(e))

    def edit_job_title(self):
        row = self.member_table.selected_first_row_index()
        if row < 0:
            Message.warn(self, "알림", "직급을 변경할 회원을 선택하세요.")
            return

        rr = dict(self._member_rows[row])
        user_id = rr['id']
        username = rr['username']
        current = (rr.get("job_title") or "사원").strip()

        from timeclock.settings import JOB_TITLES, DEFAULT_JOB_TITLE
        items = JOB_TITLES[:] if JOB_TITLES else ["대표", "실장", "사원"]
        if current not in items:
            current = DEFAULT_JOB_TITLE if DEFAULT_JOB_TITLE in items else items[-1]

        val, ok = QtWidgets.QInputDialog.getItem(
            self,
            "직급 변경",
            f"'{username}' 님의 직급을 선택하세요:",
            items,
            items.index(current),
            False
        )
        if not ok:
            return

        val = (val or "").strip()
        if not val:
            return

        try:
            self.db.update_user_job_title(user_id, val)
            Message.info(self, "완료", f"{username}님의 직급이 '{val}'로 변경되었습니다.")
            self.refresh_members()
        except Exception as e:
            Message.err(self, "오류", str(e))

    # ==========================================================
    # 3. 이의 제기 탭
    # ==========================================================
    def _build_dispute_tab(self):
        self.filter_disputes = DateRangeBar(label="이의제기 조회기간")
        self.filter_disputes.applied.connect(lambda *_: self.refresh_disputes())

        self.cb_dispute_filter = QtWidgets.QComboBox()
        self.cb_dispute_filter.addItem("진행 중 (검토/미처리)", "ACTIVE")
        self.cb_dispute_filter.addItem("종료 (완료/기각)", "CLOSED")
        self.cb_dispute_filter.currentIndexChanged.connect(lambda *_: self.refresh_disputes())

        self.btn_disputes_refresh = QtWidgets.QPushButton("🔍 조회")
        self.btn_disputes_refresh.clicked.connect(self.refresh_disputes)
        self._set_btn_variant(self.btn_disputes_refresh, "secondary")

        self.btn_open_chat = QtWidgets.QPushButton("💬 선택 건 채팅방 열기")
        self.btn_open_chat.clicked.connect(self.open_dispute_chat)
        self._set_btn_variant(self.btn_open_chat, "primary")

        self.dispute_table = Table([
            "ID", "근로자", "근무일자", "이의유형", "상태", "최근대화", "등록일"
        ])
        self.dispute_table.setColumnWidth(0, 0)
        QtCore.QTimer.singleShot(0, self._wire_dispute_doubleclick)

        toolbar = self._mk_toolbar_card()
        tlay = toolbar.layout()
        tlay.addWidget(self.filter_disputes)
        tlay.addWidget(self.cb_dispute_filter)
        tlay.addWidget(self.btn_disputes_refresh)
        # noinspection PyUnresolvedReferences
        tlay.addStretch(1)
        tlay.addWidget(self.btn_open_chat)

        l = QtWidgets.QVBoxLayout()
        l.setSpacing(10)
        l.addWidget(toolbar)
        l.addWidget(self.dispute_table)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w

    def refresh_disputes(self):
        d1, d2 = self.filter_disputes.get_range()
        filter_type = self.cb_dispute_filter.currentData()

        try:
            rows = self.db.list_disputes(d1, d2, filter_type)
            self._dispute_rows = rows

            out = []
            for r in rows:
                rr = dict(r)
                st = rr["status"]
                st_map = {"PENDING": "미처리", "IN_REVIEW": "검토중", "RESOLVED": "완료", "REJECTED": "기각"}

                summary = (rr["comment"] or "").replace("\n", " ")
                if len(summary) > 30: summary = summary[:30] + "..."

                out.append([
                    str(rr["id"]),
                    rr["worker_username"],
                    rr["work_date"],
                    rr["dispute_type"],
                    st_map.get(st, st),
                    summary,
                    rr["created_at"]
                ])
            self.dispute_table.set_rows(out)

            self.update_badges()

        except Exception as e:
            logging.exception("refresh_disputes failed")
            Message.err(self, "오류", f"이의제기 로드 실패: {e}")

    def _wire_dispute_doubleclick(self):
        try:
            self.dispute_table.itemDoubleClicked.disconnect()
        except:
            pass
        self.dispute_table.itemDoubleClicked.connect(self.open_dispute_chat)

    def open_dispute_chat(self):
        row = self.dispute_table.selected_first_row_index()
        if row < 0 or row >= len(self._dispute_rows):
            Message.warn(self, "알림", "목록에서 항목을 선택하세요.")
            return

        rr = dict(self._dispute_rows[row])
        dispute_id = int(rr["id"])

        dlg = DisputeTimelineDialog(
            parent=self,
            db=self.db,
            user_id=self.session.user_id,
            dispute_id=dispute_id,
            my_role="owner"
        )
        dlg.exec_()
        self.refresh_disputes()

    # ==========================================================
    # 4. 가입 신청 관리
    # ==========================================================
    def _build_signup_tab(self):
        self.btn_approve_signup = QtWidgets.QPushButton("✅ 선택 가입 승인")
        self.btn_reject_signup = QtWidgets.QPushButton("⛔ 선택 가입 거절")
        self.btn_refresh_signup = QtWidgets.QPushButton("🔄 새로고침")

        self.btn_approve_signup.clicked.connect(self.approve_signup)
        self.btn_reject_signup.clicked.connect(self.reject_signup)
        self.btn_refresh_signup.clicked.connect(self.refresh_signup_requests)

        self._set_btn_variant(self.btn_approve_signup, "primary")
        self._set_btn_variant(self.btn_reject_signup, "danger_outline")
        self._set_btn_variant(self.btn_refresh_signup, "secondary")

        self.signup_table = Table(["ID", "신청ID", "전화번호", "생년월일", "신청일", "상태"])
        self.signup_table.setColumnWidth(0, 0)

        toolbar = self._mk_toolbar_card()
        tlay = toolbar.layout()
        tlay.addWidget(self.btn_approve_signup)
        tlay.addWidget(self.btn_reject_signup)
        tlay.addWidget(self.btn_refresh_signup)
        # noinspection PyUnresolvedReferences
        tlay.addStretch(1)

        hint = QtWidgets.QLabel("※ 승인 시 계정이 생성됩니다. 거절 사유는 신청자에게 기록됩니다.")
        hint.setObjectName("OwnerHint")

        l = QtWidgets.QVBoxLayout()
        l.setSpacing(10)
        l.addWidget(toolbar)
        l.addWidget(hint)
        l.addWidget(self.signup_table)

        w = QtWidgets.QWidget()
        w.setLayout(l)
        return w

    def refresh_signup_requests(self):
        try:
            rows = self.db.list_pending_signup_requests()
            data = []

            for r in rows:
                rr = dict(r)
                phone = rr.get("phone", "")
                raw_status = rr["status"]
                status_str = SIGNUP_STATUS.get(raw_status, raw_status)

                data.append([
                    rr["id"],
                    rr["username"],
                    phone,
                    rr["birthdate"],
                    rr["created_at"],
                    status_str
                ])
            self.signup_table.set_rows(data)

            self.update_badges()

        except Exception as e:
            Message.err(self, "오류", str(e))

    def approve_signup(self):
        row = self.signup_table.selected_first_row_index()
        if row < 0: return
        sid = int(self.signup_table.get_cell(row, 0))
        name = self.signup_table.get_cell(row, 1)

        if Message.confirm(self, "승인", f"'{name}'님의 가입을 승인하시겠습니까?"):
            try:
                self.db.approve_signup_request(sid, self.session.user_id, "Approved")
                Message.info(self, "완료", "계정이 생성되었습니다.")
                self.refresh_signup_requests()
                self.refresh_members()
            except Exception as e:
                Message.err(self, "오류", str(e))

    def reject_signup(self):
        row = self.signup_table.selected_first_row_index()
        if row < 0: return
        sid = int(self.signup_table.get_cell(row, 0))

        text, ok = QtWidgets.QInputDialog.getText(self, "거절", "거절 사유:")
        if ok:
            try:
                self.db.reject_signup_request(sid, self.session.user_id, text)
                Message.info(self, "완료", "거절 처리되었습니다.")
                self.refresh_signup_requests()
            except Exception as e:
                Message.err(self, "오류", str(e))

    def change_password(self):
        dlg = ChangePasswordDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            pw = dlg.get_password()
            if pw:
                self.db.change_password(self.session.user_id, pw)
                Message.info(self, "성공", "비밀번호가 변경되었습니다.")

    def calculate_salary(self):
        try:
            row = self.member_table.selected_first_row_index()
            if row < 0:
                Message.warn(self, "알림", "급여를 정산할 직원을 목록에서 선택하세요.")
                return

            rr = dict(self._member_rows[row])
            user_id = rr['id']
            hourly_wage = rr['hourly_wage'] or 0

            dlg = DateRangeDialog(self)
            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                return

            d1, d2 = dlg.get_range()
            logs = self.db.list_all_work_logs(user_id, d1, d2, status_filter='APPROVED')

            if not logs:
                Message.info(self, "결과", "해당 기간에 승인된 근무 기록이 없습니다.")
                return

            calc = SalaryCalculator(wage_per_hour=hourly_wage)
            res = calc.calculate_period([dict(r) for r in logs])

            if not res:
                Message.info(self, "결과", "계산할 데이터가 없습니다.")
                return

            final_pay = res['grand_total']
            details = res.get('ju_hyu_details', [])
            if details:
                detail_str = " + ".join([f"{x:,}" for x in details])
                ju_hyu_msg = f"주휴수당: {detail_str} = {res['ju_hyu_pay']:,}원"
            else:
                ju_hyu_msg = f"주휴수당: {res['ju_hyu_pay']:,}원"

            msg = (
                f"[{d1} ~ {d2} 급여 정산 결과]\n\n"
                f"• 총 근무시간: {res['total_hours']}시간\n"
                f"• 실제 근무(공제후): {res['actual_hours']}시간\n\n"
                f"-------------- 상세 내역 --------------\n"
                f"1. 기본급: {res['base_pay']:,}원\n"
                f"2. 연장수당: {res['overtime_pay']:,}원 (8h 초과)\n"
                f"3. 야간수당: {res['night_pay']:,}원 (22시~06시)\n"
                f"4. {ju_hyu_msg}\n"
                f"---------------------------------------\n"
                f"💰 예상 지급 총액: {final_pay:,}원"
            )
            QtWidgets.QMessageBox.information(self, "예상 급여 내역", msg)
        except Exception as e:
            import traceback
            traceback.print_exc()
            Message.err(self, "오류", f"계산 중 오류가 발생했습니다.\n{e}")

    def export_payslip(self):
        row = self.member_table.selected_first_row_index()
        if row < 0:
            Message.warn(self, "알림", "명세서를 발급할 직원을 선택하세요.")
            return

        rr = dict(self._member_rows[row])
        user_id = rr['id']
        username = rr['username']
        real_name = rr.get('name') or username
        hourly_wage = rr['hourly_wage'] or 0

        # ✅ 직급: DB 컬럼명이 job_title로 들어오는 구조를 전제로 하되,
        # 혹시 다른 키로 들어오면 안전하게 보정
        rank = (rr.get("job_title") or rr.get("rank") or "사원").strip() if rr else "사원"
        if not rank:
            rank = "사원"

        dlg = DateRangeDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        d1, d2 = dlg.get_range()

        logs = self.db.list_all_work_logs(user_id, d1, d2, status_filter='APPROVED')
        if not logs:
            Message.warn(self, "알림", "해당 기간에 승인된 근무 기록이 없습니다.")
            return

        # 1. 계산기 실행
        calc = SalaryCalculator(hourly_wage)
        res = calc.calculate_period([dict(r) for r in logs])

        # salary.py의 친절한 설명 기능 호출
        friendly_text = calc.get_friendly_description(res)

        total_pay = res['grand_total']

        # 공제 항목 (약식 계산)
        ei_tax = int(total_pay * 0.009 / 10) * 10
        pension = 0
        health = 0
        care = 0
        income_tax = 0
        local_tax = 0
        total_deduction = ei_tax + pension + health + care + income_tax + local_tax
        net_pay = total_pay - total_deduction

        # 상세 항목 텍스트 생성
        over_hours = 0
        night_hours = 0
        ju_hyu_hours = 0

        if hourly_wage > 0:
            over_hours = round(res['overtime_pay'] / (hourly_wage * 0.5), 1) if hourly_wage else 0
            night_hours = round(res['night_pay'] / (hourly_wage * 0.5), 1) if hourly_wage else 0
            ju_hyu_hours = round(res['ju_hyu_pay'] / hourly_wage, 1) if hourly_wage else 0

        base_str = f"• 기본급: {res['actual_hours']}시간 × {hourly_wage:,}원 = {res['base_pay']:,}원"

        if res['overtime_pay'] > 0 or res['night_pay'] > 0:
            over_msg = []
            if res['overtime_pay'] > 0:
                over_msg.append(f"연장 {over_hours}h")
            if res['night_pay'] > 0:
                over_msg.append(f"야간 {night_hours}h")
            sum_add_pay = res['overtime_pay'] + res['night_pay']
            over_str = f"• 가산(0.5배): {' + '.join(over_msg)} = {sum_add_pay:,}원"
        else:
            over_str = "• 가산수당: 해당 없음"

        if res['ju_hyu_pay'] > 0:
            ju_hyu_str = f"• 주휴수당: {ju_hyu_hours}시간 (주 15시간↑ 개근) = {res['ju_hyu_pay']:,}원"
        else:
            ju_hyu_str = "• 주휴수당: 해당 없음 (조건 미충족)"

        if res['ju_hyu_pay'] > 0:
            note_text = (
                "※ 주휴수당 지급 안내:\n"
                "본 주는 일시적 업무 증가로 주 15시간 이상 근무하여\n"
                "근로기준법에 의거 주휴수당을 지급하였습니다."
            )
        else:
            note_text = "※ 본 명세서는 근로기준법 제48조에 따라 교부합니다."

        # 2. 데이터 포장
        data_ctx = {
            "title": f"{d1[:4]}년 {d1[5:7]}월 급여명세서",
            "name": real_name,
            "period": f"{d1} ~ {d2}",
            "pay_date": datetime.now().strftime("%Y-%m-%d"),

            # ✅ 추가: 직급 치환 변수
            "rank": rank,

            "company": "Hobby Brown",

            "base_pay": res['base_pay'],
            "ju_hyu_pay": res['ju_hyu_pay'],
            "overtime_pay": res['overtime_pay'],
            "night_pay": res['night_pay'],
            "holiday_pay": res['holiday_pay'],
            "other_pay": 0,
            "total_pay": total_pay,

            "ei_ins": ei_tax,
            "pension": pension,
            "health_ins": health,
            "care_ins": care,
            "income_tax": income_tax,
            "local_tax": local_tax,
            "total_deduction": total_deduction,
            "net_pay": net_pay,

            "calc_detail": friendly_text,

            "base_detail": base_str,
            "over_detail": over_str,
            "ju_hyu_detail": ju_hyu_str,
            "tax_detail": "고용보험 0.9%",
            "note": note_text
        }

        try:
            template_path = DATA_DIR / "template.xlsx"
            if not template_path.exists():
                print(f"템플릿이 없어서 새로 만듭니다: {template_path}")
                create_default_template(str(template_path))

            save_dir = Path(r"C:\my_games\timeclock\pay_result")
            save_dir.mkdir(parents=True, exist_ok=True)

            safe_d1 = d1.replace("-", "")
            safe_d2 = d2.replace("-", "")
            filename = f"급여명세서_{real_name}_{safe_d1}_{safe_d2}.xlsx"
            target_path = save_dir / filename

            save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "명세서 저장",
                str(target_path),
                "Excel Files (*.xlsx)"
            )

            if save_path:
                result = generate_payslip(str(template_path), save_path, data_ctx)
                if result:
                    Message.info(self, "완료", f"급여명세서가 생성되었습니다.\n{save_path}")
                    try:
                        os.startfile(os.path.dirname(save_path))
                    except:
                        pass
                else:
                    Message.err(self, "실패", "엑셀 파일 생성 중 오류가 발생했습니다.")

        except Exception as e:
            logging.exception("export_payslip failed")
            Message.err(self, "오류", f"명세서 발급 실패: {e}")

    # ==========================================================
    # 5. 데이터 복구 탭
    # ==========================================================
    def _build_restore_tab(self):
        layout = QtWidgets.QVBoxLayout()

        lbl_info = QtWidgets.QLabel("⚠️ 원하는 시점을 선택하고 [복구]를 누르면, 데이터가 그 시절로 돌아갑니다.")
        lbl_info.setStyleSheet("color: #d32f2f; font-weight: bold; margin: 10px;")
        layout.addWidget(lbl_info)

        # -------------------------------------------------------
        # [수정] 구글 드라이브 관련 버튼들
        # -------------------------------------------------------
        gdrive_layout = QtWidgets.QHBoxLayout()

        self.btn_gdrive_auth = QtWidgets.QPushButton("🌍 1. 구글 연동 (로그인)")
        self.btn_gdrive_auth.setStyleSheet("background-color: #E8F5E9; color: #2E7D32; font-weight: bold;")
        self.btn_gdrive_auth.clicked.connect(self.auth_gdrive)

        self.btn_gdrive_test = QtWidgets.QPushButton("🚀 2. 테스트 파일 업로드")
        self.btn_gdrive_test.setStyleSheet("background-color: #E3F2FD; color: #1565C0; font-weight: bold;")
        self.btn_gdrive_test.clicked.connect(self.test_gdrive_upload)

        gdrive_layout.addWidget(self.btn_gdrive_auth)
        gdrive_layout.addWidget(self.btn_gdrive_test)
        layout.addLayout(gdrive_layout)
        # -------------------------------------------------------

        btn_layout = QtWidgets.QHBoxLayout()
        btn_refresh = QtWidgets.QPushButton("🔄 목록 새로고침")
        btn_refresh.clicked.connect(self.refresh_backup_list)
        btn_manual = QtWidgets.QPushButton("💾 현재 상태 수동 저장")
        btn_manual.clicked.connect(self.manual_backup)

        btn_layout.addWidget(btn_refresh)
        btn_layout.addWidget(btn_manual)
        layout.addLayout(btn_layout)

        self.table_backup = QtWidgets.QTableWidget()
        self.table_backup.setColumnCount(4)
        self.table_backup.setHorizontalHeaderLabels(["저장 시각", "저장 이유", "크기", "파일명(숨김)"])
        self.table_backup.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table_backup.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table_backup.setColumnHidden(3, True)
        self.table_backup.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table_backup.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table_backup)

        self.btn_restore = QtWidgets.QPushButton("⏳ 선택한 시점으로 되돌리기 (복구)")
        self.btn_restore.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; padding: 12px;")
        self.btn_restore.clicked.connect(self.run_restore)
        layout.addWidget(self.btn_restore)

        self.refresh_backup_list()

        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    # [추가] 핸들러 함수들
    def auth_gdrive(self):
        ok, msg = backup_manager.authenticate_gdrive()
        if ok:
            Message.info(self, "성공", msg)
        else:
            Message.err(self, "실패", msg)

    def test_gdrive_upload(self):
        ok, msg = backup_manager.test_gdrive_upload()
        if ok:
            Message.info(self, "성공", msg)
        else:
            Message.err(self, "업로드 실패", msg)

    def refresh_backup_list(self):
        data = backup_manager.get_backup_list()
        self.table_backup.setRowCount(0)

        for item in data:
            row = self.table_backup.rowCount()
            self.table_backup.insertRow(row)

            self.table_backup.setItem(row, 0, QtWidgets.QTableWidgetItem(item['time']))
            self.table_backup.setItem(row, 1, QtWidgets.QTableWidgetItem(item['reason']))
            self.table_backup.setItem(row, 2, QtWidgets.QTableWidgetItem(item['size']))
            self.table_backup.setItem(row, 3, QtWidgets.QTableWidgetItem(item['filename']))

    def manual_backup(self):
        res = QtWidgets.QMessageBox.question(self, "저장", "현재 데이터를 백업하시겠습니까?",
                                             QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if res != QtWidgets.QMessageBox.Yes:
            return

        # 비동기 백업 실행
        def job_fn(progress_callback):
            return backup_manager.run_backup("manual", progress_callback)

        def on_done(ok, res, err):
            # 백업이 끝나면 목록을 새로고침
            self.refresh_backup_list()
            # async_helper가 성공 시 자동으로 "완료" 후 닫히므로
            # 별도 팝업은 띄우지 않아도 깔끔합니다.

        run_job_with_progress_async(
            self,
            "수동 백업 진행 중...",
            job_fn,
            on_done=on_done
        )

    def run_restore(self):
        row = self.table_backup.currentRow()
        if row < 0:
            Message.warn(self, "선택", "복구할 시점을 목록에서 선택해주세요.")
            return

        time_str = self.table_backup.item(row, 0).text()
        reason_str = self.table_backup.item(row, 1).text()
        filename = self.table_backup.item(row, 3).text()

        msg = (f"정말 데이터를 되돌리시겠습니까?\n\n"
               f"선택한 시점: {time_str}\n"
               f"내용: {reason_str}\n\n"
               f"⚠️ 주의: 복구 시, 현재 데이터는 덮어씌워집니다.\n"
               f"(안전을 위해, 복구 직전 상태가 한 번 더 자동 저장됩니다.)")

        res = QtWidgets.QMessageBox.warning(self, "데이터 복구", msg,
                                            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)

        if res == QtWidgets.QMessageBox.Yes:
            ok, result_msg = backup_manager.restore_backup(filename)
            if ok:
                QtWidgets.QMessageBox.information(self, "복구 완료",
                                                  "데이터가 성공적으로 복구되었습니다.\n안전한 적용을 위해 프로그램이 종료됩니다.\n다시 실행해주세요.")
                QtWidgets.QApplication.quit()
            else:
                Message.err(self, "오류", result_msg)
            self.refresh_backup_list()

    def open_profile_settings(self):
        """개인정보 변경: 현재 비밀번호 재확인 → 개인정보 수정 UI."""
        dlg = ConfirmPasswordDialog(self, title="개인정보 변경", message="개인정보 변경을 위해 현재 비밀번호를 다시 입력해 주세요.")
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        pw = dlg.password()
        try:
            ok = self.db.verify_user_password(self.session.user_id, pw)
        except Exception:
            ok = False

        if not ok:
            QtWidgets.QMessageBox.warning(self, "실패", "비밀번호가 올바르지 않습니다.")
            return

        edit = ProfileEditDialog(self.db, self.session.user_id, parent=self)
        edit.exec_()

    def open_personal_info(self):
        dlg = PersonalInfoDialog(self.db, self.session.user_id, self)
        dlg.exec_()

    # ----------------------------------------------------------------------
    # [시스템 업데이트 탭] UI 및 기능
    # ----------------------------------------------------------------------
    def _build_update_tab(self):
        layout = QtWidgets.QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(50, 50, 50, 50)

        # 🟢 레이아웃 전체를 가운데 정렬
        layout.setAlignment(QtCore.Qt.AlignCenter)

        # 아이콘
        lbl_icon = QtWidgets.QLabel("🚀")
        lbl_icon.setStyleSheet("font-size: 60px; background: transparent;")
        lbl_icon.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(lbl_icon)

        # 제목
        lbl_title = QtWidgets.QLabel("최신 버전 업데이트")
        lbl_title.setStyleSheet("font-size: 24px; font-weight: bold; color: #333; background: transparent;")
        lbl_title.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(lbl_title)

        # 설명
        lbl_desc = QtWidgets.QLabel(
            "서버(GitHub)에 올라온 최신 기능과 버그 수정 사항을 다운로드합니다.\n"
            "업데이트가 완료되면 프로그램이 자동으로 재시작됩니다."
        )
        lbl_desc.setStyleSheet("font-size: 14px; color: #666; line-height: 1.5; background: transparent;")
        lbl_desc.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(lbl_desc)

        # 🟢 [수정] 업데이트 버튼 (가운데 정렬 속성 명시)
        self.btn_update = QtWidgets.QPushButton("지금 업데이트 실행 (Git Pull)")
        self.btn_update.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_update.setFixedSize(280, 55)  # 크기 조금 더 키움
        self.btn_update.setStyleSheet("""
            QPushButton {
                background-color: #2196F3; 
                color: white; 
                border-radius: 27px;
                font-size: 16px; 
                font-weight: bold;
                border: 1px solid #1976D2;
            }
            QPushButton:hover { 
                background-color: #1976D2; 
                border: 1px solid #1565C0;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
        """)
        self.btn_update.clicked.connect(self.run_git_update)

        # addWidget 할 때 정렬 옵션(Qt.AlignCenter)을 한 번 더 줘서 확실하게 가운데로 보냄
        layout.addWidget(self.btn_update, 0, QtCore.Qt.AlignCenter)

        # 저장소 주소
        lbl_repo = QtWidgets.QLabel("Repository: https://github.com/rntkdgnl932/timeclock.git")
        lbl_repo.setStyleSheet("font-size: 11px; color: #999; margin-top: 20px; background: transparent;")
        lbl_repo.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(lbl_repo)

        layout.addStretch(1)

        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    def run_git_update(self):
        # 1. 실행 전 확인
        if not Message.confirm(self, "업데이트", "최신 버전을 다운로드하고 프로그램을 재시작하시겠습니까?"):
            return

        # 2. 업데이트 작업 (사용자님이 주신 코드 로직 적용)
        def job_fn(progress_callback):
            import git  # GitPython 라이브러리 사용

            progress_callback({"msg": "업데이트 다운로드 중 (Git Pull)..."})

            # 👇 말씀하신 핵심 코드 그대로 적용
            my_repo = git.Repo()
            my_repo.remotes.origin.pull()

            return "업데이트 성공"

        # 3. 완료 후 재시작
        def on_done(ok, res, err):
            if ok:
                # 업데이트 성공 시 바로 재시작
                import time
                import sys
                import os

                time.sleep(1)
                os.execl(sys.executable, sys.executable, *sys.argv)
            else:
                # 실패 시 에러 메시지는 async_helper 창에 남습니다.
                pass

                # 4. 실행 (UI 멈춤 방지를 위해 스레드로 실행)

        run_job_with_progress_async(
            self,
            "시스템 업데이트",
            job_fn,
            on_done=on_done
        )



    def _restart_program(self):
        """현재 파이썬 프로그램을 재시작합니다."""
        try:
            # 🔴 import sys가 상단에 있는지 꼭 확인하세요!
            python = sys.executable
            os.execl(python, python, *sys.argv)
        except Exception as e:
            Message.err(self, "재시작 실패", f"자동 재시작에 실패했습니다. 수동으로 다시 실행해주세요.\n{e}")

    def _restart_program(self):
        """현재 파이썬 프로그램을 재시작합니다."""
        try:
            python = sys.executable
            os.execl(python, python, *sys.argv)
        except Exception as e:
            Message.err(self, "재시작 실패", f"자동 재시작에 실패했습니다. 수동으로 다시 실행해주세요.\n{e}")

class WorkLogApproveDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, row_data=None, mode="START"):
        super().__init__(parent)
        self.data = row_data or {}
        self.mode = mode

        # 제목 설정
        if self.mode == "START":
            self.setWindowTitle("작업 시작 승인 (시간 확정)")
        else:
            self.setWindowTitle("퇴근 승인 (시간 확정)")

        self.resize(400, 200)

        layout = QtWidgets.QVBoxLayout()

        # [1] 상단 안내
        if self.mode == "END":
            info_text = (
                f"근로자: {self.data.get('worker_username')}\n"
                f"※ [확인] 클릭 시, 근무 시간에 따라 휴게시간 부여 여부를 묻고\n"
                f"   퇴근 시간을 자동으로 연장합니다."
            )
        else:
            info_text = f"근로자: {self.data.get('worker_username')}\n시작 시간을 확정해주세요."

        lbl_info = QtWidgets.QLabel(info_text)
        lbl_info.setStyleSheet("background-color: #f0f0f0; padding: 10px; border-radius: 5px; color: #333;")
        layout.addWidget(lbl_info)

        form = QtWidgets.QFormLayout()

        # [2] 날짜/시간 에디터
        self.dte_start = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.dte_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dte_start.setCalendarPopup(True)

        self.dte_end = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTime())
        self.dte_end.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dte_end.setCalendarPopup(True)

        # 초기값 주입
        s_time_str = self.data.get("approved_start") or self.data.get("start_time")
        e_time_str = self.data.get("approved_end") or self.data.get("end_time")

        if s_time_str:
            self.dte_start.setDateTime(QtCore.QDateTime.fromString(s_time_str, "yyyy-MM-dd HH:mm:ss"))

        if e_time_str:
            self.dte_end.setDateTime(QtCore.QDateTime.fromString(e_time_str, "yyyy-MM-dd HH:mm:ss"))
        else:
            self.dte_end.setDateTime(QtCore.QDateTime.currentDateTime())

        # [3] 잠금 처리
        disabled_style = "background-color: #e0e0e0; color: #666; border: 1px solid #ccc;"
        active_style = "background-color: #ffffff; color: #000; font-weight: bold;"

        if self.mode == "START":
            self.dte_end.setDisabled(True)
            self.dte_end.setStyleSheet(disabled_style)
            self.dte_start.setStyleSheet(active_style)
        else:
            self.dte_start.setDisabled(True)
            self.dte_start.setStyleSheet(disabled_style)
            self.dte_end.setStyleSheet(active_style)

        # [4] 비고 (콤보박스 없음)
        self.cb_comment = QtWidgets.QComboBox()
        self.cb_comment.setEditable(True)
        self.cb_comment.setPlaceholderText("특이사항이 있다면 입력하세요.")
        standard_reasons = ["", "정상 승인", "지각 처리", "조퇴 처리", "업무 연장", "기타"]
        self.cb_comment.addItems(standard_reasons)

        old_comment = self.data.get("owner_comment")
        if old_comment:
            self.cb_comment.setCurrentText(old_comment)

        form.addRow("확정 시작시각", self.dte_start)
        form.addRow("확정 종료시각", self.dte_end)
        form.addRow("관리자 메모", self.cb_comment)

        layout.addLayout(form)

        # [5] 버튼
        btns = QtWidgets.QHBoxLayout()
        btn_label = "작업 시작 승인" if self.mode == "START" else "퇴근 및 시간 확정"

        self.btn_ok = QtWidgets.QPushButton(btn_label)
        # noinspection PyUnresolvedReferences
        self.btn_ok.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_ok.setStyleSheet("""
            QPushButton {
                font-weight: bold; color: white; background-color: #003366; 
                padding: 10px; border-radius: 4px;
            }
            QPushButton:hover { background-color: #004080; }
        """)
        self.btn_ok.clicked.connect(self.on_ok_clicked)

        self.btn_cancel = QtWidgets.QPushButton("취소")
        # noinspection PyUnresolvedReferences
        self.btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)

        btns.addStretch(1)
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)

        layout.addLayout(btns)
        self.setLayout(layout)

    def on_ok_clicked(self):
        if self.mode == "END":
            s_dt = self.dte_start.dateTime()
            e_dt = self.dte_end.dateTime()

            secs = s_dt.secsTo(e_dt)
            hours = secs / 3600.0

            added_min = 0
            break_label = ""

            if hours >= 8:
                added_min = 60
                break_label = "1시간"
            elif hours >= 4:
                added_min = 30
                break_label = "30분"

            if added_min > 0:
                msg = (f"현재 근무시간: 약 {hours:.1f}시간\n\n"
                       f"법정 휴게시간 [{break_label}]을 부여하셨습니까?\n"
                       f"('예'를 누르면 퇴근 시간이 {break_label} 연장됩니다)")

                ans = QtWidgets.QMessageBox.question(self, "휴게시간 확인", msg,
                                                     QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)

                if ans == QtWidgets.QMessageBox.Yes:
                    # (1) 퇴근 시간 연장
                    new_e_dt = e_dt.addSecs(added_min * 60)
                    self.dte_end.setDateTime(new_e_dt)

                    # (2) 스마트 필터링 (00분, 30분 단위 정렬)
                    time_slots = []

                    # 시작 시간을 다음 30분 단위로 올림(Ceiling)
                    # 예: 09:22 -> 09:30, 09:40 -> 10:00
                    curr = s_dt
                    mm = curr.time().minute()
                    ss = curr.time().second()

                    # 정각이나 30분이 아니면 앞으로 당김
                    if not (mm == 0 and ss == 0) and not (mm == 30 and ss == 0):
                        if mm < 30:
                            # 30분으로 이동
                            add_sec = (30 - mm) * 60 - ss
                        else:
                            # 다음 시간 00분으로 이동
                            add_sec = (60 - mm) * 60 - ss
                        curr = curr.addSecs(add_sec)

                    required_gap = added_min * 60

                    # 루프: 근무 시간 내에서 30분 간격으로 생성
                    while curr.secsTo(new_e_dt) >= required_gap:
                        nxt = curr.addSecs(required_gap)

                        # 리스트에 추가 (깔끔한 00/30분 단위)
                        slot_str = f"{curr.toString('HH:mm')} ~ {nxt.toString('HH:mm')}"
                        time_slots.append(slot_str)

                        # 다음 보기는 30분 뒤
                        curr = curr.addSecs(30 * 60)

                    time_slots.append("직접 입력")

                    item, ok = QtWidgets.QInputDialog.getItem(
                        self,
                        "휴게시간대 선택",
                        f"부여한 휴게시간({break_label}) 선택 (30분 단위 자동정렬):",
                        time_slots,
                        0,
                        False
                    )

                    if ok and item:
                        final_break_str = item
                        if item == "직접 입력":
                            text, txt_ok = QtWidgets.QInputDialog.getText(self, "직접 입력", "휴게시간을 입력하세요")
                            if txt_ok and text:
                                final_break_str = text
                            else:
                                final_break_str = ""

                        if final_break_str:
                            current_txt = self.cb_comment.currentText().strip()
                            add_txt = f"[휴게: {final_break_str}]"
                            if current_txt:
                                self.cb_comment.setCurrentText(f"{current_txt} / {add_txt}")
                            else:
                                self.cb_comment.setCurrentText(add_txt)

                    QtWidgets.QMessageBox.information(self, "적용 완료", f"퇴근시간이 {break_label} 연장되었습니다.")

        self.accept()

    def get_data(self):
        s = self.dte_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        e = None
        if self.mode == "END":
            e = self.dte_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")

        c = self.cb_comment.currentText().strip()
        return s, e, c


