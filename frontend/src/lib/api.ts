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
  /** effective role: owners come from the server config (ADMIN_IDS) */
  role: Role;
  work_status: "working" | "not_working";
  account_state: "active" | "paused" | "blocked";
  state_reason: string | null;
  network_id: number | null;
  is_owner?: boolean;
  created_at?: string;
  notify_reminders?: boolean;
}

export type Role = "owner" | "admin" | "trainer" | "employee";

export const ROLE_TITLES: Record<Role, string> = {
  owner: "Владелец",
  admin: "Администратор",
  trainer: "Тренер",
  employee: "Сотрудник",
};

/** see backend/app/permissions.py */
export type Capability =
  | "money" | "block" | "roles" | "global_actual" | "networks" | "fine_direct" | "approve_fines" | "fine_request"
  | "bonus" | "pause" | "review" | "schedule" | "broadcast_all" | "broadcast_network" | "settings" | "points"
  | "registrations" | "export" | "raspred";

export const isStaff = (u: User) => u.role !== "employee" && u.account_state !== "blocked";

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
  role_title?: string;
  capabilities?: Capability[];
}

export interface SettingsResponse {
  settings: ScheduleSettings;
  timezone: string;
  archive: { connected: boolean; title: string | null };
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
  fact_volume: number | null;
  fact_pay?: number;
  can_set_fact: boolean;
  shortfall: number;
  shortfall_status: "fined" | "waived" | null;
  shortfall_penalty: number | null;
}

export interface Shortfall {
  shift_id: number;
  date: string;
  user_id: number;
  name: string;
  username: string | null;
  location: string | null;
  start_time: string | null;
  end_time: string | null;
  volume: number;
  fact_volume: number;
  shortfall: number;
  percent: number;
  status: "fined" | "waived" | null;
  penalty: number | null;
}

export interface Point {
  id: number;
  name: string;
  capacity: number | null;
  active: number;
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
  points_load: Record<string, number>;
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
  fact_volume: number | null;
  shortfall: number;
  shortfall_status: "fined" | "waived" | null;
  shortfall_penalty: number | null;
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
    fact_volume: number;
    fact_pay: number;
    fact_missing: number;
    shortfall_shifts: number;
    shortfall_volume: number;
    shortfall_pending: number;
  };
}

export interface PenaltyRow {
  shift_id: number;
  kind: "final_cancel" | "shortfall";
  status: "fined" | "waived" | null;
  date: string;
  user_id: number;
  name: string;
  username: string | null;
  reason: string;
  amount: number | null;
  set_by: string | null;
  set_at: string | null;
}

export interface ScheduleSettings {
  eve_reminder_enabled: boolean;
  eve_reminder_time: string;
  auto_raspred_enabled: boolean;
  auto_raspred_time: string;
  week_reminder_enabled: boolean;
  week_reminder_weekday: number;
  week_reminder_time: string;
  fact_request_enabled: boolean;
  fact_request_delay: number;
  weekly_report_enabled: boolean;
  weekly_report_time: string;
  backup_enabled: boolean;
  backup_time: string;
}

export interface Broadcast {
  id: number;
  audience: string;
  text: string;
  html: string | null;
  signed: number;
  recipients: number;
  delivered: number;
  created_at: string;
  first_name: string | null;
  last_name: string | null;
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
  setNotify: (enabled: boolean) => patch<{ user: User }>("/api/me/notifications", { enabled }),
  points: () => request<{ items: Point[] }>("/api/points"),
  setFact: (id: number, fact_volume: number, key: string) =>
    post<ShiftResponse>(`/api/shifts/${id}/fact`, { fact_volume, idempotency_key: key }),
  myPenalties: () =>
    request<{
      items: { id: number; kind: "final_cancel" | "shortfall"; shift_date: string; penalty_amount: number | null; reason: string }[];
    }>("/api/me/penalties"),
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
    points: () => request<{ items: Point[] }>("/api/admin/points"),
    createPoint: (name: string, capacity: number | null) => post<{ point: Point }>("/api/admin/points", { name, capacity }),
    updatePoint: (id: number, body: { name?: string; capacity?: number; clear_capacity?: boolean; active?: boolean }) =>
      patch<{ point: Point }>(`/api/admin/points/${id}`, body),
    settings: () => request<SettingsResponse>("/api/admin/settings"),
    saveSettings: (body: Partial<ScheduleSettings>) => patch<SettingsResponse>("/api/admin/settings", body),
    broadcasts: () =>
      request<{ items: Broadcast[]; audiences: Record<string, string>; people: { id: number; working: boolean; admin: boolean }[] }>(
        "/api/admin/broadcasts",
      ),
    broadcast: (audiences: string[], html: string, signed: boolean) =>
      post<{ broadcast: Broadcast }>("/api/admin/broadcasts", { audiences, html, signed }),
    broadcastTest: (html: string, signed: boolean) => post<{ ok: boolean }>("/api/admin/broadcasts/test", { html, signed }),
    exportXlsx: (date_from: string, date_to: string, user_id?: number) =>
      post<{ ok: boolean }>("/api/admin/export", { date_from, date_to, user_id }),
    shortfalls: () => request<{ items: Shortfall[] }>("/api/admin/shortfalls"),
    decideShortfall: (shiftId: number, amount: number | null) =>
      post<{ shift: Shift; changed: boolean }>(`/api/admin/shifts/${shiftId}/shortfall`, { amount, override: true }),
    team: () => request<{ items: User[]; bot_username: string | null }>("/api/admin/team"),
    setRole: (userId: number, role: Exclude<Role, "owner">, key: string) =>
      post<{ user: User; changed: boolean }>(`/api/admin/team/${userId}/role`, { role, idempotency_key: key }),
    setAccess: (userId: number, allow: boolean, key: string) =>
      post<{ user: User; changed: boolean }>(`/api/admin/team/${userId}/access`, { allow, idempotency_key: key }),
    setState: (userId: number, state: User["account_state"], reason: string | null, key: string) =>
      post<{ user: User; changed: boolean }>(`/api/admin/team/${userId}/state`, { state, reason, idempotency_key: key }),
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
      body: Partial<ShiftPayload> & { reason: string; action?: AdminAction; penalty_amount?: number; fact_volume?: number },
      key: string,
    ) => patch<ShiftResponse>(`/api/admin/shifts/${id}`, { ...body, idempotency_key: key }),
    deleteShift: (id: number, reason: string, key: string) =>
      post<{ changed: boolean }>(`/api/admin/shifts/${id}/delete`, { reason, idempotency_key: key }),
  },
};
