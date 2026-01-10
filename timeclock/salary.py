# timeclock/salary.py
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

# ★ [설정] 5인 미만 사업장 여부 (True: 가산수당 없음, False: 가산수당 1.5배 적용)
IS_UNDER_5_EMPLOYEES = True


class SalaryCalculator:
    def __init__(self, wage_per_hour):
        self.wage = wage_per_hour

    def calculate_period(self, work_logs):
        if not work_logs:
            return None

        # 1. 날짜순 정렬
        logs = sorted(work_logs, key=lambda x: x['approved_start'] or x['start_time'])

        total_work_seconds = 0
        total_break_seconds = 0
        total_actual_seconds = 0

        # 급여 합계
        total_base_pay = 0
        total_overtime_pay = 0
        total_night_pay = 0
        total_holiday_pay = 0
        total_ju_hyu_pay = 0

        # [신규] 설명문 생성을 위한 시간 합계 변수 추가
        sum_overtime_hours = 0.0
        sum_night_hours = 0.0
        sum_holiday_hours = 0.0

        weeks = {}

        # ★ 가산 배율 설정 (5인 미만이면 0.0, 아니면 0.5)
        premium_rate = 0.0 if IS_UNDER_5_EMPLOYEES else 0.5

        for log in logs:
            s_str = log.get('approved_start') or log.get('start_time')
            e_str = log.get('approved_end') or log.get('end_time')
            if not s_str or not e_str: continue

            start_dt = datetime.strptime(s_str, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(e_str, "%Y-%m-%d %H:%M:%S")

            duration = (end_dt - start_dt).total_seconds()
            hours = duration / 3600.0

            # 휴게 공제 (4시간/8시간 룰)
            break_sec = 0
            if hours >= 8:
                break_sec = 3600
            elif hours >= 4:
                break_sec = 1800

            actual_sec = max(0, duration - break_sec)
            actual_hours = actual_sec / 3600.0

            total_work_seconds += duration
            total_break_seconds += break_sec
            total_actual_seconds += actual_sec

            # 1) 기본급 (시급 x 실 근로시간)
            total_base_pay += actual_hours * self.wage

            # 2) 연장 수당 (일 8시간 초과)
            if actual_hours > 8:
                over = actual_hours - 8
                total_overtime_pay += over * self.wage * premium_rate
                sum_overtime_hours += over  # 시간 누적

            # 3) 야간 수당
            n_hours = self._calc_night_hours(start_dt, end_dt)
            total_night_pay += n_hours * self.wage * premium_rate
            sum_night_hours += n_hours  # 시간 누적

            # 주별 집계
            yr, wk, _ = start_dt.isocalendar()
            week_key = (yr, wk)
            if week_key not in weeks:
                weeks[week_key] = {"hours": 0, "days": set()}
            weeks[week_key]["hours"] += actual_hours
            weeks[week_key]["days"].add(start_dt.date())

        # 주단위 계산 (주휴, 주 연장)
        ju_hyu_details = []

        for wk_key, data in weeks.items():
            w_hours = data["hours"]
            w_days = len(data["days"])

            # 주 연장 (주 40시간 초과)
            if w_hours > 40:
                w_over = w_hours - 40
                total_overtime_pay += w_over * self.wage * premium_rate
                sum_overtime_hours += w_over  # 시간 누적

            # 주휴수당 (5인 미만도 지급 의무 있음)
            if w_hours >= 15:
                day_avg = w_hours / w_days if w_days > 0 else 0
                if day_avg > 8: day_avg = 8
                jh_amt = int(day_avg * self.wage)
                total_ju_hyu_pay += jh_amt
                ju_hyu_details.append(jh_amt)

        grand_total = total_base_pay + total_overtime_pay + total_night_pay + total_holiday_pay + total_ju_hyu_pay

        return {
            "start_date": logs[0].get('work_date'),
            "end_date": logs[-1].get('work_date'),
            "total_hours": round(total_work_seconds / 3600.0, 1),  # 총 체류
            "actual_hours": round(total_actual_seconds / 3600.0, 1),  # 실 근로
            "break_hours": round(total_break_seconds / 3600.0, 1),  # 휴게 시간

            # 금액
            "base_pay": int(total_base_pay),
            "overtime_pay": int(total_overtime_pay),
            "night_pay": int(total_night_pay),
            "holiday_pay": int(total_holiday_pay),
            "ju_hyu_pay": int(total_ju_hyu_pay),
            "grand_total": int(grand_total),
            "ju_hyu_details": ju_hyu_details,

            # [신규] 시간 합계 (설명문용)
            "overtime_hours": round(sum_overtime_hours, 1),
            "night_hours": round(sum_night_hours, 1),
            "holiday_hours": round(sum_holiday_hours, 1)
        }

    @staticmethod
    def _calc_night_hours(start_dt, end_dt):
        """22:00 ~ 06:00 사이의 겹치는 시간 계산"""
        night = 0.0
        curr = start_dt
        while curr < end_dt:
            h = curr.hour
            is_night = (h >= 22 or h < 6)
            nxt = curr.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            if nxt <= curr:
                nxt += timedelta(hours=1)

            chunk = min(end_dt, nxt)
            if is_night:
                night += (chunk - curr).total_seconds() / 3600.0
            curr = chunk
        return night

    # ------------------------------------------------------------------
    # ★ [신규] 상세 산출 내역 텍스트 생성기 (사장님 요청 4대 기능 통합)
    # ------------------------------------------------------------------
    def get_friendly_description(self, r):
        """calculate_period의 결과(r)를 받아 친절한 설명 텍스트를 반환"""
        if not r: return ""

        lines = []

        # 1. 휴게시간 공제 명시 (용어 변경: 체류시간 -> 작업시간)
        lines.append("■ 근로시간 상세")
        lines.append(f"   • 총 작업시간({r['total_hours']}h) - 휴게시간({r['break_hours']}h) = 실 근무시간({r['actual_hours']}h)")
        lines.append("")

        # 2. 법적 가산 미적용 안내 (5인 미만일 경우)
        if IS_UNDER_5_EMPLOYEES:
            lines.append("■ 법적 기준 안내")
            lines.append("   • 본 사업장은 상시근로자 5인 미만으로, 근로기준법 제56조에 의거하여")
            lines.append("     연장·야간·휴일 근로에 대한 가산수당(1.5배)이 적용되지 않으며,")
            lines.append("     실제 근로시간에 대한 통상임금(1.0배)이 지급됩니다.")
            lines.append("")

        # 3. 특이 근무 인정 내역 (시간 표시)
        details = []
        if r['overtime_hours'] > 0: details.append(f"연장근로 {r['overtime_hours']}시간")
        if r['night_hours'] > 0: details.append(f"야간근로 {r['night_hours']}시간")
        if r['holiday_hours'] > 0: details.append(f"휴일근로 {r['holiday_hours']}시간")

        if details:
            if IS_UNDER_5_EMPLOYEES:
                lines.append("■ 특이 근무 인정 (1.0배 지급)")
            else:
                lines.append("■ 특이 근무 인정 (1.5배 가산 적용)")
            lines.append("   • " + ", ".join(details))
            lines.append("")

        # 4. 주휴수당 발생 내역 (상세 시간 표기 추가)
        lines.append("■ 주휴수당 발생 내역")
        ju_list = r['ju_hyu_details']
        if len(ju_list) > 0:
            total_ju_pay = sum(ju_list)
            lines.append(f"   • 주 15시간 이상 개근한 {len(ju_list)}개 주에 대해 주휴수당 발생")

            # [추가] 시급 정보를 역산하여 몇 시간분인지 표기
            if self.wage > 0:
                avg_ju_hours = round(total_ju_pay / self.wage, 1)
                lines.append(f"   • 인정 시간: 총 {avg_ju_hours}시간 (시급 {self.wage:,}원 기준)")

            lines.append(f"   • (지급액: {total_ju_pay:,}원)")
        else:
            lines.append("   • 해당 기간 내 주 15시간 이상 만근한 주가 없어 발생하지 않음")

        return "\n".join(lines)

