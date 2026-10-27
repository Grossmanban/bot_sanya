"use client";

import { useMemo, useState, type CSSProperties } from "react";
import { Check, Copy, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type AdminShift, type Capability, type StatsItem, type User } from "@/lib/api";
import {
  STATUS_META,
  WEEKDAYS_SHORT,
  addDays,
  dayMonth,
  parseDay,
  plural,
  rub,
  timeRange,
  WEEKDAYS,
} from "@/lib/format";
import { moscowToday, useLoad } from "@/lib/hooks";
import { haptic, tapHaptic } from "@/lib/telegram";
import { CapsContext } from "@/lib/caps";
import { readSession, rememberScroll, useScrollMemory, writeSession } from "@/lib/scroll";
import { cn } from "@/lib/utils";
import { DayLanes, DayTrack, type Lane } from "../day-line";
import { AdminShiftSheet, type EditableShift } from "./admin-shift-sheet";
import { TeamSection } from "./team-section";
import { PlanFactBar } from "./shortfall";
import { ReportsSection } from "./reports";
import { DayActualSection } from "./day-actual-section";
import { FinanceSection } from "../finance/finance-section";
import { NetworksSection } from "./networks-section";
import { PayoutsSection } from "../finance/payouts-section";
import { GoalsSection } from "../goals/goals";
import { EventsAdmin } from "../events/events-admin";
import { ExportCard, PointsSection, SettingsSection } from "./admin-extra";
import { BroadcastSection } from "./broadcast-section";
import { ViolationsSection } from "./violations-section";
import { OnboardingSection } from "./onboarding-section";
import { AccessSection } from "./access-section";
import { GroupsSection } from "./groups-section";

type Section = "groups" | "access" | "onboarding" | "discipline" | "events" | "goals" | "payouts" | "networks" | "raspred" | "reports" | "actual" | "day" | "stats" | "penalties" | "team" | "points" | "broadcast" | "settings";
// need: the capability that unlocks the section (trainers get a smaller, network-scoped panel)
const SECTIONS: { id: Section; label: string; need: Capability }[] = [
  { id: "raspred", label: "Распред", need: "raspred" },
  { id: "onboarding", label: "Онбординг", need: "registrations" },
  { id: "reports", label: "Отчёты", need: "review" },
  { id: "discipline", label: "Дисциплина", need: "review" },
  { id: "actual", label: "Факт дня", need: "global_actual" },
  { id: "day", label: "День", need: "schedule" },
  { id: "stats", label: "Отчёт", need: "review" },
  { id: "penalties", label: "Финансы", need: "review" },
  { id: "payouts", label: "Выплаты", need: "money" },
  { id: "team", label: "Команда", need: "pause" },
  { id: "goals", label: "Цели", need: "review" },
  { id: "events", label: "События", need: "review" },
  { id: "networks", label: "Сети", need: "registrations" },
  { id: "points", label: "Точки", need: "points" },
  { id: "groups", label: "Группы", need: "groups" },
  { id: "access", label: "Доступ", need: "access_log" },
  { id: "broadcast", label: "Рассылка", need: "broadcast_network" },
  { id: "settings", label: "Настройки", need: "settings" },
];

const field = "h-12 w-full rounded-2xl border-0 bg-muted px-4 outline-none focus:ring-2 focus:ring-ring/25";

export function AdminScreen({ me, caps, network }: { me: User; caps: Capability[]; network?: string | null }) {
  const sections = SECTIONS.filter((s) => caps.includes(s.need));
  // the open section and its scroll survive a reload and switching tabs
  const [picked, setPicked] = useState<Section | null>(() => readSession("admin-section") as Section | null);
  const section: Section = sections.some((s) => s.id === picked) ? picked! : sections[0]?.id ?? "day";
  const setSection = (s: Section) => {
    rememberScroll();
    setPicked(s);
    writeSession("admin-section", s);
  };
  useScrollMemory(`admin:${section}`);
  const [editing, setEditing] = useState<EditableShift | null>(null);
  const [version, setVersion] = useState(0);
  const counts = useLoad(() => api.admin.reportCounts(), [version, section]);
  const waiting = (counts.data?.review ?? 0) + (counts.data?.no_show ?? 0);
  const money = (counts.data?.fines_pending ?? 0) + (counts.data?.fines_draft ?? 0);

  return (
    <CapsContext.Provider value={caps}>
    <div className="mx-auto max-w-3xl space-y-5">
      <nav className="no-scrollbar -mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
        <div className="flex w-max gap-1 rounded-2xl bg-card p-1 shadow-lift lg:w-full lg:flex-wrap">
          {sections.map((s) => (
            <button
              key={s.id}
              ref={(el) => {
                if (el && section === s.id) el.scrollIntoView({ block: "nearest", inline: "nearest" });
              }}
              onClick={() => {
                tapHaptic();
                setSection(s.id);
              }}
              className={cn(
                "press inline-flex h-10 shrink-0 items-center justify-center rounded-xl px-4 text-sm font-semibold whitespace-nowrap transition-colors duration-200",
                section === s.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {s.label}
              {s.id === "actual" && (counts.data?.actual_missing ?? 0) > 0 && (
                <span className={cn("ml-1.5 inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
                  section === s.id ? "bg-white/20" : "bg-wait text-white")}>
                  {counts.data?.actual_missing}
                </span>
              )}
              {s.id === "penalties" && money > 0 && (
                <span className={cn("ml-1.5 inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
                  section === s.id ? "bg-white/20" : "bg-wait text-white")}>
                  {money}
                </span>
              )}
              {s.id === "discipline" && (counts.data?.violations ?? 0) > 0 && (
                <span className={cn("ml-1.5 inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
                  section === s.id ? "bg-white/20" : "bg-cancel text-white")}>
                  {counts.data?.violations}
                </span>
              )}
              {s.id === "reports" && waiting > 0 && (
                <span className={cn("ml-1.5 inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
                  section === s.id ? "bg-white/20" : "bg-cancel text-white")}>
                  {waiting}
                </span>
              )}
            </button>
          ))}
        </div>
      </nav>

      <div key={section} className="screen-in">
        {section === "raspred" && <RaspredSection version={version} onEdit={setEditing} />}
        {section === "onboarding" && <OnboardingSection me={me} />}
        {section === "reports" && <ReportsSection onChanged={() => setVersion((v) => v + 1)} />}
        {section === "discipline" && <ViolationsSection onChanged={() => setVersion((v) => v + 1)} />}
        {section === "actual" && <DayActualSection onChanged={() => setVersion((v) => v + 1)} />}
        {section === "day" && <DaySection version={version} onEdit={setEditing} />}
        {section === "stats" && <StatsSection version={version} onEdit={setEditing} />}
        {section === "penalties" && (
          <FinanceSection onChanged={() => setVersion((v) => v + 1)} />
        )}
        {section === "team" && <TeamSection me={me} caps={caps} network={network} />}
        {section === "networks" && <NetworksSection />}
        {section === "payouts" && <PayoutsSection />}
        {section === "goals" && <GoalsSection me={me} />}
        {section === "events" && <EventsAdmin me={me} />}
        {section === "points" && <PointsSection />}
        {section === "access" && <AccessSection />}
        {section === "groups" && <GroupsSection />}
        {section === "broadcast" && <BroadcastSection me={me} />}
        {section === "settings" && <SettingsSection />}
      </div>

      <AdminShiftSheet
        shift={editing}
        onClose={() => setEditing(null)}
        onChanged={() => {
          setEditing(null);
          setVersion((v) => v + 1);
        }}
      />
    </div>
    </CapsContext.Provider>
  );
}

type EditFn = (s: EditableShift) => void;

const lanesFrom = (rows: { name: string; start_time: string | null; end_time: string | null; location?: string | null; status?: AdminShift["status"] }[]): Lane[] =>
  rows.map((r, i) => ({
    key: String(i),
    name: r.name.split(" ")[0],
    start_time: r.start_time,
    end_time: r.end_time,
    state: r.status ?? "active",
    highlight: true,
    caption: r.location ?? undefined,
  }));

function RaspredSection({ version, onEdit }: { version: number; onEdit: EditFn }) {
  const today = moscowToday();
  const tomorrow = addDays(today, 1);
  // ТЗ №21: «На сегодня», «На завтра» or any day from the calendar
  const [day, setDay] = useState(tomorrow);
  const { data, loading, reload } = useLoad(() => api.admin.raspredOn(day), [version, day]);
  const [copied, setCopied] = useState(false);
  const other = day !== today && day !== tomorrow;

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    haptic("success");
    setCopied(true);
    toast.success("Распред скопирован");
    setTimeout(() => setCopied(false), 1800);
  }

  const tab = (active: boolean) =>
    cn("press relative h-10 flex-1 rounded-xl text-sm font-semibold transition-colors",
      active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground");
  const tabs = (
    <div className="flex gap-1 rounded-2xl bg-card p-1 shadow-lift">
      {[today, tomorrow].map((d) => (
        <button key={d} onClick={() => { tapHaptic(); setDay(d); }} className={tab(day === d)}>
          {d === today ? "На сегодня" : "На завтра"}
        </button>
      ))}
      <label className={cn(tab(other), "grid cursor-pointer place-items-center")}>
        <span className="pointer-events-none truncate px-2">{other ? `📅 ${dayMonth(day)}` : "📅 Дата"}</span>
        <input type="date" aria-label="Распред на другую дату" value={day}
               onChange={(e) => { if (e.target.value) { tapHaptic(); setDay(e.target.value); } }}
               className="absolute inset-0 cursor-pointer opacity-0" />
      </label>
    </div>
  );
  const caption = day === today ? "Сегодня" : day === tomorrow ? "Завтра"
    : `${WEEKDAYS[parseDay(day).weekday]}${day < today ? " · прошедший день" : ""}`;
  if (!data || data.date !== day)
    return <div className="space-y-4">{tabs}<Skeleton className="h-72 rounded-3xl" /></div>;
  return (
    <div className="space-y-4">
      {tabs}
      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm text-muted-foreground">{caption}</p>
            <h2 className="font-display text-2xl font-semibold">{dayMonth(data.date)}</h2>
            <p className="mt-1 text-[15px] text-muted-foreground">
              {data.total_count
                ? `${data.total_count} ${plural(data.total_count, "человек", "человека", "человек")}, ${data.total_volume} шт.`
                : "Пока никто не выходит"}
            </p>
          </div>
          <button onClick={reload} aria-label="Обновить" className="press grid size-10 place-items-center rounded-full bg-muted">
            <RefreshCw className={cn("size-4", loading && "animate-spin")} />
          </button>
        </div>
        {data.rows.length > 0 && (
          <div className="mt-5">
            <DayLanes lanes={lanesFrom(data.rows)} />
          </div>
        )}
        {data.rows.length > 0 && (
          <ul className="mt-5 divide-y border-t">
            {data.rows.map((r) => (
              <li key={r.shift_id}>
                <button
                  className="press flex w-full items-center justify-between gap-3 py-3 text-left"
                  onClick={async () => {
                    const day = await api.admin.day(data.date);
                    const s = day.items.find((x) => x.id === r.shift_id);
                    if (s) onEdit({ ...s });
                  }}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">
                      {r.name}
                      {r.username && <span className="font-normal text-muted-foreground"> @{r.username}</span>}
                      {r.trainee && <span className="ml-1.5 rounded-md bg-wait-soft px-1.5 py-0.5 text-xs font-semibold text-wait">стажёр</span>}
                    </span>
                    <span className="text-sm text-muted-foreground">{r.location}</span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block text-sm font-semibold tabular">{r.start_time}–{r.end_time}</span>
                    <span className="text-sm text-muted-foreground">{r.volume} шт.</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h3 className="font-semibold">Сообщение для рабочего чата</h3>
        <div className="mt-3 max-w-md rounded-3xl rounded-bl-lg bg-muted px-4 py-3 text-[15px] leading-relaxed whitespace-pre-wrap">
          {data.text}
        </div>
        <Button className="press mt-4 h-12 w-full rounded-2xl text-[15px] font-semibold md:w-auto md:px-6" onClick={() => copy(data.text)}>
          {copied ? <Check key="ok" className="pop" /> : <Copy />}
          {copied ? "Скопировано" : "Скопировать"}
        </Button>
        {day === tomorrow && <p className="mt-3 text-sm text-muted-foreground">В боте тот же текст по команде /raspred_zavtra.</p>}
      </section>
    </div>
  );
}

function DaySection({ version, onEdit }: { version: number; onEdit: EditFn }) {
  const today = moscowToday();
  const [day, setDay] = useState(() => addDays(today, 1));
  const { data, loading } = useLoad(() => api.admin.day(day), [day, version]);
  const chips = [
    { label: "Сегодня", value: today },
    { label: "Завтра", value: addDays(today, 1) },
    { label: "Послезавтра", value: addDays(today, 2) },
  ];
  const { weekday } = parseDay(day);
  const going = data?.items.filter((s) => s.status === "active" || s.status === "restored") ?? [];

  return (
    <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div className="flex flex-wrap gap-2">
        {chips.map((c) => (
          <button key={c.label} onClick={() => setDay(c.value)}
                  className={cn("press rounded-full px-4 py-2 text-sm font-semibold", day === c.value ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
            {c.label}
          </button>
        ))}
        <input type="date" aria-label="Другая дата" className="h-9 rounded-full border-0 bg-muted px-4 text-sm outline-none"
               value={day} onChange={(e) => e.target.value && setDay(e.target.value)} />
      </div>
      <h2 className="mt-5 font-display text-2xl font-semibold">{WEEKDAYS[weekday]}, {dayMonth(day)}</h2>
      <p className="mt-1 text-[15px] text-muted-foreground">
        {data ? `Выходят ${data.total_count} ${plural(data.total_count, "человек", "человека", "человек")}, ${data.total_volume} шт.` : " "}
      </p>

      {loading && !data ? (
        <Skeleton className="mt-5 h-32 rounded-2xl" />
      ) : data && data.items.length === 0 ? (
        <p className="mt-5 rounded-2xl bg-muted p-4 text-sm text-muted-foreground">На эту дату записей нет.</p>
      ) : (
        <>
          {going.length > 0 && (
            <div className="mt-5">
              <DayLanes lanes={lanesFrom(going)} />
            </div>
          )}
          <ul className="mt-5 divide-y border-t">
            {data?.items.map((s) => {
              const meta = STATUS_META[s.status];
              return (
                <li key={s.id}>
                  <button onClick={() => onEdit({ ...s })} className="press flex w-full items-center justify-between gap-3 py-3 text-left">
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{s.name}</span>
                      <span className={cn("flex items-center gap-1.5 text-sm", meta.text)}>
                        <span className={cn("size-1.5 rounded-full", meta.dot)} />
                        {meta.label}
                      </span>
                    </span>
                    <span className="shrink-0 text-right text-sm">
                      <span className="block font-semibold tabular">{s.start_time ? timeRange(s) : ""}</span>
                      <span className="text-muted-foreground">{s.location ? `${s.location}, ${s.volume} шт.` : ""}</span>
                      {s.fact_volume != null && (
                        <span className={cn("block text-xs font-semibold", s.shortfall ? "text-cancel" : "text-go")}>
                          факт {s.fact_volume}{s.shortfall ? ` (−${s.shortfall})` : ""}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

function UserPicker({ users, value, onChange }: { users: User[]; value: User | null; onChange: (u: User | null) => void }) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase().replace(/^@/, "");
    if (!s) return users;
    return users.filter((u) => `${u.first_name} ${u.last_name} ${u.last_name} ${u.first_name} ${u.username ?? ""}`.toLowerCase().includes(s));
  }, [q, users]);

  if (value) {
    return (
      <div className="flex items-center justify-between rounded-2xl bg-muted px-4 py-3">
        <div className="min-w-0">
          <p className="truncate font-semibold">{value.first_name} {value.last_name}</p>
          {value.username && <p className="text-sm text-muted-foreground">@{value.username}</p>}
        </div>
        <button className="text-sm font-semibold" onClick={() => onChange(null)}>Сменить</button>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="absolute top-1/2 left-4 size-4 -translate-y-1/2 text-muted-foreground" />
        <input className={cn(field, "pl-11")} placeholder="Имя, фамилия или @username" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <ul className="max-h-60 divide-y overflow-y-auto rounded-2xl border">
        {filtered.map((u) => (
          <li key={u.id}>
            <button className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-muted/60" onClick={() => onChange(u)}>
              <span className="font-medium">{u.first_name} {u.last_name}</span>
              <span className="text-sm text-muted-foreground">{u.username ? `@${u.username}` : ""}</span>
            </button>
          </li>
        ))}
        {filtered.length === 0 && <li className="px-4 py-3 text-sm text-muted-foreground">Никого не нашли</li>}
      </ul>
    </div>
  );
}

function presets(today: string) {
  const { weekday, y, m } = parseDay(today);
  const monday = addDays(today, -weekday);
  const monthStart = `${y}-${String(m).padStart(2, "0")}-01`;
  const nextMonth = m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, "0")}-01`;
  const prevMonth = m === 1 ? `${y - 1}-12-01` : `${y}-${String(m - 1).padStart(2, "0")}-01`;
  return [
    { label: "Сегодня", from: today, to: today },
    { label: "Эта неделя", from: monday, to: addDays(monday, 6) },
    { label: "Прошлая неделя", from: addDays(monday, -7), to: addDays(monday, -1) },
    { label: "Этот месяц", from: monthStart, to: addDays(nextMonth, -1) },
    { label: "Прошлый месяц", from: prevMonth, to: addDays(monthStart, -1) },
  ];
}

function StatsSection({ version, onEdit }: { version: number; onEdit: EditFn }) {
  const users = useLoad(() => api.admin.users(), [version]);
  const [user, setUser] = useState<User | null>(null);
  const ranges = useMemo(() => presets(moscowToday()), []);
  const [range, setRange] = useState(ranges[1]);
  const stats = useLoad(
    () => (user ? api.admin.stats(user.id, range.from, range.to) : Promise.resolve(null)),
    [user?.id, range.from, range.to, version],
  );
  const t = stats.data?.totals;

  return (
    <div className="space-y-4">
      <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        {users.data ? <UserPicker users={users.data.items} value={user} onChange={setUser} /> : <Skeleton className="h-12 rounded-2xl" />}
        <div className="flex flex-wrap gap-2">
          {ranges.map((r) => (
            <button key={r.label} onClick={() => setRange(r)}
                    className={cn("press rounded-full px-4 py-2 text-sm font-semibold", range.label === r.label ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
              {r.label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1.5">
            <span className="text-sm text-muted-foreground">С</span>
            <input type="date" className={field} value={range.from} onChange={(e) => e.target.value && setRange({ label: "", from: e.target.value, to: range.to })} />
          </label>
          <label className="space-y-1.5">
            <span className="text-sm text-muted-foreground">По</span>
            <input type="date" className={field} value={range.to} onChange={(e) => e.target.value && setRange({ label: "", from: range.from, to: e.target.value })} />
          </label>
        </div>
      </section>

      <ExportCard from={range.from} to={range.to} user={user} />

      {stats.error && <p className="text-sm text-destructive">{stats.error.message}</p>}
      {user && t && stats.data && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <div className="p-5 md:p-6">
            <p className="text-sm text-muted-foreground">План за период</p>
            <p className="font-display text-3xl font-semibold">{t.active_volume} шт.</p>
            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-[15px] md:grid-cols-4">
              <Fact label="Факт">
                {t.fact_volume} шт.
                {t.fact_missing > 0 && <span className="block text-xs font-normal text-wait">без факта: {t.fact_missing}</span>}
              </Fact>
              <Fact label="Выходов">{t.active_shifts} из {t.shifts}</Fact>
              <Fact label="Заработок, план">{rub(t.expected_pay)}</Fact>
              <Fact label="Заработок, факт">{rub(t.fact_pay)}</Fact>
              <Fact label="Отменено">{t.cancelled_volume} шт.</Fact>
              <Fact label="Штрафы">{rub(t.penalties)}</Fact>
              <Fact label="Не по норме">
                <span className={t.shortfall_shifts ? "text-cancel" : ""}>
                  {t.shortfall_shifts ? `${t.shortfall_shifts} ${plural(t.shortfall_shifts, "смена", "смены", "смен")}, −${t.shortfall_volume} шт.` : "нет"}
                </span>
                {t.shortfall_pending > 0 && <span className="block text-xs font-normal text-wait">ждут решения: {t.shortfall_pending}</span>}
              </Fact>
            </dl>
          </div>
          {stats.data.items.length === 0 ? (
            <p className="border-t px-5 py-4 text-sm text-muted-foreground md:px-6">За этот период записей нет.</p>
          ) : (
            <div className="divide-y border-t">
              {stats.data.items.map((i, idx) => (
                <StatsRow key={i.id} item={i} index={idx}
                          onClick={() => onEdit({ ...i, name: stats.data!.user.name, username: stats.data!.user.username })} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function StatsRow({ item, index, onClick }: { item: StatsItem; index: number; onClick: () => void }) {
  const meta = STATUS_META[item.status];
  const { d, weekday } = parseDay(item.date);
  return (
    <button onClick={onClick} style={{ "--i": index } as CSSProperties}
            className="row-in grid w-full grid-cols-[3rem_1fr] gap-4 px-4 py-3.5 text-left hover:bg-muted/60 md:px-5">
      <span className="flex flex-col items-center">
        <span className="text-xs text-muted-foreground">{WEEKDAYS_SHORT[weekday]}</span>
        <span className="font-display text-lg font-semibold">{d}</span>
      </span>
      <span className="min-w-0">
        <span className="flex items-baseline justify-between gap-3">
          <span className={cn("flex items-center gap-2 text-sm font-semibold", meta.text)}>
            <span className={cn("size-2 rounded-full", meta.dot)} />
            {meta.label}
          </span>
          {item.start_time && <span className="text-sm font-semibold tabular">{timeRange(item)}</span>}
        </span>
        {item.shortfall > 0 && item.volume && item.fact_volume != null ? (
          <PlanFactBar className="mt-2.5" plan={item.volume} fact={item.fact_volume} />
        ) : (
          <DayTrack className="mt-2.5" state={item.status} mine={item.start_time ? item : null} others={[]} index={index} />
        )}
        <span className="mt-2 block truncate text-xs text-muted-foreground">
          {[
            item.location && `${item.location}, план ${item.volume} шт.`,
            item.fact_volume != null && `факт ${item.fact_volume} шт.`,
            item.notes.toLowerCase(),
          ]
            .filter(Boolean)
            .join(", ")}
        </span>
      </span>
    </button>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 font-semibold">{children}</dd>
    </div>
  );
}
