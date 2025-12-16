# timeclock/salary.py
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta


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

        # ★ [수정] 수당 변수 분리
        total_base_pay = 0  # 기본급
        total_overtime_pay = 0  # 연장수당 (1일 8시간 초과 + 주 40시간 초과)
        total_night_pay = 0  # 야간수당 (22:00~06:00)
        total_holiday_pay = 0  # 휴일수당 (현재는 0원 처리, 추후 휴일 로직 추가 시 사용)
        total_ju_hyu_pay = 0  # 주휴수당

        weeks = {}

        for log in logs:
            s_str = log.get('approved_start') or log.get('start_time')
            e_str = log.get('approved_end') or log.get('end_time')
            if not s_str or not e_str: continue

            start_dt = datetime.strptime(s_str, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(e_str, "%Y-%m-%d %H:%M:%S")

            # A. 시간 계산
            duration = (end_dt - start_dt).total_seconds()
            hours = duration / 3600.0

            # 휴게 공제
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

            # B. 일급 계산
            # 1) 기본급
            total_base_pay += actual_hours * self.wage

            # 2) 1일 연장 (8시간 초과) -> 0.5배 가산
            if actual_hours > 8:
                over = actual_hours - 8
                total_overtime_pay += over * self.wage * 0.5

            # 3) 야간 (22:00~06:00) -> 0.5배 가산
            n_hours = self._calc_night_hours(start_dt, end_dt)
            total_night_pay += n_hours * self.wage * 0.5

            # 4) 휴일 (로직 추가 가능 지점) - 현재는 0원
            # if is_holiday(start_dt): total_holiday_pay += ...

            # 주별 집계
            yr, wk, _ = start_dt.isocalendar()
            week_key = (yr, wk)
            if week_key not in weeks:
                weeks[week_key] = {"hours": 0, "days": set()}
            weeks[week_key]["hours"] += actual_hours
            weeks[week_key]["days"].add(start_dt.date())

        # C. 주단위 계산
        ju_hyu_details = []

        for wk_key, data in weeks.items():
            w_hours = data["hours"]
            w_days = len(data["days"])

            # 1) 주 연장 (40시간 초과)
            if w_hours > 40:
                w_over = w_hours - 40
                total_overtime_pay += w_over * self.wage * 0.5

            # 2) 주휴수당 (15시간 이상)
            if w_hours >= 15:
                # 1일 평균 근로시간(최대 8시간) * 시급
                day_avg = w_hours / w_days if w_days > 0 else 0
                if day_avg > 8: day_avg = 8

                jh_amt = int(day_avg * self.wage)
                total_ju_hyu_pay += jh_amt
                ju_hyu_details.append(jh_amt)

        # 최종 합계
        grand_total = total_base_pay + total_overtime_pay + total_night_pay + total_holiday_pay + total_ju_hyu_pay

        return {
            "start_date": logs[0].get('work_date'),
            "end_date": logs[-1].get('work_date'),
            "total_hours": round(total_work_seconds / 3600.0, 1),
            "actual_hours": round(total_actual_seconds / 3600.0, 1),

            # ★ 분리된 결과값 반환
            "base_pay": int(total_base_pay),
            "overtime_pay": int(total_overtime_pay),
            "night_pay": int(total_night_pay),
            "holiday_pay": int(total_holiday_pay),
            "ju_hyu_pay": int(total_ju_hyu_pay),
            "grand_total": int(grand_total),

            "ju_hyu_details": ju_hyu_details
        }

    def _calc_night_hours(self, start_dt, end_dt):
        """22:00 ~ 06:00 사이의 겹치는 시간 계산"""
        night = 0.0
        curr = start_dt
        while curr < end_dt:
            h = curr.hour
            is_night = (h >= 22 or h < 6)
            nxt = curr.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            if nxt <= curr: nxt += timedelta(hours=1)
            chunk = min(end_dt, nxt)
            if is_night:
                night += (chunk - curr).total_seconds() / 3600.0
            curr = chunk
        return night