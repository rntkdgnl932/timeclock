# timeclock/excel_maker.py
# -*- coding: utf-8 -*-
import openpyxl
import shutil
import re


def generate_payslip(template_path, save_path, data_context):
    print("\n" + "=" * 60)
    print(f"[🔍 엑셀 생성 및 진단 시작]")
    print(f"1. 템플릿 파일: {template_path}")
    print(f"2. 저장할 경로: {save_path}")

    # 1. 템플릿 복사
    try:
        shutil.copy(template_path, save_path)
    except Exception as e:
        print(f"[❌ 오류] 템플릿 복사 실패! 파일이 없거나 사용 중입니다.\n내용: {e}")
        return None  # [수정] 명시적으로 None 반환

    # 2. 엑셀 로드
    try:
        wb = openpyxl.load_workbook(save_path, data_only=False)
        ws = wb.active
        print(f"3. 엑셀 로드 성공! (전체 시트 목록: {wb.sheetnames})")
        print(f"   👉 현재 작업 중인 시트: '{ws.title}'")
    except Exception as e:
        print(f"[❌ 오류] 엑셀 열기 실패! DRM이 걸려있거나 손상된 파일입니다.\n내용: {e}")
        return None  # [수정] 명시적으로 None 반환

    # 3. 셀 내용 미리보기 (진단용 - 첫 5줄만)
    print("-" * 60)
    print("[👀 시트 내용 미리보기 (데이터 있는 행만)]")
    row_limit = 5
    for i, row in enumerate(ws.iter_rows(max_row=row_limit)):
        vals = [str(c.value).strip() if c.value else "" for c in row]
        if any(vals):  # 내용이 있는 줄만 출력
            print(f"   행 {i + 1}: {vals}")
    print("-" * 60)

    # 4. 치환 작업 (Regex 적용)
    replaced_count = 0
    print("[🛠️ 치환 작업 시작]")

    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                text = str(cell.value)

                # 디버깅: {{ }} 가 들어있는 셀이 보이면 일단 출력
                if "{{" in text:
                    print(f"   📍 변수 패턴 발견 (위치 {cell.coordinate}): '{text}'")

                for key, val in data_context.items():
                    # 패턴: {{ key }} (공백 무시, 대소문자 구분 없음 등 유연하게 처리 가능하지만 여기선 키 정확도 우선)
                    # 정규식: \{\{\s*KEY\s*\}\}
                    pattern = r"\{\{\s*" + re.escape(key) + r"\s*\}\}"

                    if re.search(pattern, text):
                        print(f"      ✅ 매칭 성공! '{{{{{key}}}}}' -> '{val}'")

                        # 셀 내용이 정확히 변수 하나만 있으면 -> 값 자체로 교체 (숫자 형식 유지)
                        if re.fullmatch(pattern, text.strip()):
                            cell.value = val
                            # 문장 속에 섞여 있으면 -> 문자열 치환
                        else:
                            cell.value = re.sub(pattern, str(val), text)

                        replaced_count += 1
                        text = str(cell.value)  # 갱신된 텍스트로 업데이트

    # 5. 저장
    try:
        wb.save(save_path)
        wb.close()
        print(f"[💾 저장 완료]")
    except Exception as e:
        print(f"[❌ 오류] 저장 실패! 엑셀 파일을 켜두셨나요?\n내용: {e}")
        return None  # [수정] 명시적으로 None 반환

    print("-" * 60)
    if replaced_count == 0:
        print("🚨 [결과: 실패] 바뀐 항목이 0개입니다!")
        print("   1) 위 '시트 내용 미리보기'에 {{name}} 같은 글자가 보이나요?")
        print("   2) 안 보인다면 템플릿 파일이 비어있거나, 엉뚱한 시트입니다.")
    else:
        print(f"🎉 [결과: 성공] 총 {replaced_count}개의 항목을 채워 넣었습니다!")
    print("=" * 60 + "\n")

    return str(save_path)