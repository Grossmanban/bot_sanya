import type { Shift, ShiftStatus } from "./api";

export const WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
export const WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const MONTHS_SHORT = ["янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек."];

// Dates come from the API as YYYY-MM-DD in Moscow time; parse them as plain calendar dates.
export function parseDay(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m, d, weekday: (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7 };
}

export const ddmm = (iso: string) => {
  const { d, m } = parseDay(iso);
  return `${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}`;
};

export const shortDate = (iso: string) => {
  const { d, m, weekday } = parseDay(iso);
  return `${WEEKDAYS_SHORT[weekday]}, ${d} ${MONTHS_SHORT[m - 1]}`;
};

export const longDate = (iso: string) => {
  const { d, m, weekday } = parseDay(iso);
  return `${WEEKDAYS[weekday]}, ${d} ${MONTHS_SHORT[m - 1]}`;
};

export const addDays = (iso: string, days: number) => {
  const { y, m, d } = parseDay(iso);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
};

export const rub = (n: number) => `${n.toLocaleString("ru-RU")} ₽`;

export function plural(n: number, one: string, few: string, many: string) {
  const a = n % 10;
  const b = n % 100;
  if (a === 1 && b !== 11) return one;
  if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
  return many;
}

export type DayState = ShiftStatus | "none";

export const STATUS_META: Record<DayState, { label: string; emoji: string; className: string }> = {
  none: { label: "Не отметился", emoji: "❔", className: "text-pending" },
  active: { label: "Выхожу", emoji: "✅", className: "text-go" },
  restored: { label: "Выхожу", emoji: "✅", className: "text-go" },
  awaiting_approval: { label: "Ожидает решения", emoji: "⏳", className: "text-wait" },
  pre_declared_off: { label: "Не выхожу", emoji: "🚫", className: "text-off" },
  cancelled: { label: "Отменено", emoji: "❌", className: "text-cancel" },
  finally_cancelled: { label: "Финальная отмена", emoji: "🔒", className: "text-final" },
  rejected_by_admin: { label: "Отклонено админом", emoji: "⛔", className: "text-muted-foreground" },
};

export const dayState = (shift: Shift | null): DayState => shift?.status ?? "none";

export const timeRange = (s: { start_time: string | null; end_time: string | null }) =>
  s.start_time && s.end_time ? `${s.start_time}–${s.end_time}` : "—";
