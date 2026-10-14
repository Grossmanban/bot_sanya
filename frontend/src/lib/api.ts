import { initData } from "./telegram";

export type ShiftStatus =
  | "active"
  | "restored"
  | "cancelled"
  | "finally_cancelled"
  | "awaiting_approval"
  | "rejected_by_admin"
  | "pre_declared_off";

export interface User {
  id: number;
  telegram_id: number;
  first_name: string;
  last_name: string;
  username: string | null;
  status: "pending" | "approved" | "rejected";
  role: "employee" | "admin";
  work_status: "working" | "not_working";
}

export interface Pricing {
  base_rate: number;
  lazy_rate: number;
  lazy_threshold: number;
}

export interface Me {
  registered: boolean;
  user?: User;
  pricing?: Pricing;
  bot_username?: string | null;
}

export interface Shift {
  id: number;
  date: string;
  status: ShiftStatus;
  start_time: string | null;
  end_time: string | null;
  volume: number | null;
  location: string | null;
  edit_count: number;
  is_urgent: boolean;
  penalty_amount: number | null;
  admin_locked: boolean;
  can_edit: boolean;
  can_cancel: boolean;
  can_final_cancel: boolean;
  can_restore: boolean;
  can_clear_off: boolean;
  rate?: number;
  expected_pay?: number;
  lazy?: boolean;
}

export interface Colleague {
  name: string;
  first_name: string;
  start_time: string;
  end_time: string;
  location: string;
}

export interface Day {
  date: string;
  weekday: number;
  is_past: boolean;
  is_today: boolean;
  my_shift: Shift | null;
  colleagues: Colleague[];
  colleagues_off: { name: string; first_name: string }[];
}

export interface Week {
  offset: number;
  start: string;
  end: string;
  today: string;
  days: Day[];
}

export interface ShiftPayload {
  start_time: string;
  end_time: string;
  volume: number;
  location: string;
}

export interface AdminShift extends Shift {
  user_id: number;
  name: string;
  username: string | null;
}

export interface Raspred {
  date: string;
  rows: { shift_id: number; name: string; username: string | null; start_time: string; end_time: string; volume: number; location: string }[];
  total_count: number;
  total_volume: number;
  text: string;
}

export interface StatsItem {
  id: number;
  date: string;
  status: ShiftStatus;
  start_time: string | null;
  end_time: string | null;
  volume: number | null;
  location: string | null;
  rate: number | null;
  expected_pay: number | null;
  penalty_amount: number | null;
  notes: string;
}

export interface Stats {
  user: { id: number; name: string; username: string | null };
  from: string;
  to: string;
  items: StatsItem[];
  totals: {
    shifts: number;
    active_shifts: number;
    active_volume: number;
    cancelled_volume: number;
    penalties: number;
    expected_pay: number;
  };
}

export interface PenaltyRow {
  shift_id: number;
  date: string;
  user_id: number;
  name: string;
  username: string | null;
  reason: string;
  amount: number | null;
  set_by: string | null;
  set_at: string | null;
}

export type AdminAction = "force_cancel" | "force_restore" | "approve_urgent" | "reject_urgent";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export const newKey = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Telegram-Init-Data": initData(),
        ...(init.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером. Проверьте интернет.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Что-то пошло не так";
    throw new ApiError(res.status, data.code ?? "error", detail);
  }
  return data as T;
}

const post = <T>(path: string, body: object) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
const patch = <T>(path: string, body: object) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) });

type ShiftResponse = { shift: Shift | null; changed: boolean };

export const api = {
  me: () => request<Me>("/api/me"),
  setWorkStatus: (work_status: User["work_status"]) =>
    patch<{ user: User }>("/api/me/work-status", { work_status, idempotency_key: newKey() }),
  myPenalties: () =>
    request<{ items: { id: number; shift_date: string; penalty_amount: number | null; penalty_set_at: string | null }[] }>(
      "/api/me/penalties",
    ),
  week: (offset: number) => request<Week>(`/api/schedule/week?offset=${offset}`),
  history: (weeksBack = 12) =>
    request<{ weeks: { start: string; end: string; shifts: Shift[] }[] }>(`/api/history?weeks_back=${weeksBack}`),

  // every mutation carries a fresh idempotency key created when the user taps,
  // so a double tap or a network retry of the same action is applied once
  create: (shift_date: string, p: ShiftPayload, key: string) =>
    post<ShiftResponse>("/api/shifts/create", { shift_date, ...p, idempotency_key: key }),
  edit: (id: number, p: ShiftPayload, key: string) =>
    patch<ShiftResponse>(`/api/shifts/${id}`, { ...p, idempotency_key: key }),
  cancel: (id: number, key: string) => post<ShiftResponse>(`/api/shifts/${id}/cancel`, { idempotency_key: key }),
  restore: (id: number, p: ShiftPayload, key: string) =>
    post<ShiftResponse>(`/api/shifts/${id}/restore`, { ...p, idempotency_key: key }),
  finalCancel: (id: number, key: string) =>
    post<ShiftResponse>(`/api/shifts/${id}/final-cancel-confirm`, { idempotency_key: key }),
  preDeclareOff: (shift_date: string, key: string) =>
    post<ShiftResponse>("/api/shifts/pre-declare-off", { shift_date, idempotency_key: key }),
  clearOff: (id: number, key: string) => post<ShiftResponse>(`/api/shifts/${id}/clear-off`, { idempotency_key: key }),

  admin: {
    users: () => request<{ items: User[] }>("/api/admin/users"),
    raspred: () => request<Raspred>("/api/admin/raspred/tomorrow"),
    day: (day: string) =>
      request<{ date: string; items: AdminShift[]; total_count: number; total_volume: number }>(`/api/admin/day?day=${day}`),
    stats: (userId: number, from: string, to: string) =>
      request<Stats>(`/api/admin/stats?user_id=${userId}&from=${from}&to=${to}`),
    penalties: (params: { userId?: number; from?: string; to?: string }) => {
      const q = new URLSearchParams();
      if (params.userId) q.set("user_id", String(params.userId));
      if (params.from) q.set("from", params.from);
      if (params.to) q.set("to", params.to);
      return request<{ items: PenaltyRow[] }>(`/api/admin/penalties?${q}`);
    },
    editShift: (
      id: number,
      body: Partial<ShiftPayload> & { reason: string; action?: AdminAction; penalty_amount?: number },
      key: string,
    ) => patch<ShiftResponse>(`/api/admin/shifts/${id}`, { ...body, idempotency_key: key }),
    deleteShift: (id: number, reason: string, key: string) =>
      post<{ changed: boolean }>(`/api/admin/shifts/${id}/delete`, { reason, idempotency_key: key }),
  },
};
