import type { Shift, ShiftStatus } from "./api";

export const WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
export const WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const MONTHS_SHORT = ["янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек."];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

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

export const dayMonth = (iso: string) => {
  const { d, m } = parseDay(iso);
  return `${d} ${MONTHS_GEN[m - 1]}`;
};

/** "12–18 окт." or "29 сент. – 5 окт." */
export const weekRange = (start: string, end: string) => {
  const a = parseDay(start);
  const b = parseDay(end);
  if (a.m === b.m) return `${a.d}–${b.d} ${MONTHS_SHORT[b.m - 1]}`;
  return `${a.d} ${MONTHS_SHORT[a.m - 1]} – ${b.d} ${MONTHS_SHORT[b.m - 1]}`;
};

export const addDays = (iso: string, days: number) => {
  const { y, m, d } = parseDay(iso);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
};

export const rub = (n: number) => `${n.toLocaleString("ru-RU")}\u00a0₽`;

export function plural(n: number, one: string, few: string, many: string) {
  const a = n % 10;
  const b = n % 100;
  if (a === 1 && b !== 11) return one;
  if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
  return many;
}

export type DayState = ShiftStatus | "none";

export interface StatusMeta {
  label: string;
  /** text colour */
  text: string;
  /** small dot */
  dot: string;
  /** soft pill background */
  soft: string;
  /** shift bar on the day line */
  bar: string;
}

export const STATUS_META: Record<DayState, StatusMeta> = {
  none: { label: "Не отмечен", text: "text-muted-foreground", dot: "bg-idle", soft: "bg-muted", bar: "" },
  active: { label: "Выхожу", text: "text-go", dot: "bg-go", soft: "bg-go-soft", bar: "bg-go" },
  restored: { label: "Выхожу", text: "text-go", dot: "bg-go", soft: "bg-go-soft", bar: "bg-go" },
  awaiting_approval: { label: "Ждёт решения", text: "text-wait", dot: "bg-wait", soft: "bg-wait-soft", bar: "bg-wait" },
  pre_declared_off: { label: "Выходной", text: "text-off", dot: "bg-off", soft: "bg-muted", bar: "" },
  cancelled: {
    label: "Отменено",
    text: "text-cancel",
    dot: "bg-cancel",
    soft: "bg-cancel-soft",
    bar: "border-[1.5px] border-dashed border-cancel bg-cancel-soft",
  },
  finally_cancelled: {
    label: "Отменено со штрафом",
    text: "text-final",
    dot: "bg-final",
    soft: "bg-cancel-soft",
    bar: "border-[1.5px] border-dashed border-final bg-cancel-soft",
  },
  rejected_by_admin: {
    label: "Отклонено",
    text: "text-muted-foreground",
    dot: "bg-idle",
    soft: "bg-muted",
    bar: "border-[1.5px] border-dashed border-idle",
  },
};

export const dayState = (shift: Shift | null): DayState => shift?.status ?? "none";

export const timeRange = (s: { start_time: string | null; end_time: string | null }) =>
  s.start_time && s.end_time ? `${s.start_time}–${s.end_time}` : "—";

export const isGoing = (s: Shift | null) => !!s && (s.status === "active" || s.status === "restored");
