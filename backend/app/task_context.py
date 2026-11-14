import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Use a valid IANA timezone") from exc
    return value


def dated_request(message: str, created_at: str, timezone: str) -> tuple[str, dict]:
    local = datetime.fromisoformat(created_at).astimezone(ZoneInfo(timezone))
    today = local.date()
    resolved = []
    weekdays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

    def weekday(match):
        delta = (weekdays.index(match[1].lower()) - today.weekday()) % 7 or 7
        date = (today + timedelta(days=delta)).isoformat()
        resolved.append({'expression': match[0], 'date': date})
        return date

    def window(match):
        end = (today + timedelta(days=int(match[1]))).isoformat()
        resolved.append({'expression': match[0], 'start_inclusive': today.isoformat(), 'end_exclusive': end})
        return f'{today.isoformat()} inclusive through {end} exclusive'

    dated = re.sub(r'\bnext (Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b', weekday, message, flags=re.I)
    dated = re.sub(r'\b(?:the )?next (\d{1,3}) days\b', window, dated, flags=re.I)
    context = {'timezone': timezone, 'local_date': today.isoformat(), 'request_time': created_at, 'resolved_dates': resolved}
    return dated + f'\nUser timezone: {timezone}. Reference clock only, not a requested appointment date: {today.isoformat()}.', context
