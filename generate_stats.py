#!/usr/bin/env python3
"""Verify data.yaml and rebuild README.md plus the per-year pages.

Every day is worth a fixed number of hours: a plain workday or a
holiday/weekend day. A month's hours are always counted over the full calendar
month and split equally between the three codes; each code's balance is its own
hours minus that equal share, accumulated from the start of the year on top of
the year's initial state. A year without an explicit initial state continues
from the previous year's closing balance. Balances are rounded to whole hours
only when displayed.

Months that data.yaml does not cover in full are marked with '*': their hours
are those of the whole month, but only the covered days are credited to a code,
so the three balances do not cancel out until the month is filled in.

README.md shows the current month (heat map and change list), the current
year's cumulative table and a menu; years/<year>.md holds each year's detailed
monthly breakdown and full heat map. Heat maps read oldest first.

Run without arguments for a dry run (report plus a diff); pass --write to
update the files. Exits non-zero when validation fails.
"""

from __future__ import annotations

import argparse
import calendar
import difflib
import sys
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Iterator

import yaml

CODES: tuple[str, ...] = ("M", "P", "D")
DEFAULT_CELLS: dict[str, str] = {"M": "🟦", "P": "🟨", "D": "🟧"}
EMPTY_CELL = "⬜"
# A weekend or holiday is drawn as a heart in the same colour as the code's
# square, because GitHub's sanitiser strips 'style' and 'bgcolor' and leaves
# the glyph as the only way to shade a cell.
HEART_CELLS: dict[str, str] = {
    "🟥": "❤️", "🟧": "🧡", "🟨": "💛", "🟩": "💚",
    "🟦": "💙", "🟪": "💜", "🟫": "🤎", "⬛": "🖤", "⬜": "🤍",
}
# Combining marks that render inside the preceding glyph and take no width.
ZERO_WIDTH = frozenset({0x200D, 0xFE0E, 0xFE0F})
HEATMAP_MARKERS = ("<!-- heatmap:begin -->", "<!-- heatmap:end -->")
CHANGES_MARKERS = ("<!-- changes:begin -->", "<!-- changes:end -->")
STATS_MARKERS = ("<!-- stats:begin -->", "<!-- stats:end -->")
NAV_MARKERS = ("<!-- nav:begin -->", "<!-- nav:end -->")
ARCHIVE_DIR = "years"
INCOMPLETE_MARK = "*"
WEEKDAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
DETAIL_HEADER = [
    "month", "days", "uncov", "hours", "share",
    "M h", "P h", "D h", "cum M", "cum P", "cum D",
]


# --------------------------------------------------------------------------
# data.yaml
# --------------------------------------------------------------------------

def load_data(path: Path) -> dict:
    """Read the YAML source of truth."""
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def code_cells(data: dict) -> dict[str, str]:
    """Coloured square per code, overridable through the 'codes:' key."""
    configured = data.get("codes") or {}
    return {code: str(configured.get(code, DEFAULT_CELLS[code])) for code in CODES}


def code_labels(cells: dict[str, str]) -> dict[str, str]:
    """Row labels for the Markdown tables, e.g. '🟦M'."""
    return {code: f"{cells[code]}{code}" for code in CODES}


def heart_cells(cells: dict[str, str]) -> dict[str, str]:
    """Weekend/holiday variant of each code's square, falling back to itself."""
    return {code: HEART_CELLS.get(cell, cell) for code, cell in cells.items()}


def daterange(start: date, end: date) -> Iterator[date]:
    """Yield every day from start to end, both inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def year_configs(data: dict) -> dict[int, dict]:
    """Map each configured year to its config block."""
    configs: dict[int, dict] = {}
    for entry in data.get("years") or []:
        year = date.fromisoformat(entry["start_date"]).year
        configs[year] = entry
    return configs


def year_holidays(configs: dict[int, dict]) -> dict[int, frozenset[date]]:
    """Map each configured year to its holiday dates."""
    return {
        year: frozenset(
            date.fromisoformat(str(day)) for day in (config.get("holidays") or [])
        )
        for year, config in configs.items()
    }


def build_segments(data: dict) -> dict[date, tuple[str, date, date]]:
    """Map every covered day to (code, segment start, segment end)."""
    segments: dict[date, tuple[str, date, date]] = {}
    for event in data["events"]:
        start = date.fromisoformat(event["start_date"])
        end = date.fromisoformat(event["end_date"])
        for day in daterange(start, end):
            segments[day] = (event["summary"], start, end)
    return segments


def validate(data: dict) -> list[str]:
    """Return a list of problems found in data.yaml; empty means it is sound."""
    problems: list[str] = []
    events = data["events"]
    configs = year_configs(data)

    previous_end: date | None = None
    previous_event: dict | None = None
    for event in events:
        start = date.fromisoformat(event["start_date"])
        end = date.fromisoformat(event["end_date"])
        code = event["summary"]
        if code not in CODES:
            problems.append(f"{start}: unknown code {code!r} (expected one of {', '.join(CODES)})")
        if end < start:
            problems.append(f"{start}: end_date {end} is before start_date")
        if previous_end is not None:
            gap = (start - previous_end).days
            if gap <= 0:
                problems.append(f"{start}: overlaps previous event ending {previous_end}")
            elif gap > 1:
                problems.append(f"{previous_end} -> {start}: gap of {gap - 1} uncovered day(s)")
        if previous_event is not None and code == previous_event["summary"]:
            problems.append(
                f"{start}: same code {code!r} as the previous event "
                f"({previous_event['start_date']}) - ranges should be merged"
            )
        previous_end = end
        previous_event = event

    covered_years = {date.fromisoformat(e["start_date"]).year for e in events}
    covered_years |= {date.fromisoformat(e["end_date"]).year for e in events}
    for year in sorted(covered_years):
        config = configs.get(year)
        if config is None:
            problems.append(f"{year}: no entry under 'years:' (initial state, hours, holidays)")
            continue
        if "initial" in config and set(config["initial"]) != set(CODES):
            problems.append(f"{year}: 'initial' must list exactly {', '.join(CODES)}")
        if set(config.get("hours", {})) != {"workday", "holiday"}:
            problems.append(f"{year}: 'hours' must define 'workday' and 'holiday'")
        seen: set[date] = set()
        for raw in config.get("holidays") or []:
            day = date.fromisoformat(str(raw))
            if day.year != year:
                problems.append(f"{year}: holiday {day} does not belong to this year")
            if day in seen:
                problems.append(f"{year}: duplicate holiday {day}")
            seen.add(day)
    return problems


# --------------------------------------------------------------------------
# hours and balances
# --------------------------------------------------------------------------

def day_hours(day: date, configs: dict[int, dict], holidays: dict[int, frozenset[date]]) -> int:
    """Hours credited for a single day, from its own year's configuration."""
    config = configs.get(day.year)
    if config is None:
        return 0
    if day.weekday() >= 5 or day in holidays.get(day.year, frozenset()):
        return int(config["hours"]["holiday"])
    return int(config["hours"]["workday"])


def compute(data: dict) -> tuple[dict[int, dict[int, dict]], dict[int, dict]]:
    """Compute per-month detail and per-year balances.

    Returns (monthly, yearly). monthly[year][month] holds the full month's
    hours, the equal share, the hours credited to each code and the cumulative
    balance at the end of that month. yearly[year] holds the year's opening
    balance, its closing balance and whether the year is covered in full.
    """
    segments = build_segments(data)
    configs = year_configs(data)
    holidays = year_holidays(configs)

    monthly: dict[int, dict[int, dict]] = {}
    yearly: dict[int, dict] = {}
    carried: dict[str, Fraction] | None = None

    for year in sorted({day.year for day in segments}):
        config = configs[year]
        if "initial" in config:
            running = {code: Fraction(int(config["initial"][code])) for code in CODES}
        elif carried is not None:
            running = dict(carried)
        else:
            running = {code: Fraction(0) for code in CODES}
        opening = dict(running)
        monthly[year] = {}

        for month in range(1, 13):
            length = calendar.monthrange(year, month)[1]
            all_days = [date(year, month, day) for day in range(1, length + 1)]
            covered = [day for day in all_days if day in segments]
            if not covered:
                continue
            # Hours always span the whole month, even when data.yaml stops early.
            total = sum(day_hours(day, configs, holidays) for day in all_days)
            share = Fraction(total, len(CODES))
            per_code = {code: 0 for code in CODES}
            for day in covered:
                per_code[segments[day][0]] += day_hours(day, configs, holidays)
            for code in CODES:
                running[code] += Fraction(per_code[code]) - share
            monthly[year][month] = {
                "days": length,
                "uncovered": length - len(covered),
                "total": total,
                "share": share,
                "per_code": per_code,
                "balance": dict(running),
                "incomplete": len(covered) < length,
            }

        complete = len(monthly[year]) == 12 and not any(
            info["incomplete"] for info in monthly[year].values()
        )
        yearly[year] = {
            "initial": opening,
            "balance": dict(running),
            "incomplete": not complete,
        }
        carried = dict(running)
    return monthly, yearly


def round_half_away(value: Fraction) -> int:
    """Round to the nearest whole hour, halves away from zero."""
    sign = -1 if value < 0 else 1
    return sign * int((abs(value) * 2 + 1) // 2)


def format_signed(value: Fraction | int) -> str:
    """Render a balance as a signed whole number of hours."""
    hours = round_half_away(Fraction(value))
    return "0" if hours == 0 else f"{hours:+d}"


# --------------------------------------------------------------------------
# table plumbing
# --------------------------------------------------------------------------

def display_width(text: str) -> int:
    """Width of a cell in a monospaced terminal; emoji take two columns.

    Variation selectors and joiners render inside the glyph they follow, so
    they add nothing - without this, a heart like U+2764 U+FE0F would
    measure four columns.
    """
    return sum(
        0 if ord(char) in ZERO_WIDTH else 2 if ord(char) > 0x2000 else 1
        for char in text
    )


def column_widths(header: list[str], rows: list[list[str]], minimum: int = 1) -> list[int]:
    """Widest cell per column, honouring a minimum width."""
    return [
        max(minimum, display_width(header[i]), *(display_width(row[i]) for row in rows))
        if rows
        else max(minimum, display_width(header[i]))
        for i in range(len(header))
    ]


def pad(cell: str, width: int, align: str) -> str:
    """Pad a cell to width: left ('l'), right ('r') or centred ('c')."""
    slack = width - display_width(cell)
    if align == "r":
        return " " * slack + cell
    if align == "c":
        left = slack // 2
        return " " * left + cell + " " * (slack - left)
    return cell + " " * slack


def render_markdown_table(
    header: list[str],
    rows: list[list[str]],
    aligns: str | None = None,
) -> list[str]:
    """Render a GitHub-flavoured Markdown table.

    aligns holds one of 'l', 'r' or 'c' per column; it defaults to a left
    label column followed by right-aligned numbers.
    """
    # Minimum of 3 keeps the delimiter row valid in GitHub Markdown.
    widths = column_widths(header, rows, minimum=3)
    aligns = aligns or "l" + "r" * (len(header) - 1)

    def line(cells: Iterable[str]) -> str:
        return (
            "| "
            + " | ".join(pad(c, w, a) for c, w, a in zip(cells, widths, aligns))
            + " |"
        )

    delimiter = []
    for width, align in zip(widths, aligns):
        if align == "r":
            delimiter.append("-" * (width - 1) + ":")
        elif align == "c":
            delimiter.append(":" + "-" * (width - 2) + ":")
        else:
            delimiter.append("-" * width)
    return [line(header), "| " + " | ".join(delimiter) + " |", *(line(row) for row in rows)]


def render_text_table(
    header: list[str],
    rows: list[list[str]],
    aligns: str,
    rules_before: Iterable[int] = (),
) -> list[str]:
    """Render a box-drawn table for the terminal report."""
    widths = column_widths(header, rows)

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (width + 2) for width in widths) + right

    def line(cells: list[str]) -> str:
        return (
            "│ "
            + " │ ".join(pad(c, w, a) for c, w, a in zip(cells, widths, aligns))
            + " │"
        )

    marks = frozenset(rules_before)
    out = [rule("┌", "┬", "┐"), line(header), rule("├", "┼", "┤")]
    for index, row in enumerate(rows):
        if index in marks:
            out.append(rule("├", "┼", "┤"))
        out.append(line(row))
    out.append(rule("└", "┴", "┘"))
    return out


def month_label(month: int, info: dict) -> str:
    """Month number, marked when the month is not covered in full."""
    return f"{month}{INCOMPLETE_MARK}" if info["incomplete"] else str(month)


def year_label(year: int, info: dict) -> str:
    """Year label, marked when the year is not covered in full."""
    return f"{year}{INCOMPLETE_MARK}" if info["incomplete"] else str(year)


# --------------------------------------------------------------------------
# rendered views
# --------------------------------------------------------------------------

def detail_rows(
    monthly: dict[int, dict[int, dict]],
    yearly: dict[int, dict],
    year: int,
) -> list[list[str]]:
    """Rows of the detailed breakdown: carry-over, each month, then totals."""
    months = sorted(monthly[year])
    blank = [""] * 7
    rows: list[list[str]] = [
        ["init", *blank] + [format_signed(yearly[year]["initial"][code]) for code in CODES]
    ]
    for month in months:
        info = monthly[year][month]
        rows.append(
            [
                month_label(month, info),
                str(info["days"]),
                str(info["uncovered"]) if info["uncovered"] else "",
                str(info["total"]),
                f"{float(info['share']):.2f}",
                str(info["per_code"]["M"]),
                str(info["per_code"]["P"]),
                str(info["per_code"]["D"]),
                format_signed(info["balance"]["M"]),
                format_signed(info["balance"]["P"]),
                format_signed(info["balance"]["D"]),
            ]
        )
    totals = {
        key: sum(monthly[year][m][key] for m in months)
        for key in ("days", "uncovered", "total")
    }
    total_share = sum((monthly[year][m]["share"] for m in months), Fraction(0))
    per_code_total = {
        code: sum(monthly[year][m]["per_code"][code] for m in months) for code in CODES
    }
    rows.append(
        [
            "Σ",
            str(totals["days"]),
            str(totals["uncovered"]) if totals["uncovered"] else "",
            str(totals["total"]),
            f"{float(total_share):.2f}",
            str(per_code_total["M"]),
            str(per_code_total["P"]),
            str(per_code_total["D"]),
            format_signed(yearly[year]["balance"]["M"]),
            format_signed(yearly[year]["balance"]["P"]),
            format_signed(yearly[year]["balance"]["D"]),
        ]
    )
    return rows


def report(monthly: dict[int, dict[int, dict]], yearly: dict[int, dict]) -> str:
    """Build the terminal report used to cross-check against the spreadsheet."""
    lines: list[str] = []
    for year in sorted(monthly, reverse=True):
        rows = detail_rows(monthly, yearly, year)
        lines.append(year_label(year, yearly[year]))
        lines.extend(
            render_text_table(
                DETAIL_HEADER, rows, "r" * len(DETAIL_HEADER), rules_before=(1, len(rows) - 1)
            )
        )
        lines.append("")
    return "\n".join(lines)


def render_detail(monthly: dict[int, dict[int, dict]], yearly: dict[int, dict], year: int) -> str:
    """The same detailed breakdown as the terminal report, as Markdown."""
    rows = detail_rows(monthly, yearly, year)
    aligns = "l" + "r" * (len(DETAIL_HEADER) - 1)
    return "\n".join(render_markdown_table(DETAIL_HEADER, rows, aligns))


def render_cumulative(
    monthly: dict[int, dict[int, dict]],
    yearly: dict[int, dict],
    labels: dict[str, str],
    year: int,
) -> str:
    """Cumulative balances for one year: months downwards, codes across."""
    months = sorted(monthly[year], reverse=True)
    header = [year_label(year, yearly[year])] + [labels[code] for code in CODES]
    rows = [
        [month_label(m, monthly[year][m])]
        + [format_signed(monthly[year][m]["balance"][code]) for code in CODES]
        for m in months
    ]
    rows.append(["init"] + [format_signed(yearly[year]["initial"][code]) for code in CODES])
    return "\n".join(render_markdown_table(header, rows))


def week_start(day: date) -> date:
    """The Sunday that opens the week containing day."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def render_heatmap(
    segments: dict[date, tuple[str, date, date]],
    cells: dict[str, str],
    configs: dict[int, dict],
    holidays: dict[int, frozenset[date]],
    scope_start: date,
    scope_end: date,
) -> str:
    """Calendar heat map over a date range: rows are weeks, oldest first.

    Columns are weekdays from Sunday. The leading column is the week's first
    day inside the range, so together they identify every day. Weekends and
    holidays are drawn as hearts instead of squares, which also marks the
    days worth `hours.holiday`. Days outside the range stay blank and
    EMPTY_CELL marks only a real hole in the data.
    """
    hearts = heart_cells(cells)

    def is_off(day: date) -> bool:
        """True when the day counts as a weekend/holiday day."""
        return day.weekday() >= 5 or day in holidays.get(day.year, frozenset())

    in_scope = [day for day in segments if scope_start <= day <= scope_end]
    covered_from = min(in_scope) if in_scope else None
    covered_to = max(in_scope) if in_scope else None

    header = ["week", *WEEKDAY_NAMES]
    rows: list[list[str]] = []
    cursor = week_start(scope_start)
    while cursor <= scope_end:
        week = [cursor + timedelta(days=offset) for offset in range(7)]
        opening = next((d for d in week if scope_start <= d <= scope_end), None)
        row = [f"{opening.month:02d}-{opening.day:02d}" if opening else ""]
        for day in week:
            if not scope_start <= day <= scope_end:
                row.append("")
            elif day in segments:
                code = segments[day][0]
                row.append(hearts[code] if is_off(day) else cells[code])
            elif covered_from is not None and covered_from <= day <= covered_to:
                # A real hole inside the covered range; validate() flags it too.
                row.append(EMPTY_CELL)
            else:
                row.append("")
        rows.append(row)
        cursor += timedelta(days=7)

    config = configs.get(scope_start.year)
    workday = f" ({config['hours']['workday']} h)" if config else ""
    off = f" ({config['hours']['holiday']} h)" if config else ""
    plain = " ".join(f"{cells[code]} {code}" for code in CODES)
    shaded = " ".join(f"{hearts[code]} {code}" for code in CODES)
    legend = (
        f"workday{workday}: {plain} &nbsp;&nbsp; "
        f"weekend/holiday{off}: {shaded} &nbsp;&nbsp; {EMPTY_CELL} gap"
    )
    table = "\n".join(render_markdown_table(header, rows, "l" + "c" * 7))
    return f"{table}\n\n{legend}"


def render_changes(
    data: dict,
    labels: dict[str, str],
    configs: dict[int, dict],
    holidays: dict[int, frozenset[date]],
    scope_start: date,
    scope_end: date,
) -> str:
    """List the segments touching a date range, in order, with their hours.

    Every column describes only the part inside the range: a segment crossing
    either boundary is clipped, so the dates match the hours and the hours
    column adds up to the range's own total.
    """
    header = ["R", "start", "end", "hours"]
    rows: list[list[str]] = []
    for event in data["events"]:
        start = date.fromisoformat(event["start_date"])
        end = date.fromisoformat(event["end_date"])
        if end < scope_start or start > scope_end:
            continue
        shown_start = max(start, scope_start)
        shown_end = min(end, scope_end)
        hours = sum(
            day_hours(day, configs, holidays) for day in daterange(shown_start, shown_end)
        )
        rows.append(
            [
                labels[event["summary"]],
                shown_start.isoformat(),
                shown_end.isoformat(),
                str(hours),
            ]
        )
    return "\n".join(render_markdown_table(header, rows, "lllr"))


def render_nav(years: list[int], current: int, inside_archive: bool, self_year: int | None = None) -> str:
    """Render the year menu; the page being rendered is never linked to itself."""
    items: list[str] = []
    if inside_archive:
        items.append("[dty](../README.md)")
    for year in sorted(years, reverse=True):
        name = f"**{year}**" if year == current else str(year)
        if year == self_year:
            items.append(name)
        else:
            target = f"{year}.md" if inside_archive else f"{ARCHIVE_DIR}/{year}.md"
            items.append(f"[{name}]({target})")
    return " | ".join(items)


def render_year_page(
    year: int,
    segments: dict[date, tuple[str, date, date]],
    monthly: dict[int, dict[int, dict]],
    yearly: dict[int, dict],
    cells: dict[str, str],
    configs: dict[int, dict],
    holidays: dict[int, frozenset[date]],
    nav: str,
) -> str:
    """Standalone page for one year: detailed breakdown plus its full heat map."""
    last = max(day for day in segments if day.year == year)
    heatmap = render_heatmap(segments, cells, configs, holidays, date(year, 1, 1), last)
    return (
        f"# {year}\n\n"
        f"{render_detail(monthly, yearly, year)}\n\n"
        f"{heatmap}\n\n"
        f"{nav}\n"
    )


def splice(readme: str, markers: tuple[str, str], block: str) -> str:
    """Replace the content between a pair of markers with freshly rendered output."""
    begin_marker, end_marker = markers
    start = readme.find(begin_marker)
    end = readme.find(end_marker)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(f"README.md must contain the markers {begin_marker} and {end_marker}")
    return f"{readme[: start + len(begin_marker)]}\n{block}\n{readme[end:]}"


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--write", action="store_true", help="update the files in place")
    parser.add_argument("--data", default="data.yaml", type=Path, help="path to data.yaml")
    parser.add_argument("--readme", default="README.md", type=Path, help="path to README.md")
    parser.add_argument("--quiet", action="store_true", help="skip the detailed monthly report")
    parser.add_argument("--today", help="treat this ISO date as today")
    args = parser.parse_args()

    data = load_data(args.data)
    problems = validate(data)
    if problems:
        print("data.yaml - problems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    monthly, yearly = compute(data)
    if not args.quiet:
        print(report(monthly, yearly))

    cells = code_cells(data)
    labels = code_labels(cells)
    segments = build_segments(data)
    configs = year_configs(data)
    holidays = year_holidays(configs)
    today = date.fromisoformat(args.today) if args.today else date.today()
    years = sorted(yearly, reverse=True)

    month_first = date(today.year, today.month, 1)
    month_last = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    heading = f"## {MONTH_NAMES[today.month - 1]} {today.year}"

    current = args.readme.read_text(encoding="utf-8")
    updated = splice(
        current,
        HEATMAP_MARKERS,
        f"{heading}\n\n"
        + render_heatmap(segments, cells, configs, holidays, month_first, month_last),
    )
    updated = splice(
        updated,
        CHANGES_MARKERS,
        render_changes(data, labels, configs, holidays, month_first, month_last),
    )
    year_block = (
        f"## {today.year}\n\n{render_cumulative(monthly, yearly, labels, today.year)}"
        if today.year in monthly
        else f"## {today.year}\n\nNo data for {today.year} in `data.yaml` yet."
    )
    updated = splice(updated, STATS_MARKERS, year_block)
    updated = splice(updated, NAV_MARKERS, render_nav(years, today.year, inside_archive=False))

    pages: dict[Path, str] = {}
    for year in years:
        nav = render_nav(years, today.year, inside_archive=True, self_year=year)
        path = args.readme.parent / ARCHIVE_DIR / f"{year}.md"
        pages[path] = render_year_page(
            year, segments, monthly, yearly, cells, configs, holidays, nav
        )

    changed = [
        path for path, text in pages.items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]
    if updated == current and not changed:
        print("README.md: unchanged")
        return 0

    if args.write:
        if updated != current:
            args.readme.write_text(updated, encoding="utf-8")
            print("README.md: updated")
        for path in changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(pages[path], encoding="utf-8")
            print(f"{path}: written")
        return 0

    if updated != current:
        print("README.md diff (dry run, use --write to apply):")
        sys.stdout.writelines(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile="README.md",
                tofile="README.md (new)",
            )
        )
    for path in changed:
        print(f"{path}: would be written (dry run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
