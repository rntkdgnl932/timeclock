# timeclock/ui/async_helper.py
# -*- coding: utf-8 -*-
from PyQt5 import QtWidgets, QtCore, QtGui


class ProgressDialog(QtWidgets.QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setModal(True)

        # 1. 윈도우 프레임 제거 및 배경 투명 처리
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.Dialog)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        self.resize(450, 350)

        # 2. 메인 레이아웃 및 스타일링
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)  # 그림자 공간 확보

        # 3. 카드 형태의 메인 위젯
        self.container = QtWidgets.QFrame()
        self.container.setObjectName("Container")
        self.container.setStyleSheet("""
            #Container {
                background-color: white;
                border-radius: 15px;
                border: 1px solid #e0e0e0;
            }
        """)

        # 그림자 효과
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QtGui.QColor(0, 0, 0, 40))
        self.container.setGraphicsEffect(shadow)

        # 컨테이너 내부 레이아웃
        vbox = QtWidgets.QVBoxLayout(self.container)
        vbox.setContentsMargins(25, 25, 25, 25)
        vbox.setSpacing(15)

        # [헤더] 타이틀
        self.lbl_title = QtWidgets.QLabel(title)
        self.lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #333;")
        vbox.addWidget(self.lbl_title)

        # [상태] 현재 상태 텍스트
        self.lbl_status = QtWidgets.QLabel("준비 중...")
        self.lbl_status.setStyleSheet("font-size: 14px; color: #666; margin-bottom: 5px;")
        vbox.addWidget(self.lbl_status)

        # [진행바] 커스텀 스타일
        self.pbar = QtWidgets.QProgressBar()
        self.pbar.setFixedHeight(8)
        self.pbar.setTextVisible(False)
        self.pbar.setRange(0, 0)  # 무한 로딩
        self.pbar.setStyleSheet("""
            QProgressBar {
                border: none;
                background-color: #f0f0f0;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 4px;
            }
        """)
        vbox.addWidget(self.pbar)

        # [로그창]
        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("""
            QTextEdit {
                background-color: #fafafa;
                border: 1px solid #eee;
                border-radius: 8px;
                padding: 10px;
                font-family: 'Consolas', 'Malgun Gothic'; 
                font-size: 12px;
                color: #555;
            }
        """)
        vbox.addWidget(self.log_view)

        # [하단 버튼] (에러 났을 때만 보임)
        self.btn_close = QtWidgets.QPushButton("닫기")
        self.btn_close.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_close.setVisible(False)
        self.btn_close.setFixedHeight(40)
        self.btn_close.clicked.connect(self.accept)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #333;
                color: white;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #555;
            }
        """)
        vbox.addWidget(self.btn_close)

        layout.addWidget(self.container)

        self.old_pos = None

    # 창 드래그 이동
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.old_pos = event.globalPos()

    def mouseMoveEvent(self, event):
        if self.old_pos:
            delta = event.globalPos() - self.old_pos
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.old_pos = None

    def append_log(self, msg):
        self.log_view.append(msg)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_done(self, success, err_msg=None):
        self.pbar.setRange(0, 100)
        self.pbar.setValue(100)

        if success:
            # ✅ 성공 시: 초록색 완료 표시 후 0.5초 뒤 자동 닫기
            self.lbl_status.setText("완료되었습니다.")
            self.lbl_status.setStyleSheet("font-size: 14px; color: #4CAF50; font-weight: bold;")
            self.pbar.setStyleSheet("""
                QProgressBar { background-color: #f0f0f0; border-radius: 4px; }
                QProgressBar::chunk { background-color: #4CAF50; border-radius: 4px; }
            """)
            self.append_log("\n✔ 작업 완료. (창이 곧 닫힙니다)")

            # 500ms(0.5초) 뒤에 창을 닫음 (너무 빨리 닫히면 불안해하므로 약간의 텀)
            QtCore.QTimer.singleShot(500, self.accept)

        else:
            # ❌ 실패 시: 닫기 버튼을 활성화하여 사용자가 에러를 확인하게 함
            self.btn_close.setVisible(True)
            self.btn_close.setEnabled(True)

            self.lbl_status.setText("오류 발생")
            self.lbl_status.setStyleSheet("font-size: 14px; color: #E53935; font-weight: bold;")
            self.pbar.setStyleSheet("""
                QProgressBar { background-color: #f0f0f0; border-radius: 4px; }
                QProgressBar::chunk { background-color: #E53935; border-radius: 4px; }
            """)
            self.append_log(f"\n❌ 오류: {err_msg}")


def _mk_progress(owner, title, tail_file=None):
    dlg = ProgressDialog(owner, title)
    dlg.show()

    def on_progress(info: dict):
        msg = info.get("msg", "")
        if msg:
            dlg.lbl_status.setText(msg)
            dlg.append_log(msg)

    def finalize(ok, res, err):
        dlg.set_done(ok, str(err) if err else None)

    return on_progress, finalize, dlg


# 사용자 함수
def run_job_with_progress_async(owner: QtWidgets.QWidget, title: str, job, *, tail_file=None, on_done=None) -> None:
    # 0) 재사용 체크
    reuse_ctx = getattr(owner, "_progress_ctx", None)
    on_progress_ui = finalize_ui = dlg = None
    reused = False

    if reuse_ctx is not None:
        try:
            old_on_progress, old_finalize, old_dlg = reuse_ctx
            if old_dlg is not None and old_dlg.isVisible():
                on_progress_ui, finalize_ui, dlg = old_on_progress, old_finalize, old_dlg
                reused = True
        except Exception:
            pass

    # 1) 없으면 생성
    if dlg is None:
        on_progress_ui, finalize_ui, dlg = _mk_progress(owner, title, tail_file=tail_file)
        setattr(owner, "_progress_ctx", (on_progress_ui, finalize_ui, dlg))
        reused = False

    # 2) 준비 로그
    try:
        on_progress_ui({"stage": "ui", "msg": "작업 시작 준비..."})
    except Exception:
        pass

    class _Worker(QtCore.QObject):
        progress = QtCore.pyqtSignal(dict)
        finished = QtCore.pyqtSignal(object, object)

        @QtCore.pyqtSlot()
        def run(self):
            payload = None
            err = None
            try:
                def on_progress(info: dict):
                    if not isinstance(info, dict):
                        info = {"msg": str(info)}
                    self.progress.emit(info)

                payload = job(on_progress)
            except Exception as ex:
                err = ex
            finally:
                self.finished.emit(payload, err)

    obj = _Worker()
    th = QtCore.QThread(dlg)
    obj.moveToThread(th)

    def _on_progress(info: dict):
        try:
            on_progress_ui(info)
        except Exception:
            pass

    def _on_finished(payload, err):
        ok = (err is None)
        if not reused:
            try:
                finalize_ui(ok, payload, err)
            except Exception:
                pass
        else:
            try:
                on_progress_ui({"stage": "done", "msg": "작업 완료."})
                # 재사용 중에도 완료되면 자동 닫힘 시도 (필요 시)
                if ok:
                    QtCore.QTimer.singleShot(500, dlg.accept)
            except Exception:
                pass

        if callable(on_done):
            try:
                on_done(ok, payload, err)
            except Exception:
                pass

        try:
            th.quit()
            th.wait(100)
        except Exception:
            pass

        try:
            jobss = getattr(owner, "_progress_jobs", [])
            if th in jobss:
                jobss.remove(th)
            setattr(owner, "_progress_jobs", jobss)
        except Exception:
            pass

    obj.progress.connect(_on_progress)
    obj.finished.connect(_on_finished)
    th.started.connect(obj.run)

    try:
        jobs = getattr(owner, "_progress_jobs", None)
        if not isinstance(jobs, list):
            jobs = []
        jobs.append(th)
        setattr(owner, "_progress_jobs", jobs)
        setattr(th, "_worker_ref", obj)
    except Exception:
        pass

    th.start()