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
  /** network name (team listing) */
  network?: string | null;
  is_owner?: boolean;
  created_at?: string;
  notify_reminders?: boolean;
  /** since when the current work status is on, and how many days that is */
  work_status_at?: string | null;
  status_days?: number | null;
  /** personal norm of work days a week; null — the common norm, 0 — no norm */
  min_weekly_shifts?: number | null;
  /** owners only: personal rate (null — the network's or the common) and «не применять налог на лень» */
  personal_rate?: number | null;
  lazy_exempt?: boolean;
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
  | "registrations" | "export" | "raspred" | "all_people" | "forgive_fines";

export const isStaff = (u: User) => u.role !== "employee" && u.account_state !== "blocked";

export interface Pricing {
  base_rate: number;
  lazy_rate: number;
  lazy_threshold: number;
  /** a trainee: shifts are training ones, not paid */
  training?: boolean;
}

export interface Me {
  registered: boolean;
  user?: User;
  pricing?: Pricing;
  bot_username?: string | null;
  role_title?: string;
  capabilities?: Capability[];
  network?: string | null;
  events?: number;
  /** a candidate (status pending): their questionnaire — none yet / returned to the reserve, or where it stands */
  application?: { status: ApplicationStatus; training: { training_date: string | null; training_time: string | null;
    training_location: string | null } } | null;
  /** a trainee: the internship's progress */
  internship?: StageProgress | null;
}

export type ApplicationStatus = "new" | "scheduled" | "internship" | "done" | "rejected" | "returned";

export interface StageProgress {
  stage: 1 | 2;
  units: number;
  pending: number;
  target: number;
  review: boolean;
  finished?: boolean;
}

export interface ApplicationForm {
  full_name: string;
  age: number;
  metro: string;
  min_days: number;
  training_days: number[];
  communication: number;
  truthful: boolean;
  consent: boolean;
}

/** The onboarding card (the customer's module 3). */
export interface Application {
  id: number;
  status: ApplicationStatus;
  full_name: string;
  age: number;
  metro: string;
  min_days: number;
  communication: number;
  training_days: number[];
  created_at: string;
  reject_reason: string | null;
  decided_at: string | null;
  training_date: string | null;
  training_time: string | null;
  training_location: string | null;
  training_confirmed_at: string | null;
  contact_manager: string | null;
  contacted_at: string | null;
  contact_answer: "wrote" | "cant_write" | null;
  contact_answer_at: string | null;
  stage1_counted: number | null;
  stage1_at: string | null;
  stage2_counted: number | null;
  stage2_at: string | null;
  user_id: number;
  telegram_id: number;
  username: string | null;
  referrer: string | null;
  /** to open the referrer's Telegram profile */
  referrer_tg: { telegram_id: number; username: string | null } | null;
  trainer: { id: number; name: string; username: string | null } | null;
  earlier: number;
  progress: StageProgress | null;
  avatar_url: string;
}

export interface Referral {
  id: number;
  name: string;
  status: ApplicationStatus;
  created_at: string;
  progress: StageProgress | null;
  stage1_counted: number | null;
  stage2_counted: number | null;
}

/** ТЗ №20, №22: what goals and events share once running. */
export interface Lifecycle {
  prize: string | null;
  prize_type: string | null;
  prize_photo_url: string | null;
  /** optimistic locking: sent back with every edit */
  version: number;
  visibility: "auto" | "shown" | "hidden";
  hidden_at: string | null;
  /** admin lists only */
  in_feed?: boolean;
  can_manage?: boolean;
}

export interface LiveEvent extends Lifecycle {
  id: number;
  title: string;
  description: string | null;
  /** the description with formatting (Telegram HTML, sanitized by the server) */
  description_html: string | null;
  /** «Гонка до 30 шт., 1 победитель» */
  rule: string;
  /** «Побеждает первый, кто сделает 30 шт. и подтвердит скриншотом» */
  how_to_win: string;
  prize_media_kind?: "photo" | "video" | null;
  mode: "first_to_reach" | "accumulative";
  target: number | null;
  max_winners: number;
  max_attempts: number;
  starts_at: string;
  ends_at: string;
  buffer_min: number;
  status: "active" | "finished" | "cancelled";
  phase: "upcoming" | "live" | "review" | "finished" | "cancelled";
  network: string | null;
  leaderboard: { user_id: number; name: string; score: number; confirmed: number; pending: number; place: number; winner: boolean; out: boolean }[];
  winners_count: number;
  finished_at: string | null;
  created_at: string;
  /** admin list: confirmed results that can be taken back */
  entries?: { id: number; user_id: number; value: number; status: string; annulled: boolean; reason: string | null }[];
  my: {
    score: number;
    confirmed: number;
    winner: boolean;
    attempts_left: number;
    pending: boolean;
    can_submit: boolean;
    entries: { id: number; attempt: number; value: number; status: "pending_review" | "confirmed" | "rejected"; reason: string | null; annulled: boolean; submitted_at: string }[];
  };
}

export interface EventChanges {
  title?: string;
  description?: string | null;
  starts_at?: string;
  ends_at?: string;
  target?: number;
  max_winners?: number;
  max_attempts?: number;
  prize?: string | null;
  prize_type?: string | null;
  prize_photo?: string | null;
  prize_media_kind?: "photo" | "video";
}

export interface GoalChanges {
  title?: string;
  reset_time?: string;
  location?: string;
  forever?: boolean;
  description?: string | null;
  target?: number;
  date_from?: string;
  date_to?: string;
  prize?: string | null;
  prize_type?: string | null;
  prize_photo?: string | null;
}

export interface PendingEntry {
  id: number;
  event_id: number;
  event: string;
  mode: LiveEvent["mode"];
  target: number | null;
  user_id: number;
  name: string;
  value: number;
  attempt: number;
  submitted_at: string;
  photo_url: string;
}

export interface Goal extends Lifecycle {
  id: number;
  title: string;
  description: string | null;
  kind: "personal" | "team" | "network" | "group" | "point";
  mode: "shared" | "each";
  target: number;
  date_from: string;
  date_to: string;
  status: "active" | "completed" | "archived";
  network: string | null;
  person?: string;
  progress: number;
  total: number;
  participants: { user_id: number; name: string; volume: number; done: boolean; annulled: boolean; annul_reason: string | null;
    /** a point goal: this fact is still under review */
    on_review?: boolean }[];
  /** a point's daily norm: the point, when its day starts, the day counted now, «каждый день», the day's plan */
  location?: string;
  reset_time?: string;
  day?: string;
  forever?: boolean;
  plan?: number;
  completed_at: string | null;
  /** ТЗ №20: how many have «выполнено» while below the (raised) target */
  revocable: number;
  /** the viewer's result here was annulled — the reason */
  my_annulled: string | null;
  joined: boolean;
  done: boolean;
  my_volume: number;
  /** ТЗ №18: approved before the goal was created (counted automatically) and still under review */
  backfill?: number;
  pending?: number;
}

export interface NetworkMember {
  id: number;
  name: string;
  username: string | null;
  telegram_id: number;
  role: Role;
  work_status: User["work_status"];
  account_state: User["account_state"];
  status_days?: number | null;
}

export interface Network {
  id: number;
  name: string;
  status: "active" | "archived";
  trainer: { id: number; name: string; username: string | null; telegram_id: number; is_owner?: boolean } | null;
  members: NetworkMember[];
  /** owners only (customer round B): null — the common rate / the common commission */
  rate_base?: number | null;
  ladder?: LadderStep[];
  ladder_period?: "day" | "week";
  ladder_counts_trainer?: boolean;
}

/** «From N pieces → R ₽ per piece»: the reached step pays every piece. */
export interface LadderStep {
  from: number;
  rate: number;
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
  /** a trainee's training shift: not paid */
  training?: boolean;
  fact_volume: number | null;
  fact_pay?: number;
  report_state: ReportState | null;
  /** present on the employee's own cards and in the admin day view */
  report?: ReportView | null;
  shortfall: number;
  shortfall_status: "fined" | "waived" | null;
  shortfall_penalty: number | null;
}

export type ReportState = "pending_review" | "approved" | "needs_fix" | "rejected" | "no_show";

export interface ReportAttempt {
  id: number;
  attempt: number;
  fact_volume: number;
  status: "pending_review" | "approved" | "auto_approved" | "approved_team" | "rejected_penalty" | "rejected_retry";
  source: "app" | "bot" | "admin" | "legacy";
  submitted_at: string;
  review_comment: string | null;
  penalty_amount: number | null;
  photo_url: string | null;
}

export interface ReportView {
  state: ReportState | null;
  deadline: string;
  can_submit: boolean;
  started: boolean;
  note: string | null;
  fine: number | null;
  decided: "fined" | "waived" | null;
  last: ReportAttempt | null;
  team: TeamDay | null;
  /** why a shortfall fine can't be given now (no global actual yet, or the team met the plan) */
  fine_block: string | null;
  /** a trainer's fine waiting for an owner */
  fine_pending?: number | null;
}

export interface TeamDay {
  plan: number;
  actual: number;
  met: boolean;
}

export interface DayActual {
  date: string;
  is_today: boolean;
  shifts: number;
  plan: number;
  facts: number;
  reported: number;
  pending: number;
  actual: number | null;
  met: boolean | null;
  set_by: string | null;
  set_at: string | null;
  fined: { shift_id: number; user_id: number; amount: number; name: string }[];
  log: { id: number; old_value: number | null; new_value: number; plan: number; actor_name: string; created_at: string }[];
}

export type QueueKind = "review" | "no_show" | "missing";

export interface QueueItem {
  shift_id: number;
  date: string;
  user_id: number;
  telegram_id: number;
  name: string;
  username: string | null;
  location: string | null;
  start_time: string;
  end_time: string;
  volume: number;
  fact_volume: number | null;
  state: ReportState | null;
  decided: "fined" | "waived" | null;
  fine: number | null;
  note: string | null;
  deadline: string;
  attempts: ReportAttempt[];
  team: TeamDay | null;
  fine_block: string | null;
  fine_pending?: number | null;
}

export type ReportCounts = {
  review: number;
  no_show: number;
  actual_missing?: number;
  fines_pending?: number;
  fines_draft?: number;
  violations?: number;
};

/** ТЗ 12.4: a week below the norm of work days, waiting for «штраф» or «оправдание». */
export interface Violation {
  id: number;
  /** weekly — below the norm of work days; unmarked — nothing marked for one day by the deadline */
  kind: "weekly" | "unmarked";
  user_id: number;
  name: string;
  username: string | null;
  telegram_id: number;
  network: string | null;
  week_start: string;
  week_end: string;
  week_number: number;
  required_days: number;
  actual_days: number;
  absent_dates: string[];
  status: "pending" | "penalized" | "waived";
  default_comment: string;
  resolution_comment: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  media_count: number;
  media: string[];
  fine: { id: number; amount: number; status: TxStatus } | null;
  created_at: string;
}

export type TxType = "fine" | "bonus" | "deduction" | "payout_offset";
export type TxStatus = "draft" | "pending_admin_approval" | "active" | "paid" | "offset" | "cancelled" | "rejected";

export interface Tx {
  id: number;
  user_id: number;
  type: TxType;
  amount: number | null;
  status: TxStatus;
  reason: string;
  source: "manual" | "final_cancel" | "report" | "no_show" | "payout" | "attendance";
  shift_id: number | null;
  event_date: string;
  created_at: string;
  created_by: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_comment: string | null;
  has_media: boolean;
  media_url?: string | null;
  /** a deduction entered in pieces: pieces × rate = amount */
  pieces?: number | null;
  rate?: number | null;
  linked_tx_id?: number | null;
  /** journal rows carry the person */
  name?: string;
  username?: string | null;
  telegram_id?: number;
}

export interface Balance {
  fines: number;
  bonuses: number;
  deductions: number;
  balance: number;
  pending?: number;
  drafts: number;
}

export interface MoneyPerson extends Balance {
  user_id: number;
  name: string;
  username: string | null;
  telegram_id: number;
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
  /** the owner hid colleagues' names from employees: only how many */
  names_hidden?: boolean;
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
  rows: { shift_id: number; name: string; username: string | null; start_time: string; end_time: string; volume: number; location: string;
    /** a trainee's training shift */ trainee?: boolean }[];
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
  kind: "final_cancel" | "report" | "no_show" | "manual" | "payout" | "attendance";
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
  weekly_attendance_quota: number;
  show_colleague_names: boolean;
  /** ТЗ №22: finished goals and events move to «Архив» after this many days */
  feed_hide_after_days: number;
  attendance_check_enabled: boolean;
  attendance_check_time: string;
  daily_mark_enabled: boolean;
  daily_mark_reminder_time: string;
  daily_mark_deadline: string;
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
  report_reminder_enabled: boolean;
  report_reminder_lead: number;
  report_digest_enabled: boolean;
  report_digest_time: string;
  report_digest_to: "owners" | "admins" | "staff";
  global_actual_evening_enabled: boolean;
  global_actual_evening_time: string;
  global_actual_morning_enabled: boolean;
  global_actual_morning_time: string;
  rate_base: number;
  rate_lazy: number;
  lazy_threshold: number;
  trainer_commission: number;
  /** owners only: what the business gets per piece (the owner's earnings in payouts) */
  revenue_per_piece?: number;
}

export interface PaymentProfile {
  phone: string;
  bank: string;
  recipient: string | null;
  updated_at: string;
}

export interface PayoutRates {
  rate_base: number;
  rate_lazy: number;
  lazy_threshold: number;
  trainer_commission: number;
}

export interface PayoutRow {
  user_id: number;
  name: string;
  role: Role;
  network: string | null;
  phone: string | null;
  bank: string | null;
  recipient: string | null;
  personal_volume: number;
  team_volume: number;
  base_calc: number;
  bonuses: number;
  fines: number;
  deductions: number;
  total: number;
  carried_debt: number;
  has_deductions: boolean;
  /** pieces × the person's rate of each day */
  pieces_pay: number;
  /** the trainer's ladder money for the network */
  commission: number;
  commission_days?: { date: string; volume: number; step_volume: number; rate: number; amount: number }[];
  rates?: { rate_base: number; rate_lazy: number; lazy_threshold: number };
}

/** The owner's side of a payout (customer round B). */
export interface PayoutSummary {
  pieces: number;
  revenue_per_piece: number;
  revenue: number;
  pieces_pay: number;
  commission: number;
  bonuses: number;
  withheld: number;
  paid: number;
  owner: number;
}

export interface PayoutItem extends Omit<PayoutRow, "has_deductions" | "commission_days" | "rates"> {
  id: number;
  base: number;
}

export interface PayoutBatch {
  id: number;
  title: string;
  period_from: string;
  period_to: string;
  total: number;
  created_at: string;
  rates: PayoutRates;
  proof_media: string[];
  deduction_media: string[];
  items: PayoutItem[];
  summary?: PayoutSummary | null;
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
  require_ack: number;
  media_file_id: string | null;
  media_kind?: "photo" | "video";
  network_id: number | null;
  read: number;
  /** «Возможность ответить»: how many people replied */
  allow_reply?: number;
  replied?: number;
  /** the photo or video, signed — to see the sent message in full */
  media_url?: string | null;
}

export interface BroadcastTemplate {
  id: number;
  title: string;
  html: string;
  author: string | null;
  can_edit: boolean;
}

export interface BroadcastReply {
  text: string | null;
  created_at: string;
  photos: number;
  videos: number;
}

export interface BroadcastPerson {
  id: number;
  name: string;
  working: boolean;
  admin: boolean;
  network_id: number | null;
  network: string | null;
  /** has a shift or a day off tomorrow */
  marked_tomorrow: boolean;
}

export interface Receipt {
  user_id: number;
  name: string;
  username: string | null;
  telegram_id: number;
  delivered: boolean;
  read_at: string | null;
  reminders: number;
  reminded_at: string | null;
  replies: BroadcastReply[];
}

export interface BroadcastDraft {
  audiences: string[];
  html: string;
  signed: boolean;
  network_id: number | null;
  user_ids: number[] | null;
  require_ack: boolean;
  media_file_id: string | null;
  /** only those of the chosen who haven't marked tomorrow (no shift, no day off) */
  only_unmarked_tomorrow?: boolean;
  media_kind?: "photo" | "video";
  allow_reply?: boolean;
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

/** Raw file body (a compressed screenshot); parameters go in the query string. */
const upload = <T>(path: string, file: Blob | null) =>
  request<T>(path, { method: "POST", body: file ?? undefined, headers: { "Content-Type": file?.type || "application/octet-stream" } });

const post = <T>(path: string, body: object) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
const patch = <T>(path: string, body: object) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) });

type ShiftResponse = { shift: Shift | null; changed: boolean };

export const api = {
  me: () => request<Me>("/api/me"),
  setWorkStatus: (work_status: User["work_status"]) =>
    patch<{ user: User }>("/api/me/work-status", { work_status, idempotency_key: newKey() }),
  setNotify: (enabled: boolean) => patch<{ user: User }>("/api/me/notifications", { enabled }),
  points: () => request<{ items: Point[] }>("/api/points"),
  submitReport: (id: number, fact_volume: number, photo: Blob, key: string) =>
    upload<ShiftResponse>(`/api/shifts/${id}/report?fact_volume=${fact_volume}&idempotency_key=${key}`, photo),
  myPenalties: () =>
    request<{
      items: { id: number; kind: string; shift_date: string; penalty_amount: number | null; reason: string }[];
    }>("/api/me/penalties"),
  paymentProfile: () => request<{ profile: PaymentProfile | null; banks: string[] }>("/api/me/payment-profile"),
  savePaymentProfile: (body: { phone: string; bank: string; recipient: string }, key: string) =>
    request<{ profile: PaymentProfile; banks: string[] }>("/api/me/payment-profile", {
      method: "PUT",
      body: JSON.stringify({ ...body, idempotency_key: key }),
    }),
  events: () => request<{ items: LiveEvent[] }>("/api/events"),
  event: (id: number) => request<{ event: LiveEvent }>(`/api/events/${id}`),
  submitEntry: (id: number, value: number, photo: Blob, key: string) =>
    upload<{ event: LiveEvent; changed: boolean }>(`/api/events/${id}/entries?value=${value}&idempotency_key=${key}`, photo),
  goals: () => request<{ items: Goal[] }>("/api/goals"),
  archive: () => request<{ events: LiveEvent[]; goals: Goal[] }>("/api/archive"),
  joinGoal: (id: number) => post<{ goal: Goal }>(`/api/goals/${id}/join`, {}),
  register: (form: ApplicationForm) => post<{ application: { status: ApplicationStatus } }>("/api/register", form),
  referrals: () => request<{ code: string; link: string | null; items: Referral[];
    targets: { internship_units: number; second_stage_units: number } }>("/api/me/referrals"),
  myFinances: () => request<{ balance: Balance; items: Tx[] }>("/api/me/finances"),
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
      request<{
        items: Broadcast[];
        audiences: Record<string, string>;
        people: BroadcastPerson[];
        networks: { id: number; name: string }[];
        scope: "all" | "network";
      }>("/api/admin/broadcasts"),
    broadcast: (draft: BroadcastDraft) => post<{ broadcast: Broadcast }>("/api/admin/broadcasts", draft),
    broadcastTest: (body: { html: string; signed: boolean; require_ack: boolean; media_file_id: string | null;
      media_kind: "photo" | "video"; allow_reply: boolean }) => post<{ ok: boolean }>("/api/admin/broadcasts/test", body),
    uploadVideo: (file: Blob) => upload<{ file_id: string }>("/api/admin/media/video", file),
    onboarding: (tab: "new" | "scheduled" | "internship" | "rejected") =>
      request<{ items: Application[]; counts: Record<"new" | "scheduled" | "internship" | "rejected", number>;
        trainers: { id: number; name: string; username: string | null }[]; points: string[];
        targets: { internship_units: number; second_stage_units: number } }>(`/api/admin/onboarding?tab=${tab}`),
    assignTraining: (id: number, body: { date: string; time: string; location: string; trainer_id: number }, key: string) =>
      post<{ application: Application }>(`/api/admin/onboarding/${id}/training`, { ...body, idempotency_key: key }),
    rejectApplication: (id: number, reason: string | null, key: string) =>
      post<{ application: Application }>(`/api/admin/onboarding/${id}/reject`, { reason, idempotency_key: key }),
    returnApplication: (id: number, key: string) =>
      post<{ application: Application }>(`/api/admin/onboarding/${id}/return`, { idempotency_key: key }),
    contactApplication: (id: number, manager: string) =>
      post<{ application: Application }>(`/api/admin/onboarding/${id}/contact`, { manager }),
    startInternship: (id: number, key: string) =>
      post<{ application: Application }>(`/api/admin/onboarding/${id}/internship`, { idempotency_key: key }),
    confirmStage: (id: number, stage: 1 | 2, counted: number, bonus: number, key: string) =>
      post<{ application: Application }>(`/api/admin/onboarding/${id}/stage`, { stage, counted, bonus, idempotency_key: key }),
    templates: () => request<{ items: BroadcastTemplate[] }>("/api/admin/broadcast-templates"),
    saveTemplate: (title: string, html: string, id?: number) =>
      id ? patch<{ template: BroadcastTemplate }>(`/api/admin/broadcast-templates/${id}`, { title, html })
        : post<{ template: BroadcastTemplate }>("/api/admin/broadcast-templates", { title, html }),
    deleteTemplate: (id: number) => post<{ deleted: boolean }>(`/api/admin/broadcast-templates/${id}/delete`, {}),
    receipts: (id: number) => request<{ broadcast: Broadcast; items: Receipt[] }>(`/api/admin/broadcasts/${id}`),
    remind: (id: number, userIds: number[] | null) =>
      post<{ sent: number }>(`/api/admin/broadcasts/${id}/remind`, { user_ids: userIds }),
    exportXlsx: (date_from: string, date_to: string, user_id?: number) =>
      post<{ ok: boolean }>("/api/admin/export", { date_from, date_to, user_id }),
    reports: (kind: QueueKind, day?: string) =>
      request<{ items: QueueItem[]; counts: ReportCounts }>(`/api/admin/reports?kind=${kind}${day ? `&day=${day}` : ""}`),
    dayActuals: () => request<{ items: DayActual[]; missing: string[] }>("/api/admin/day-actuals"),
    setDayActual: (day: string, actual: number, key: string) =>
      post<{ changed: boolean; met: boolean; approved: number; fined: { shift_id: number; amount: number }[] }>(
        `/api/admin/day-actuals/${day}`, { actual, idempotency_key: key }),
    annulDayFines: (day: string, shiftIds: number[] | null, key: string) =>
      post<{ annulled: number }>(`/api/admin/day-actuals/${day}/annul`, { shift_ids: shiftIds, idempotency_key: key }),
    finances: (params: { userId?: number; status?: TxStatus; type?: TxType } = {}) => {
      const q = new URLSearchParams();
      if (params.userId) q.set("user_id", String(params.userId));
      if (params.status) q.set("status", params.status);
      if (params.type) q.set("type", params.type);
      return request<{ items: Tx[]; balance: Balance | null }>(`/api/admin/finances?${q}`);
    },
    financeSummary: () => request<{ people: MoneyPerson[]; pending: Tx[]; drafts: Tx[] }>("/api/admin/finances/summary"),
    createTx: (user_id: number, type: Exclude<TxType, "payout_offset">, amount: number, reason: string, key: string,
               media_file_id: string | null = null) =>
      post<{ tx: Tx; changed: boolean }>("/api/admin/finances", { user_id, type, amount, reason, media_file_id, idempotency_key: key }),
    decideTx: (id: number, approve: boolean, comment: string | null, key: string) =>
      post<{ tx: Tx; changed: boolean }>(`/api/admin/finances/${id}/decide`, { approve, comment, idempotency_key: key }),
    /** «Простить» a fine: the reason is optional */
    forgiveTx: (id: number, reason: string | null, key: string) =>
      post<{ tx: Tx; changed: boolean }>(`/api/admin/finances/${id}/forgive`, { reason, idempotency_key: key }),
    cancelTx: (id: number, reason: string, key: string) =>
      post<{ tx: Tx; changed: boolean }>(`/api/admin/finances/${id}/cancel`, { reason, idempotency_key: key }),
    fillTx: (id: number, amount: number, key: string) =>
      post<{ tx: Tx; changed: boolean }>(`/api/admin/finances/${id}/amount`, { amount, idempotency_key: key }),
    payAll: (user_id: number, key: string) =>
      post<{ paid: number; total: number }>("/api/admin/finances/pay-all", { user_id, idempotency_key: key }),
    events: () => request<{ items: LiveEvent[]; pending: PendingEntry[]; prize_types: string[]; hide_after_days: number }>("/api/admin/events"),
    createEvent: (
      body: { title: string; mode: LiveEvent["mode"]; starts_at: string; ends_at: string; description?: string; prize?: string;
        target?: number | null; max_winners: number; max_attempts: number; network_id?: number | null; prize_type?: string | null;
        prize_photo?: string | null; prize_media_kind?: "photo" | "video" },
      key: string,
    ) => post<{ event: LiveEvent }>("/api/admin/events", { ...body, idempotency_key: key }),
    updateEvent: (id: number, body: EventChanges & { version: number; notify: boolean }, key: string) =>
      patch<{ event: LiveEvent; changed: string[]; critical: string[]; finished: boolean; restarted: boolean }>(
        `/api/admin/events/${id}`, { ...body, idempotency_key: key }),
    eventVisibility: (id: number, hidden: boolean, key: string) =>
      post<{ event: LiveEvent }>(`/api/admin/events/${id}/visibility`, { hidden, idempotency_key: key }),
    deleteEvent: (id: number, key: string) => post<{ deleted: boolean }>(`/api/admin/events/${id}/delete`, { idempotency_key: key }),
    annulEntry: (id: number, reason: string, key: string) =>
      post<{ event: LiveEvent; changed: boolean; reopened: boolean }>(`/api/admin/event-entries/${id}/annul`, { reason, idempotency_key: key }),
    cancelEvent: (id: number, key: string) => post<{ event: LiveEvent }>(`/api/admin/events/${id}/cancel`, { idempotency_key: key }),
    reviewEntry: (id: number, approve: boolean, reason: string | null, key: string) =>
      post<{ event: LiveEvent; changed: boolean }>(`/api/admin/event-entries/${id}/review`, { approve, reason, idempotency_key: key }),
    goals: () => request<{ items: Goal[]; prize_types: string[]; hide_after_days: number }>("/api/admin/goals"),
    createGoal: (
      body: { title: string; kind: Goal["kind"]; mode: Goal["mode"]; target: number; date_from: string; date_to: string;
        description?: string; user_id?: number | null; network_id?: number | null; prize?: string | null; prize_type?: string | null;
        prize_photo?: string | null; user_ids?: number[]; location?: string; reset_time?: string; forever?: boolean },
      key: string,
    ) => post<{ goal: Goal }>("/api/admin/goals", { ...body, idempotency_key: key }),
    updateGoal: (id: number, body: GoalChanges & { version: number; notify: boolean }, key: string) =>
      patch<{ goal: Goal; changed: string[]; critical: string[] }>(`/api/admin/goals/${id}`, { ...body, idempotency_key: key }),
    goalVisibility: (id: number, hidden: boolean, key: string) =>
      post<{ goal: Goal }>(`/api/admin/goals/${id}/visibility`, { hidden, idempotency_key: key }),
    deleteGoal: (id: number, key: string) => post<{ deleted: boolean }>(`/api/admin/goals/${id}/delete`, { idempotency_key: key }),
    revokeGoal: (id: number, key: string) => post<{ goal: Goal; revoked: number }>(`/api/admin/goals/${id}/revoke`, { idempotency_key: key }),
    annulGoal: (id: number, user_id: number, reason: string, key: string) =>
      post<{ goal: Goal; changed: boolean }>(`/api/admin/goals/${id}/annul`, { user_id, reason, idempotency_key: key }),
    networks: () => request<{ items: Network[] }>("/api/admin/networks"),
    createNetwork: (name: string, key: string) => post<{ network: Network }>("/api/admin/networks", { name, idempotency_key: key }),
    updateNetwork: (id: number, body: {
      name?: string; trainer_id?: number; clear_trainer?: boolean; archive?: boolean;
      rate_base?: number | null; clear_rate?: boolean; ladder?: LadderStep[]; ladder_period?: "day" | "week";
      ladder_counts_trainer?: boolean;
    }, key: string) =>
      patch<{ network: Network }>(`/api/admin/networks/${id}`, { ...body, idempotency_key: key }),
    setMemberNetwork: (userId: number, networkId: number | null, key: string) =>
      post<{ user: User; changed: boolean }>(`/api/admin/team/${userId}/network`, { network_id: networkId, idempotency_key: key }),
    userPaymentProfile: (userId: number) =>
      request<{ profile: PaymentProfile | null; full: boolean; banks: string[] }>(`/api/admin/users/${userId}/payment-profile`),
    uploadMedia: (file: Blob) => upload<{ file_id: string }>("/api/admin/media", file),
    payouts: () => request<{ items: { id: number; title: string; period_from: string; period_to: string; total: number; people: number; created_at: string }[]; rates: PayoutRates }>("/api/admin/payouts"),
    payout: (id: number) => request<{ batch: PayoutBatch; bank_text: string }>(`/api/admin/payouts/${id}`),
    payoutExcel: (id: number) => post<{ ok: boolean }>(`/api/admin/payouts/${id}/excel`, {}),
    previewPayout: (body: { date_from: string; date_to: string; network_id: number | null; rates: PayoutRates }) =>
      post<{ rows: PayoutRow[]; rates: PayoutRates; summary: PayoutSummary }>("/api/admin/payouts/preview", body),
    setUserRates: (userId: number, body: { personal_rate?: number | null; clear_personal_rate?: boolean; lazy_exempt?: boolean },
                   key: string) => post<{ user: User }>(`/api/admin/team/${userId}/rates`, { ...body, idempotency_key: key }),
    pieceDeduction: (body: { user_id: number; day: string; pieces: number; reason: string; media_file_id: string | null },
                     key: string) => post<{ items: Tx[] }>("/api/admin/finances/pieces", { ...body, idempotency_key: key }),
    commitPayout: (
      body: {
        title: string;
        date_from: string;
        date_to: string;
        network_id: number | null;
        rates: PayoutRates;
        choices: { user_id: number; include: boolean; base_override: number | null }[];
        proof: string[];
        deduction_proof: string[];
      },
      key: string,
    ) => post<{ batch: PayoutBatch; bank_text: string }>("/api/admin/payouts", { ...body, idempotency_key: key }),
    reportCounts: () => request<ReportCounts>("/api/admin/reports/counts"),
    violations: (status: "pending" | "resolved") =>
      request<{ items: Violation[]; pending: number; quota: number; personal: boolean }>(`/api/admin/violations?status=${status}`),
    scanViolations: () => post<{ created: number }>("/api/admin/violations/scan", {}),
    fineViolation: (id: number, body: { amount: number; comment: string; media_files: string[] }, key: string) =>
      post<{ violation: Violation; changed: boolean }>(`/api/admin/violations/${id}/fine`, { ...body, idempotency_key: key }),
    waiveViolation: (id: number, body: { comment: string; media_files: string[] }, key: string) =>
      post<{ violation: Violation; changed: boolean }>(`/api/admin/violations/${id}/waive`, { ...body, idempotency_key: key }),
    review: (shiftId: number, action: "approve" | "return" | "fine", extra: { amount?: number; comment?: string }, key: string) =>
      post<ShiftResponse>(`/api/admin/shifts/${shiftId}/review`, { action, ...extra, idempotency_key: key }),
    noShow: (shiftId: number, action: "fine" | "excuse", extra: { amount?: number; comment?: string }, key: string) =>
      post<ShiftResponse>(`/api/admin/shifts/${shiftId}/no-show`, { action, ...extra, idempotency_key: key }),
    manualResult: (shiftId: number, fact_volume: number, comment: string, photo: Blob | null, key: string) =>
      upload<ShiftResponse>(
        `/api/admin/shifts/${shiftId}/result?fact_volume=${fact_volume}&idempotency_key=${key}&comment=${encodeURIComponent(comment)}`,
        photo,
      ),
    team: () => request<{ items: User[]; bot_username: string | null }>("/api/admin/team"),
    setRole: (userId: number, role: Exclude<Role, "owner">, key: string) =>
      post<{ user: User; changed: boolean }>(`/api/admin/team/${userId}/role`, { role, idempotency_key: key }),
    setAccess: (userId: number, allow: boolean, key: string) =>
      post<{ user: User; changed: boolean }>(`/api/admin/team/${userId}/access`, { allow, idempotency_key: key }),
    setState: (userId: number, state: User["account_state"], reason: string | null, key: string) =>
      post<{ user: User; changed: boolean }>(`/api/admin/team/${userId}/state`, { state, reason, idempotency_key: key }),
    raspred: (when: "today" | "tomorrow" = "tomorrow") => request<Raspred>(`/api/admin/raspred/${when}`),
    raspredOn: (date: string) => request<Raspred>(`/api/admin/raspred?date=${date}`),
    setNorm: (userId: number, norm: number | null, key: string) =>
      post<{ user: User }>(`/api/admin/team/${userId}/norm`, { norm, idempotency_key: key }),
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
