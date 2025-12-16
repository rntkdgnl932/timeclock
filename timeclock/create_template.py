# create_template.py
# -*- coding: utf-8 -*-
import openpyxl
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
import os

# 1. 저장 경로 설정
base_dir = r"C:\my_games\timeclock\app_data"
if not os.path.exists(base_dir):
    os.makedirs(base_dir)

save_path = os.path.join(base_dir, "template.xlsx")

# 2. 새 엑셀 파일 생성
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "급여명세서"

# ==========================================
# [스타일 정의] 색상 및 테두리 설정
# ==========================================
# 색상 (Hex Code)
COLOR_TITLE_BG = "E3F2FD"  # 연한 파랑 (제목)
COLOR_HEADER_BG = "DCE6F1"  # 회하늘색 (항목 헤더)
COLOR_LABEL_BG = "F5F5F5"  # 연한 회색 (일반 라벨)
COLOR_TOTAL_BG = "FFF9C4"  # 연한 노랑 (실수령액 강조)

# 테두리 스타일
border_thin = Side(style='thin', color='000000')
border_thick = Side(style='medium', color='000000')

box_border = Border(left=border_thin, right=border_thin, top=border_thin, bottom=border_thin)
bottom_thick_border = Border(bottom=border_thick)

# 폰트
font_title = Font(name='맑은 고딕', size=20, bold=True)
font_header = Font(name='맑은 고딕', size=11, bold=True)
font_bold = Font(name='맑은 고딕', size=10, bold=True)
font_normal = Font(name='맑은 고딕', size=10)

# 정렬
align_center = Alignment(horizontal='center', vertical='center')
align_right = Alignment(horizontal='right', vertical='center')
align_left = Alignment(horizontal='left', vertical='center', wrap_text=True)

# 채우기(배경색)
fill_title = PatternFill(start_color=COLOR_TITLE_BG, end_color=COLOR_TITLE_BG, fill_type='solid')
fill_header = PatternFill(start_color=COLOR_HEADER_BG, end_color=COLOR_HEADER_BG, fill_type='solid')
fill_label = PatternFill(start_color=COLOR_LABEL_BG, end_color=COLOR_LABEL_BG, fill_type='solid')
fill_total = PatternFill(start_color=COLOR_TOTAL_BG, end_color=COLOR_TOTAL_BG, fill_type='solid')

# ==========================================
# [데이터 배치]
# ==========================================
# (행, 열, 값, 스타일옵션)
data = [
    # 1. 제목 (1행)
    (1, 1, "급여명세서", "TITLE"),

    # 2. 기본 정보 (3~4행)
    (3, 1, "지급일", "LABEL"), (3, 2, "{{pay_date}}", "CENTER"),
    (3, 4, "성함", "LABEL"), (3, 5, "{{name}}", "CENTER"),
    (4, 1, "정산기간", "LABEL"), (4, 2, "{{period}}", "CENTER"),
    (4, 4, "직급", "LABEL"), (4, 5, "사원", "CENTER"),

    # 3. 상세 내역 헤더 (6행)
    (6, 1, "지급 내역", "HEADER"), (6, 3, "금액", "HEADER"),
    (6, 4, "공제 내역", "HEADER"), (6, 6, "금액", "HEADER"),

    # 4. 상세 항목 (7~12행) - 왼쪽(지급) / 오른쪽(공제)
    (7, 1, "기본급", "TEXT"), (7, 3, "{{base_pay}}", "MONEY"),
    (7, 4, "국민연금", "TEXT"), (7, 6, "{{pension}}", "MONEY"),

    (8, 1, "주휴수당", "TEXT"), (8, 3, "{{ju_hyu_pay}}", "MONEY"),
    (8, 4, "건강보험", "TEXT"), (8, 6, "{{health_ins}}", "MONEY"),

    (9, 1, "연장수당", "TEXT"), (9, 3, "{{overtime_pay}}", "MONEY"),
    (9, 4, "장기요양", "TEXT"), (9, 6, "{{care_ins}}", "MONEY"),

    (10, 1, "야간수당", "TEXT"), (10, 3, "{{night_pay}}", "MONEY"),
    (10, 4, "고용보험", "TEXT"), (10, 6, "{{ei_ins}}", "MONEY"),

    (11, 1, "휴일수당", "TEXT"), (11, 3, "{{holiday_pay}}", "MONEY"),
    (11, 4, "소득세", "TEXT"), (11, 6, "{{income_tax}}", "MONEY"),

    (12, 1, "기타수당", "TEXT"), (12, 3, "{{other_pay}}", "MONEY"),
    (12, 4, "지방소득세", "TEXT"), (12, 6, "{{local_tax}}", "MONEY"),

    # 5. 합계 라인 (14~16행)
    (14, 4, "지급 합계", "BOLD_LABEL"), (14, 6, "{{total_pay}}", "BOLD_MONEY"),
    (15, 4, "공제 합계", "BOLD_LABEL"), (15, 6, "{{total_deduction}}", "BOLD_MONEY"),
    (16, 4, "실수령액", "TOTAL_HIGHLIGHT"), (16, 6, "{{net_pay}}", "TOTAL_HIGHLIGHT_MONEY"),

    # 6. 하단 텍스트 (상세/비고)
    (18, 1, "상세 산출 내역", "SUB_TITLE"),
    (19, 1, "{{calc_detail}}", "LEFT_TEXT"),
    (20, 1, "{{base_detail}}", "LEFT_TEXT"),
    (21, 1, "{{over_detail}}", "LEFT_TEXT"),
    (22, 1, "{{ju_hyu_detail}}", "LEFT_TEXT"),

    (24, 1, "비고 (안내)", "SUB_TITLE"),
    (25, 1, "{{note}}", "BOX_TEXT"),

    (30, 1, "귀하의 노고에 진심으로 감사드립니다.", "FOOTER"),
    (32, 1, "회사명: {{company}}", "FOOTER"),
]

# ==========================================
# [데이터 입력 및 스타일 적용 루프]
# ==========================================
for item in data:
    row, col, val, style_type = item
    cell = ws.cell(row=row, column=col)
    cell.value = val

    # 공통: 모든 셀에 기본 테두리 적용 (나중에 외곽선 정리)
    if style_type not in ["TITLE", "FOOTER"]:
        cell.border = box_border

    # 스타일 분기 처리
    if style_type == "TITLE":
        cell.font = font_title
        cell.alignment = align_center
        # 병합
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        # 배경색 (선택사항)
        # cell.fill = fill_title

    elif style_type == "LABEL":
        cell.font = font_bold
        cell.alignment = align_center
        cell.fill = fill_label

    elif style_type == "CENTER":
        cell.font = font_normal
        cell.alignment = align_center
        # 병합 (값 들어갈 공간 확보)
        if col == 2:  # 지급일, 정산기간 옆
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 1)
        if col == 5:  # 성함, 직급 옆
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 1)

    elif style_type == "HEADER":
        cell.font = font_header
        cell.alignment = align_center
        cell.fill = fill_header
        # 헤더 병합 (지급내역/공제내역 넓게)
        if col == 1 or col == 4:
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 1)

    elif style_type == "TEXT":
        cell.font = font_normal
        cell.alignment = align_center
        # 라벨 병합
        if col == 1 or col == 4:
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=col + 1)

    elif style_type == "MONEY":
        cell.font = font_normal
        cell.alignment = align_right

    elif style_type == "BOLD_LABEL":
        cell.font = font_bold
        cell.alignment = align_center
        cell.fill = fill_label
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=5)

    elif style_type == "BOLD_MONEY":
        cell.font = font_bold
        cell.alignment = align_right

    elif style_type == "TOTAL_HIGHLIGHT":
        cell.font = Font(name='맑은 고딕', size=12, bold=True, color='FF0000')  # 빨간 글씨
        cell.alignment = align_center
        cell.fill = fill_total
        cell.border = Border(top=border_thick, bottom=border_thick, left=border_thin, right=border_thin)
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=5)

    elif style_type == "TOTAL_HIGHLIGHT_MONEY":
        cell.font = Font(name='맑은 고딕', size=12, bold=True)
        cell.alignment = align_right
        cell.fill = fill_total
        cell.border = Border(top=border_thick, bottom=border_thick, left=border_thin, right=border_thin)

    elif style_type == "SUB_TITLE":
        cell.font = font_bold
        cell.alignment = Alignment(horizontal='left', vertical='bottom')
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        # 밑줄 느낌
        cell.border = Border(bottom=border_thick)

    elif style_type == "LEFT_TEXT":
        cell.font = font_normal
        cell.alignment = align_left
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)

    elif style_type == "BOX_TEXT":
        cell.font = font_normal
        cell.alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
        # 비고란 박스 크게 병합 (4줄 정도)
        ws.merge_cells(start_row=row, start_column=1, end_row=row + 3, end_column=6)
        # 전체 테두리
        for r in range(row, row + 4):
            for c in range(1, 7):
                ws.cell(r, c).border = box_border

    elif style_type == "FOOTER":
        cell.font = font_bold
        cell.alignment = align_center
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)

# ==========================================
# [열 너비 조정]
# ==========================================
ws.column_dimensions['A'].width = 12
ws.column_dimensions['B'].width = 12
ws.column_dimensions['C'].width = 18  # 금액란 넓게
ws.column_dimensions['D'].width = 12
ws.column_dimensions['E'].width = 12
ws.column_dimensions['F'].width = 18  # 금액란 넓게

# ==========================================
# [저장]
# ==========================================
wb.save(save_path)
print(f"✨ [완료] 디자인이 적용된 엑셀 템플릿 생성 완료!\n   경로: {save_path}")