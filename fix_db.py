import sys
import os
import sqlite3

# 현재 폴더 경로 설정
sys.path.append(os.getcwd())

# 설정 파일 경로
DB_PATH = r"C:\my_games\timeclock\app_data\timeclock.db"


def run_final_fix():
    print("=" * 60)
    print("🔥 [최종 복구] DB 수리 + 구글 드라이브 강제 덮어쓰기")
    print("=" * 60)

    # 1. 로컬 DB 수리 (혹시 덮어쓰기 당해서 다시 망가졌을까봐 다시 함)
    print(f"🛠️ 1단계: 내 컴퓨터 파일 수리 중... ({DB_PATH})")

    if not os.path.exists(DB_PATH):
        print("❌ 파일이 없습니다. 경로를 확인하세요.")
        input("엔터 종료...")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # 컬럼 확인
        cur.execute("PRAGMA table_info(disputes)")
        cols = [r[1] for r in cur.fetchall()]

        if "comment" in cols:
            print("   ✅ 내 컴퓨터 파일은 현재 정상입니다.")
        else:
            print("   ⚡ 컬럼이 없어서 다시 뚫습니다...")
            cur.execute("ALTER TABLE disputes ADD COLUMN comment TEXT")
            conn.commit()
            print("   🎉 수리 완료.")
        conn.close()
    except Exception as e:
        print(f"   ❌ 수리 실패 (파일이 잠겼을 수 있음): {e}")
        print("   -> 프로그램이 켜져 있다면 끄고 다시 실행하세요.")
        input("엔터 종료...")
        return

    # 2. 구글 드라이브 강제 업로드 (이게 핵심)
    print("\n☁️ 2단계: 수리된 파일을 구글 드라이브에 강제 업로드 중...")
    print("   (이걸 해야 옛날 파일이 다운로드되는 걸 막습니다)")

    try:
        from timeclock import sync_manager

        # 강제 업로드
        success = sync_manager.upload_current_db()

        if success:
            print("\n🎉 [대성공] 구글 드라이브 파일까지 완벽하게 교체했습니다.")
            print("이제 프로그램을 켜도 안전합니다.")
        else:
            print("\n❌ [실패] 업로드 실패. 인터넷 연결을 확인해주세요.")

    except ImportError:
        print("\n❌ timeclock 모듈을 찾을 수 없습니다. 파일 위치를 확인해주세요.")
    except Exception as e:
        print(f"\n❌ 업로드 중 오류: {e}")

    print("=" * 60)
    input("엔터를 누르면 종료합니다...")


if __name__ == "__main__":
    run_final_fix()