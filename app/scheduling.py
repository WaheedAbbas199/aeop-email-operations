"""Meeting-request parsing, working-hours policy and slot generation. DST-safe: all local times are
built with zoneinfo and converted to UTC for storage/comparison."""
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TZ_ABBR = {"utc": "UTC", "gmt": "UTC", "pkt": "Asia/Karachi", "est": "America/New_York", "edt": "America/New_York",
           "et": "America/New_York", "cst": "America/Chicago", "cdt": "America/Chicago", "pst": "America/Los_Angeles",
           "pdt": "America/Los_Angeles", "pt": "America/Los_Angeles", "cet": "Europe/Paris", "cest": "Europe/Paris",
           "ist": "Asia/Kolkata", "bst": "Europe/London"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = {d: i for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
_MON = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_WD = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
DEFAULT_WH = {"start_hour": 9, "end_hour": 17, "days": [0, 1, 2, 3, 4]}


def get_tz(name: str, default="UTC") -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(default)


def local_to_utc(d: date, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(hour, minute), tzinfo=tz).astimezone(timezone.utc)


def _hm(h, m, mer):
    h = int(h)
    m = int(m or 0)
    if mer:
        mer = mer.lower().replace(".", "")
        if mer == "pm" and h < 12:
            h += 12
        if mer == "am" and h == 12:
            h = 0
    if h > 23 or m > 59:
        return None
    return h, m


_RANGE_SHARED = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(?:-|–|to|until)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_TOK = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|(\d{1,2}):(\d{2})\b"
_RANGE_FULL = re.compile(rf"(?:{_TOK})\s*(?:-|–|to|until)\s*(?:{_TOK})", re.I)
_SINGLE = re.compile(_TOK, re.I)


def _times_in(seg: str):
    """Yield (pos, (h,m), (h2,m2)|None)."""
    out, used = [], []
    for m in _RANGE_SHARED.finditer(seg):
        h1, m1, h2, m2, mer = m.groups()
        end = _hm(h2, m2, mer)
        mer1 = mer
        if end and int(h1) <= 12 and end[0] < 12 and mer.lower() == "pm" and int(h1) > int(h2):
            mer1 = "am"
        start = _hm(h1, m1, mer1)
        if start and end:
            out.append((m.start(), start, end))
            used.append((m.start(), m.end()))
    for m in _SINGLE.finditer(seg):
        if any(a <= m.start() < b for a, b in used):
            continue
        g = m.groups()
        t = _hm(g[0], g[1], g[2]) if g[0] else _hm(g[3], g[4], None)
        if t:
            out.append((m.start(), t, None))
    return sorted(out)


def _dates_in(seg: str, ref: date):
    out = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", seg):
        try:
            out.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            pass
    for m in re.finditer(rf"\b({_MON})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", seg, re.I):
        out.append((m.start(), _mk(ref, MONTHS[m[1].lower()], int(m[2]), m[3])))
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})[a-z]*\b(?:,?\s+(\d{{4}}))?", seg, re.I):
        out.append((m.start(), _mk(ref, MONTHS[m[2].lower()], int(m[1]), m[3])))
    for m in re.finditer(r"\btomorrow\b", seg, re.I):
        out.append((m.start(), ref + timedelta(days=1)))
    for m in re.finditer(rf"\b({_WD})\b", seg, re.I):
        wd = WEEKDAYS[m[1].lower()]
        delta = (wd - ref.weekday()) % 7 or 7
        out.append((m.start(), ref + timedelta(days=delta)))
    return sorted((p, d) for p, d in out if d)


def _mk(ref: date, month: int, day: int, year):
    try:
        y = int(year) if year else ref.year
        d = date(y, month, day)
        if not year and d < ref:
            d = date(y + 1, month, day)
        return d
    except ValueError:
        return None


def parse_meeting_request(text: str, ref_dt: datetime, default_tz: str = "UTC") -> dict:
    raw = re.sub(r"\b([ap])\.m\.", r"\1m", text, flags=re.I)
    tzname, detected = default_tz, False
    m = re.search(r"\b([A-Z][A-Za-z]+/[A-Za-z_]+)\b", raw)
    if m:
        try:
            ZoneInfo(m[1])
            tzname, detected = m[1], True
        except Exception:
            pass
    if not detected:
        m = re.search(r"\b(UTC|GMT|PKT|EST|EDT|ET|CST|CDT|PST|PDT|PT|CET|CEST|IST|BST)\b", raw)
        if m:
            tzname, detected = TZ_ABBR[m[1].lower()], True
    tz = get_tz(tzname)
    dur = 30
    dm = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*)?(minutes?|mins?|hours?|hrs?)\b", raw, re.I)
    if dm:
        v = float(dm[1])
        dur = int(v * 60) if dm[2].lower().startswith(("hour", "hr")) else int(v)
    elif re.search(r"half an hour", raw, re.I):
        dur = 30
    elif re.search(r"\ban hour\b", raw, re.I):
        dur = 60
    ref_local = ref_dt.astimezone(tz).date()
    windows = []
    for seg in re.split(r"[\n.!?;]+", raw):
        dates, times = _dates_in(seg, ref_local), _times_in(seg)
        if not dates or not times:
            continue
        ti = 0
        for i, (dp, d) in enumerate(dates):
            nxt = dates[i + 1][0] if i + 1 < len(dates) else 10 ** 9
            cand = [t for t in times[ti:] if dp <= t[0] < nxt] or ([times[0]] if len(dates) == 1 else [])
            if not cand:
                continue
            _, (h, mi), end = cand[0]
            start = local_to_utc(d, h, mi, tz)
            e = local_to_utc(d, end[0], end[1], tz) if end else start + timedelta(minutes=dur)
            windows.append({"start": start, "end": e})
            ti += 1
    return {"windows": windows, "duration_min": dur, "tz": tzname, "tz_detected": detected}


def within_working_hours(start: datetime, end: datetime, wh: dict, tzname: str) -> bool:
    wh = {**DEFAULT_WH, **(wh or {})}
    tz = get_tz(tzname)
    s, e = start.astimezone(tz), end.astimezone(tz)
    if s.date() != e.date() or s.weekday() not in wh["days"]:
        return False
    return wh["start_hour"] * 60 <= s.hour * 60 + s.minute and e.hour * 60 + e.minute <= wh["end_hour"] * 60


def overlaps(a_start, a_end, busy) -> list:
    return [(bs, be) for bs, be in busy if a_start < be and bs < a_end]


def generate_slots(busy: list, from_utc: datetime, duration_min: int, count: int, wh: dict, tzname: str,
                   days: int = 10, lead_hours: int = 2) -> list:
    wh = {**DEFAULT_WH, **(wh or {})}
    tz = get_tz(tzname)
    earliest = from_utc + timedelta(hours=lead_hours)
    start_day = from_utc.astimezone(tz).date()
    slots = []
    for off in range(days + 1):
        d = start_day + timedelta(days=off)
        if d.weekday() not in wh["days"]:
            continue
        per_day = 0
        minute = wh["start_hour"] * 60
        while minute + duration_min <= wh["end_hour"] * 60 and per_day < 2:
            s = local_to_utc(d, minute // 60, minute % 60, tz)
            e = s + timedelta(minutes=duration_min)
            if s >= earliest and not overlaps(s, e, busy):
                slots.append((s, e))
                per_day += 1
                minute += 120  # spread options inside the day
                if len(slots) >= count:
                    return slots
                continue
            minute += 30
    return slots


def fmt_slot(start: datetime, end: datetime, tzname: str) -> str:
    tz = get_tz(tzname)
    s, e = start.astimezone(tz), end.astimezone(tz)
    return f"{s.strftime('%a %d %b %Y, %H:%M')}–{e.strftime('%H:%M')} {s.tzname()}"
