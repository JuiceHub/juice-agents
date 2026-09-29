"""Minimal five-field cron parser and next-run calculator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class CronFields:
    """Expanded numeric cron fields."""

    minute: tuple[int, ...]
    hour: tuple[int, ...]
    day_of_month: tuple[int, ...]
    month: tuple[int, ...]
    day_of_week: tuple[int, ...]


_RANGES: tuple[tuple[int, int], ...] = (
    (0, 59),
    (0, 23),
    (1, 31),
    (1, 12),
    (0, 6),
)


def _expand_field(raw: str, *, minimum: int, maximum: int, day_of_week: bool = False) -> tuple[int, ...] | None:
    """Expand one cron field into a sorted tuple, returning None for invalid syntax."""

    values: set[int] = set()
    for part in str(raw or "").split(","):
        if not part:
            return None
        step = 1
        base = part
        if "/" in part:
            base, raw_step = part.split("/", 1)
            if not raw_step.isdigit():
                return None
            step = int(raw_step)
            if step < 1:
                return None

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            raw_start, raw_end = base.split("-", 1)
            if not raw_start.isdigit() or not raw_end.isdigit():
                return None
            start = int(raw_start)
            end = int(raw_end)
            effective_max = 7 if day_of_week else maximum
            if start > end or start < minimum or end > effective_max:
                return None
        elif base.isdigit() and "/" not in part:
            value = int(base)
            if day_of_week and value == 7:
                value = 0
            if value < minimum or value > maximum:
                return None
            values.add(value)
            continue
        else:
            return None

        effective_max = 7 if day_of_week else maximum
        if start < minimum or end > effective_max:
            return None
        for value in range(start, end + 1, step):
            values.add(0 if day_of_week and value == 7 else value)

    if not values:
        return None
    return tuple(sorted(values))


def parse_cron_expression(expr: str) -> CronFields | None:
    """Parse the supported standard five-field cron subset."""

    parts = str(expr or "").strip().split()
    if len(parts) != 5:
        return None
    expanded: list[tuple[int, ...]] = []
    for index, part in enumerate(parts):
        minimum, maximum = _RANGES[index]
        values = _expand_field(part, minimum=minimum, maximum=maximum, day_of_week=index == 4)
        if values is None:
            return None
        expanded.append(values)
    return CronFields(
        minute=expanded[0],
        hour=expanded[1],
        day_of_month=expanded[2],
        month=expanded[3],
        day_of_week=expanded[4],
    )


def compute_next_cron_run(fields: CronFields, from_dt: datetime) -> datetime | None:
    """
    Return the next local datetime strictly after ``from_dt``.

    The day-of-month/day-of-week rule follows vixie cron: when both are
    constrained, either one may match.
    """

    minute_set = set(fields.minute)
    hour_set = set(fields.hour)
    dom_set = set(fields.day_of_month)
    month_set = set(fields.month)
    dow_set = set(fields.day_of_week)
    dom_wild = len(fields.day_of_month) == 31
    dow_wild = len(fields.day_of_week) == 7

    candidate = from_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if candidate.month not in month_set:
            candidate = (candidate.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue

        cron_dow = (candidate.weekday() + 1) % 7
        if dom_wild and dow_wild:
            day_matches = True
        elif dom_wild:
            day_matches = cron_dow in dow_set
        elif dow_wild:
            day_matches = candidate.day in dom_set
        else:
            day_matches = candidate.day in dom_set or cron_dow in dow_set
        if not day_matches:
            candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
            continue

        if candidate.hour not in hour_set:
            candidate = (candidate + timedelta(hours=1)).replace(minute=0)
            continue

        if candidate.minute not in minute_set:
            candidate = candidate + timedelta(minutes=1)
            continue

        return candidate
    return None


def next_cron_run_timestamp(expr: str, from_timestamp: float) -> float | None:
    """Return the next run timestamp for a cron expression."""

    fields = parse_cron_expression(expr)
    if fields is None:
        return None
    next_run = compute_next_cron_run(fields, datetime.fromtimestamp(float(from_timestamp)))
    return None if next_run is None else next_run.timestamp()
