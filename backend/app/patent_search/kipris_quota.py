"""Durable request reservations, shared by all PRISM processes (KST months)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ..db import session_scope
from ..models import AppSetting
from .base import PatentSearchError

KEY = 'kipris_quota_state'
LIMIT = 1000
KST = timezone(timedelta(hours=9))


class KiprisQuotaExceeded(PatentSearchError):
    pass


def month_at(now=None):
    return (now or datetime.now(KST)).astimezone(KST).strftime('%Y-%m')


def snapshot(state=None, now=None):
    month = month_at(now)
    months = (state or {}).get('months', {})
    row = months.get(month, {})
    local, external = int(row.get('requests', 0)), int(row.get('external_requests', 0))
    year, mon = map(int, month.split('-'))
    reset = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=KST)
    used = local + external
    return {'month': month, 'requests': local, 'external_requests': external,
            'used': used, 'limit': LIMIT, 'remaining': max(0, LIMIT - used),
            'reset_at': reset.isoformat(), 'blocked': used >= LIMIT,
            'warning': used >= LIMIT * .8,
            'history': [{'month': key, **value} for key, value in sorted(months.items(), reverse=True)[:12]]}


def _change(*, total=None, expected_month=None, now=None):
    # Reserve and commit BEFORE sending: crashes/timeouts cannot erase spent calls.
    # BEGIN IMMEDIATE serializes both threads and separate MCP processes.
    with session_scope() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        setting = session.get(AppSetting, KEY)
        state = dict(setting.value if setting else {})
        current = snapshot(state, now)
        month = current['month']
        if expected_month is not None and expected_month != month:
            raise ValueError('월이 변경되었습니다. 사용량을 새로고침한 후 다시 입력하세요.')
        months = dict(state.get('months', {}))
        row = dict(months.get(month, {'requests': 0, 'external_requests': 0}))
        if total is None:
            if current['blocked']:
                raise KiprisQuotaExceeded('키프리스 월 1,000회 한도에 도달했습니다. 다음 달 1일(KST)에 초기화됩니다.')
            row['requests'] = current['requests'] + 1
        else:
            if type(total) is not int or not current['requests'] <= total <= 1000000:
                raise ValueError('전체 사용 횟수는 PRISM 호출 횟수 이상인 정수여야 합니다.')
            row['external_requests'] = total - current['requests']
        months[month] = row
        state['months'] = months
        if setting is None:
            session.add(AppSetting(key=KEY, value=state))
        else:
            setting.value = state
        session.commit()
        return snapshot(state, now)


def reserve(now=None):
    try:
        return _change(now=now)
    except KiprisQuotaExceeded:
        raise
    except Exception:
        raise PatentSearchError('키프리스 사용량을 저장할 수 없어 API 호출을 중단했습니다.') from None


def reconcile(total, month):
    return _change(total=total, expected_month=month)
