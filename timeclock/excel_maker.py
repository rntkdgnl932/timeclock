# timeclock/excel_maker.py
# -*- coding: utf-8 -*-
import openpyxl
import shutil
from pathlib import Path


def generate_payslip(template_path, save_path, data_context):
    print("=" * 50)
    print(f"[디버그] 엑셀 생성 시작")
    print(f"-> 템플릿: {template_path}")
    print(f"-> 저장경로: {save_path}")
    print(f"-> 넣을 데이터 키 목록: {list(data_context.keys())}")
    print("-" * 50)

    # 1. 템플릿 복사
    try:
        shutil.copy(template_path, save_path)
    except Exception as e:
        print(f"[오류] 파일 복사 실패: {e}")
        return

    # 2. 엑셀 파일 열기
    try:
        wb = openpyxl.load_workbook(save_path)
        ws = wb.active
    except Exception as e:
        print(f"[오류] 엑셀 로드 실패: {e}")
        return

    # 3. 치환 작업
    replaced_count = 0

    for row in ws.iter_rows():
        for cell in row:
            # 값이 있는 문자열 셀만 검사
            if cell.value and isinstance(cell.value, str):
                text = cell.value.strip()  # 공백 제거 후 확인

                # 엑셀에 {{ }} 가 있는지 확인 (있으면 무조건 로그 출력)
                if "{{" in text and "}}" in text:
                    print(f"[발견] 엑셀 셀({cell.coordinate}) 내용: '{text}'")

                for key, val in data_context.items():
                    target = "{{" + key + "}}"

                    if target in text:
                        print(f"   >>> [매칭 성공!] '{target}' 을 '{val}' 로 바꿉니다.")

                        if text == target:
                            cell.value = val  # 값 자체 교체 (숫자 등)
                        else:
                            cell.value = text.replace(target, str(val))  # 문자열 치환

                        replaced_count += 1
                        # 값이 바뀌었으니 text 갱신
                        if isinstance(cell.value, str):
                            text = cell.value

    print("-" * 50)
    print(f"[결과] 총 {replaced_count}개의 항목을 바꿨습니다.")

    if replaced_count == 0:
        print("!!! 주의: 바뀐 항목이 0개입니다. 엑셀의 {{변수}} 모양을 확인하세요.")
        print("    (팁: 띄어쓰기가 있거나, 오타가 있을 수 있습니다.)")

    print("=" * 50)

    # 4. 저장
    wb.save(save_path)
    wb.close()
    return str(save_path)