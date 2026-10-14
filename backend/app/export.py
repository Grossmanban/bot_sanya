"""Excel export of shifts, per-person totals and penalties for a period."""

import io
import sqlite3
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import pricing
from .services import ACTIVE, full_name

STATUS_RU = {
    "active": "Выход",
    "restored": "Выход (восстановлен)",
    "cancelled": "Отменён",
    "finally_cancelled": "Отменён со штрафом",
    "awaiting_approval": "Ждёт решения",
    "rejected_by_admin": "Отклонён",
    "pre_declared_off": "Выходной",
}
HEADER_FILL = PatternFill("solid", fgColor="161A33")
HEADER_FONT = Font(bold=True, color="FFFFFF")


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list], widths: list[int], first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(header)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        ws.append(row)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22
    return ws


def build_xlsx(conn: sqlite3.Connection, date_from: date, date_to: date, user_id: int | None = None) -> bytes:
    params: list = [date_from.isoformat(), date_to.isoformat()]
    where = "s.shift_date BETWEEN ? AND ?"
    if user_id:
        where += " AND s.user_id = ?"
        params.append(user_id)
    shifts = [
        dict(r)
        for r in conn.execute(
            f"""SELECT s.*, u.first_name, u.last_name, u.username FROM shifts s JOIN users u ON u.id = s.user_id
                WHERE {where} ORDER BY s.shift_date, s.start_time IS NULL, s.start_time, u.first_name""",
            params,
        )
    ]

    wb = Workbook()

    totals: dict[int, dict] = {}
    for s in shifts:
        t = totals.setdefault(
            s["user_id"],
            {"name": full_name(s), "username": s["username"] or "", "shifts": 0, "plan": 0, "fact": 0, "no_fact": 0,
             "plan_pay": 0, "fact_pay": 0, "cancelled": 0, "penalties": 0, "off": 0},
        )
        if s["status"] in ACTIVE:
            t["shifts"] += 1
            t["plan"] += s["volume"]
            t["plan_pay"] += pricing.expected_pay(s["volume"])
            if s["fact_volume"] is not None:
                t["fact"] += s["fact_volume"]
                t["fact_pay"] += pricing.expected_pay(s["fact_volume"])
            else:
                t["no_fact"] += 1
        elif s["status"] in ("cancelled", "finally_cancelled"):
            t["cancelled"] += 1
        elif s["status"] == "pre_declared_off":
            t["off"] += 1
        t["penalties"] += s["penalty_amount"] or 0
    total_rows = [
        [t["name"], t["username"], t["shifts"], t["plan"], t["fact"], t["no_fact"], t["plan_pay"], t["fact_pay"],
         t["cancelled"], t["off"], t["penalties"], t["fact_pay"] - t["penalties"]]
        for t in sorted(totals.values(), key=lambda x: x["name"])
    ]
    if total_rows:
        total_rows.append(["Итого", "", *[sum(r[i] for r in total_rows) for i in range(2, 12)]])
    ws = _sheet(
        wb, "Итоги",
        ["Сотрудник", "Username", "Выходов", "План, шт.", "Факт, шт.", "Без факта", "План, ₽", "Факт, ₽",
         "Отмен", "Выходных", "Штрафы, ₽", "К выплате (факт − штрафы), ₽"],
        total_rows, [24, 16, 10, 11, 11, 11, 12, 12, 9, 11, 12, 28], first=True,
    )
    if total_rows:
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

    _sheet(
        wb, "Смены",
        ["Дата", "Сотрудник", "Статус", "Начало", "Конец", "Точка", "План, шт.", "Ставка, ₽", "План, ₽",
         "Факт, шт.", "Факт, ₽", "Штраф, ₽", "Срочная"],
        [
            [
                date.fromisoformat(s["shift_date"]).strftime("%d.%m.%Y"), full_name(s), STATUS_RU.get(s["status"], s["status"]),
                s["start_time"] or "", s["end_time"] or "", s["location"] or "", s["volume"] or "",
                pricing.calculate_rate(s["volume"]) if s["volume"] else "",
                pricing.expected_pay(s["volume"]) if s["volume"] and s["status"] in ACTIVE else "",
                s["fact_volume"] if s["fact_volume"] is not None else "",
                pricing.expected_pay(s["fact_volume"]) if s["fact_volume"] is not None else "",
                s["penalty_amount"] if s["penalty_amount"] is not None else "",
                "да" if s["is_urgent"] else "",
            ]
            for s in shifts
        ],
        [12, 24, 20, 9, 9, 16, 10, 10, 10, 10, 10, 10, 9],
    )

    _sheet(
        wb, "Штрафы",
        ["Дата смены", "Сотрудник", "Причина", "Сумма, ₽"],
        [
            [date.fromisoformat(s["shift_date"]).strftime("%d.%m.%Y"), full_name(s), "Окончательная отмена смены",
             s["penalty_amount"] if s["penalty_amount"] is not None else "не назначен"]
            for s in shifts
            if s["status"] == "finally_cancelled"
        ],
        [12, 24, 30, 14],
    )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_filename(date_from: date, date_to: date) -> str:
    return f"grafik_{date_from:%d.%m}-{date_to:%d.%m.%Y}.xlsx"
