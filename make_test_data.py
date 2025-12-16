import sqlite3
from pathlib import Path

# ★ DB 파일 경로 (/app_data 폴더 안의 timeclock.db)
DB_PATH = Path("./app_data/timeclock.db")


def create_dummy_data():
    if not DB_PATH.exists():
        print(f"오류: DB 파일을 찾을 수 없습니다. 경로를 확인하세요: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # 1. worker 유저 ID 찾기
    cursor.execute("SELECT id FROM users WHERE username='worker'")
    row = cursor.fetchone()
    if not row:
        print("오류: 'worker' 아이디를 가진 유저가 DB에 없습니다.")
        return
    user_id = row[0]

    # 기준 연도 (2025년)
    YEAR = 2025
    MONTH = 12

    # 날짜별 테스트 시나리오
    # (일자, 출근시간, 퇴근시간(없으면 None), 설명)
    scenarios = [
        # 1일: 3시간 근무 (정상)
        (1, "09:00:00", "12:00:00", "12월 1일: 3시간 근무 (휴게 없음)"),

        # 3일: 4시간 10분 근무 (4시간 초과 -> 30분 휴게 알림 대상)
        (3, "13:00:00", "17:10:00", "12월 3일: 4시간 초과 (휴게 30분 체크 대상)"),

        # 5일: 9시간 근무 (8시간 초과 -> 1시간 휴게 알림 대상)
        (5, "09:00:00", "18:00:00", "12월 5일: 9시간 근무 (휴게 1시간 체크 대상)"),

        # 8일: 지각 상황 (코멘트 테스트용)
        (8, "09:30:00", "18:30:00", "12월 8일: 지각 출근 (코멘트 수정 테스트)"),

        # 10일: 조기 퇴근
        (10, "09:00:00", "16:00:00", "12월 10일: 조기 퇴근"),

        # 16일(오늘): 현재 근무 중 (퇴근 버튼 활성화 테스트)
        (16, "14:00:00", None, "12월 16일: 현재 근무 중 (퇴근 처리 테스트)"),
    ]

    print(f"--- 12월 테스트 데이터 생성 시작 ({len(scenarios)}건) ---")

    for day, start_t, end_t, desc in scenarios:
        # 날짜 문자열 생성 (YYYY-MM-DD)
        work_date = f"{YEAR}-{MONTH:02d}-{day:02d}"

        # 출근 datetime 문자열
        s_str = f"{work_date} {start_t}"

        # 퇴근 datetime 문자열 & 상태 결정
        if end_t:
            e_str = f"{work_date} {end_t}"
            status = "PENDING"  # 승인 대기
        else:
            e_str = None
            status = "WORKING"  # 근무 중

        # DB 삽입
        cursor.execute("""
            INSERT INTO work_logs (user_id, work_date, start_time, end_time, status, created_at, owner_comment)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, work_date, s_str, e_str, status, s_str, f"테스트: {desc}"))

        print(f"[{work_date}] 생성됨: {desc}")

    conn.commit()
    conn.close()
    print("------------------------------------------------")
    print("완료! 사업주 화면에서 [새로고침]을 누르고 날짜 범위를 '12월 1일 ~ 31일'로 설정하세요.")


if __name__ == "__main__":
    create_dummy_data()