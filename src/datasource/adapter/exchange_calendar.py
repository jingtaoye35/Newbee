import exchange_calendars as ecals
import pandas as pd

DEFAULT_CALENDAR = "XSHG"

def fetch_calendar_sessions(start: str, end: str, *, calendar: str = DEFAULT_CALENDAR) -> pd.DataFrame:
    if start > end:
        return pd.DataFrame(columns=["trade_date"], dtype="string")

    cal = ecals.get_calendar(calendar)
    sessions = cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))
    if len(sessions) == 0:
        return pd.DataFrame(columns=["trade_date"], dtype="string")

    return pd.DataFrame(
        {"trade_date": [d.date().isoformat() for d in sessions]},
        dtype="string",
    )