# timeclock/salary.py
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, time


class SalaryCalculator:
    def __init__(self, wage_per_hour):
        self.wage = wage_per_hour

    def calculate_period(self, work_logs):
        """
        기간 내 근무 기록을 분석하여 급여 내역을 계산함
        work_logs: DB에서 가져온 딕셔너리 리스트 [{'work_date':..., 'approved_start':..., 'approved_end':...}, ...]
        """
        if not work_logs:
            return None

        # 1. 날짜순 정렬
        logs = sorted(work_logs, key=lambda x: x['approved_start'] or x['start_time'])

        total_work_seconds = 0
        total_break_seconds = 0
        total_actual_seconds = 0

        # 수당 계산용 변수
        total_base_pay = 0  # 기본급
        total_overtime_pay = 0  # 연장/야간/휴일 가산수당 합계

        # 주휴수당 계산을 위한 주별 데이터
        weeks = {}

        for log in logs:
            # 시작/종료 시간 파싱
            s_str = log.get('approved_start') or log.get('start_time')
            e_str = log.get('approved_end') or log.get('end_time')

            if not s_str or not e_str:
                continue  # 시간 없으면 패스

            start_dt = datetime.strptime(s_str, "%Y-%m-%d %H:%M:%S")
            end_dt = datetime.strptime(e_str, "%Y-%m-%d %H:%M:%S")

            # A. 총 근무시간 (Raw)
            duration = (end_dt - start_dt).total_seconds()
            hours = duration / 3600.0

            # B. 휴게시간 공제 규칙 (4시간 이상 30분, 8시간 이상 1시간)
            break_sec = 0
            if hours >= 8:
                break_sec = 3600  # 1시간
            elif hours >= 4:
                break_sec = 1800  # 30분

            actual_sec = max(0, duration - break_sec)
            actual_hours = actual_sec / 3600.0

            total_work_seconds += duration
            total_break_seconds += break_sec
            total_actual_seconds += actual_sec

            # C. 일급 계산 (기본급 + 가산수당)
            # 1) 기본급 (시급 * 실제근무시간)
            daily_base = actual_hours * self.wage

            # 2) 1일 연장 근로 (8시간 초과분) -> 0.5배 가산
            daily_over_pay = 0
            if actual_hours > 8:
                over_hours = actual_hours - 8
                daily_over_pay += over_hours * self.wage * 0.5

            # 3) 야간 근로 (22:00 ~ 06:00) -> 0.5배 가산
            #    (휴게시간이 야간에 포함되는지는 정확한 휴게 시간을 모르므로,
            #     여기서는 "야간 시간대 근무량" 비율로 계산하거나, 단순 교집합으로 처리)
            night_hours = self._calc_night_hours(start_dt, end_dt)
            # 야간 시간에서도 휴게시간 비율만큼 뺄 수도 있으나, 통상적으로 근로자에게 유리하게
            # 혹은 명확한 휴게시간이 없으면 전체 야간 시간 인정. 여기서는 단순화하여 계산.
            daily_night_pay = night_hours * self.wage * 0.5

            # 4) 휴일 근로 (DB에 is_holiday 플래그가 없으므로 일단 제외하거나 기본값 처리)
            #    필요 시 log['is_holiday'] 확인하여 1.5배/2.0배 로직 추가

            total_base_pay += daily_base
            total_overtime_pay += (daily_over_pay + daily_night_pay)

            # D. 주휴수당용 주별 데이터 집계
            # ISO 달력 기준 (연도, 주차)
            yr, wk, _ = start_dt.isocalendar()
            week_key = (yr, wk)

            if week_key not in weeks:
                weeks[week_key] = {"hours": 0, "days": set()}

            weeks[week_key]["hours"] += actual_hours
            weeks[week_key]["days"].add(start_dt.date())

        # E. 주별 계산 (주 연장근로 & 주휴수당)
        total_ju_hyu_pay = 0
        ju_hyu_details = []

        for wk_key, data in weeks.items():
            w_hours = data["hours"]
            w_days = len(data["days"])

            # 1) 주 연장 근로
            if w_hours > 40:
                w_over = w_hours - 40
                total_overtime_pay += w_over * self.wage * 0.5

            # 2) 주휴수당
            if w_hours >= 15:
                ju_hyu_hours = w_hours / w_days if w_days > 0 else 0
                if ju_hyu_hours > 8: ju_hyu_hours = 8

                # 이번 주의 주휴수당 금액
                this_week_ju_hyu = int(ju_hyu_hours * self.wage)

                total_ju_hyu_pay += this_week_ju_hyu
                ju_hyu_details.append(this_week_ju_hyu)  # ★ 리스트에 추가

            # 최종 합계
        grand_total = total_base_pay + total_overtime_pay + total_ju_hyu_pay

        return {
            "start_date": logs[0].get('work_date'),
            "end_date": logs[-1].get('work_date'),
            "total_hours": round(total_work_seconds / 3600.0, 1),
            "break_hours": round(total_break_seconds / 3600.0, 1),
            "actual_hours": round(total_actual_seconds / 3600.0, 1),
            "base_pay": int(total_base_pay),
            "overtime_pay": int(total_overtime_pay),
            "ju_hyu_pay": int(total_ju_hyu_pay),
            "ju_hyu_details": ju_hyu_details,  # ★ [추가] 상세 내역 전달
            "grand_total": int(grand_total)
        }

    def _calc_night_hours(self, start_dt, end_dt):
        """22:00 ~ 06:00 사이의 겹치는 시간(시간 단위) 계산"""
        night_hours = 0.0

        # 로직 단순화를 위해 1분 단위로 체크하거나, 범위를 쪼개서 계산
        # 여기서는 start ~ end 가 하루를 넘길 수도 있으므로 루프 사용
        current = start_dt
        while current < end_dt:
            # 현재 시간이 야간인지 확인
            h = current.hour
            is_night = (h >= 22 or h < 6)

            # 다음 시간 정각 또는 end_dt까지의 간격
            next_hour = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            if next_hour <= current: next_hour += timedelta(hours=1)

            chunk_end = min(end_dt, next_hour)
            seconds = (chunk_end - current).total_seconds()

            if is_night:
                night_hours += (seconds / 3600.0)

            current = chunk_end

        return night_hours