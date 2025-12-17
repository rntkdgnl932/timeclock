# timeclock/ui/widgets.py


# ui/widgets.py
from PyQt5 import QtWidgets, QtCore

class DateRangeBar(QtWidgets.QWidget):
    applied = QtCore.pyqtSignal()

    def __init__(self, label="조회기간", parent=None):
        super().__init__(parent)
        self.label = QtWidgets.QLabel(label)

        # 라벨이 길 때 줄바꿈/잘림 방지
        self.label.setWordWrap(False)
        self.label.setMinimumWidth(170)  # 필요시 200까지 올려도 됨

        self.de_from = QtWidgets.QDateEdit(QtCore.QDate.currentDate().addMonths(-1))
        self.de_to = QtWidgets.QDateEdit(QtCore.QDate.currentDate())
        for de in (self.de_from, self.de_to):
            de.setCalendarPopup(True)
            de.setDisplayFormat("yyyy-MM-dd")
            de.setMinimumWidth(120)   # ✅ 날짜가 끝까지 보이도록
            de.setFixedHeight(26)

        self.btn_apply = QtWidgets.QPushButton("검색/적용")
        self.btn_apply.setFixedHeight(26)
        self.btn_apply.clicked.connect(self.applied.emit)

        lay = QtWidgets.QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lay.addWidget(self.label)
        lay.addWidget(QtWidgets.QLabel("시작"))
        lay.addWidget(self.de_from)
        lay.addWidget(QtWidgets.QLabel("종료"))
        lay.addWidget(self.de_to)
        lay.addWidget(self.btn_apply)
        lay.addStretch(1)

        self.setLayout(lay)

    def get_range(self):
        d1 = self.de_from.date().toString("yyyy-MM-dd")
        d2 = self.de_to.date().toString("yyyy-MM-dd")
        return d1, d2

    def get_date_from(self):
        return self.de_from.date().toString("yyyy-MM-dd")

    def get_date_to(self):
        return self.de_to.date().toString("yyyy-MM-dd")



class Table(QtWidgets.QTableWidget):
    def __init__(self, headers, parent=None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)

        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        # ✅ UI 가독성 기본값
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.setWordWrap(False)
        # noinspection PyUnresolvedReferences
        self.setTextElideMode(QtCore.Qt.ElideRight)

        # 행 높이 고정 (가독성)
        self.verticalHeader().setDefaultSectionSize(24)
        # ✅ Modern table defaults
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.horizontalHeader().setHighlightSections(False)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)


    def set_rows(self, rows):
        self.setRowCount(0)
        for r in rows:
            row_idx = self.rowCount()
            self.insertRow(row_idx)
            for c, val in enumerate(r):
                it = QtWidgets.QTableWidgetItem("" if val is None else str(val))
                # noinspection PyUnresolvedReferences
                it.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
                self.setItem(row_idx, c, it)

        # ✅ 데이터 넣은 뒤 컬럼 폭 정책 적용
        self.apply_default_column_policy()

    def apply_default_column_policy(self):
        h = self.horizontalHeader()

        # 기본은 “내용에 맞춤”으로 한번 잡고,
        for c in range(self.columnCount()):
            h.setSectionResizeMode(c, QtWidgets.QHeaderView.ResizeToContents)

        # 마지막 컬럼은 늘려서 화면을 채움
        h.setSectionResizeMode(self.columnCount() - 1, QtWidgets.QHeaderView.Stretch)

    def set_column_widths(self, width_map: dict):
        """
        width_map: {col_index: width_px}
        """
        for col, w in width_map.items():
            self.setColumnWidth(int(col), int(w))

    def selected_first_row_index(self) -> int:
        """
        선택된 첫 번째 행 인덱스를 반환. 선택이 없으면 -1.
        """
        idxs = self.selectionModel().selectedRows()
        if not idxs:
            return -1
        return idxs[0].row()

    def get_cell(self, row: int, col: int) -> str:
        """
        (row, col) 셀 텍스트 반환. 비어있으면 "".
        """
        it = self.item(row, col)
        return it.text() if it else ""
