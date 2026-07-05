"""Pot math. Pure functions, all date logic in the household timezone.

The pot stays silent (None) unless setup is complete AND the first payday
after setup has arrived — never a wrong or uncertain number.
"""
import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .config import DEFAULT_TIMEZONE
from .db import parse_ts, setup_complete


def fmt_money(amount: int, symbol: str = "Kč", code: str = "CZK") -> str:
    if code == "CZK":
        return f"{amount:,}".replace(",", " ") + f" {symbol}"
    sign = "-" if amount < 0 else ""
    return f"{sign}{symbol}{abs(amount):,}"


def _tz(settings: dict) -> ZoneInfo:
    try:
        return ZoneInfo(settings.get("timezone") or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def household_today(settings: dict) -> date:
    return datetime.now(_tz(settings)).date()


def clamp_payday(year: int, month: int, day: int) -> date:
    """Payday 31 in February → Feb 28/29."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(max(1, day), last))


def _next_occurrence(payday: int, after: date) -> date:
    """First occurrence of a day-of-month payday strictly after `after`."""
    candidate = clamp_payday(after.year, after.month, payday)
    if candidate > after:
        return candidate
    y, m = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
    return clamp_payday(y, m, payday)


def next_payday(settings: dict, *, today: date | None = None) -> date | None:
    """Nearest upcoming payday of either partner, in household time."""
    if not settings:
        return None
    today = today or household_today(settings)
    days = [d for d in (settings.get("p1_payday"), settings.get("p2_payday")) if d]
    if not days:
        return None
    return min(_next_occurrence(d, today) for d in days)


def first_pot_date(settings: dict) -> date | None:
    """The pot activates at the first payday after setup was completed —
    before money has actually landed, we don't claim it's spendable."""
    if not setup_complete(settings):
        return None
    completed = parse_ts(settings.get("setup_completed_at"))
    if completed is None:
        return None
    completed_local = completed.astimezone(_tz(settings)).date()
    days = [d for d in (settings.get("p1_payday"), settings.get("p2_payday")) if d]
    if not days:
        return None
    return min(_next_occurrence(d, completed_local) for d in days)


def pot_active(settings: dict, *, today: date | None = None) -> bool:
    first = first_pot_date(settings)
    if first is None:
        return False
    today = today or household_today(settings)
    return today >= first


def pot_value(settings: dict, done_dreams: list[dict], *, today: date | None = None) -> int | None:
    """pot = combined income − everyday baseline − prices of dreams done this
    calendar month (household tz). None = stay quiet."""
    if not setup_complete(settings):
        return None
    if not pot_active(settings, today=today):
        return None
    tz = _tz(settings)
    today = today or household_today(settings)
    drained = 0
    for d in done_dreams:
        ts = parse_ts(d.get("done_at"))
        if ts is None:
            continue
        local = ts.astimezone(tz).date()
        if (local.year, local.month) == (today.year, today.month):
            drained += d.get("done_price") or 0
    return settings["p1_income"] + settings["p2_income"] - settings["baseline"] - drained


def days_until(target: date | None, settings: dict, *, today: date | None = None) -> int | None:
    if target is None:
        return None
    today = today or household_today(settings)
    return (target - today).days
