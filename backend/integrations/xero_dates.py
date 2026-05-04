import re
from datetime import date, datetime, timezone
from typing import Any, Optional

_XERO_MS = re.compile(r"/Date\((-?\d+)([+-]\d+)?\)/")


def parse_xero_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        m = _XERO_MS.match(value.strip())
        if m:
            ms = int(m.group(1))
            return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).date()
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
