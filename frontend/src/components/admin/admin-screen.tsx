"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Check, ChevronRight, Copy, LoaderCircle, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type AdminDay, type AdminShift, type Capability, type Product, type ReportCounts, type StatsItem, type UnmarkedPerson, type User } from "@/lib/api";
import { TONE_CHIP } from "@/lib/tone";
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
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { haptic, openProfile, tapHaptic } from "@/lib/telegram";
import { CapsContext, ProductsContext } from "@/lib/caps";
import { PENDING_LABELS, refreshPending, sectionCount, sectionOf, usePending } from "@/lib/pending";
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
import { UnmarkedFineDrawer, ViolationsSection } from "./violations-section";
import { OnboardingSection } from "./onboarding-section";
import { AccessSection } from "./access-section";
import { GroupsSection } from "./groups-section";
import { AliceAdmin } from "./alice-admin";
import { ManualsAdmin } from "../training/manuals";

type Section = "decisions" | "manuals" | "alice" | "plus" | "groups" | "access" | "onboarding" | "discipline" | "events" | "goals" | "payouts" | "networks" | "raspred" | "reports" | "actual" | "day" | "stats" | "penalties" | "team" | "points" | "broadcast" | "settings";
// need: the capability that unlocks the section (trainers get a smaller, network-scoped panel)
const SECTIONS: { id: Section; label: string; need: Capability }[] = [
  { id: "decisions", label: "Решения", need: "review" },
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
  { id: "alice", label: "Алиса", need: "alice" },
  { id: "plus", label: "Яндекс Плюс", need: "alice" },
  { id: "manuals", label: "Методички", need: "manuals" },
  { id: "groups", label: "Группы", need: "groups" },
  { id: "access", label: "Доступ", need: "access_log" },
  { id: "broadcast", label: "Рассылка", need: "broadcast_network" },
  { id: "settings", label: "Настройки", need: "settings" },
];

// ТЗ 28.10 §27 (согласовано 29.10): the 20+ sections are grouped; «Решения» stands first, on its own
type Group = "decisions" | "people" | "shifts" | "money" | "products" | "comms";
const GROUPS: { id: Group; label: string; sections: Section[] }[] = [
  { id: "decisions", label: "Решения", sections: ["decisions"] },
  { id: "people", label: "Люди", sections: ["team", "onboarding", "groups", "access"] },
  { id: "shifts", label: "Смены", sections: ["raspred", "day", "reports", "discipline", "actual", "stats"] },
  { id: "money", label: "Деньги", sections: ["penalties", "payouts"] },
  { id: "products", label: "Продукты", sections: ["alice", "plus", "manuals", "goals", "events"] },
  { id: "comms", label: "Связь и настройки", sections: ["broadcast", "points", "networks", "settings"] },
];
// «Решения» is shown to whoever decides anything at all (the same capabilities pending.counts looks at)
const DECIDES: Capability[] = ["review", "schedule", "registrations", "global_actual", "approve_fines", "fine_direct",
  "profiles", "money", "alice"];
const RED: Section[] = ["reports", "discipline"];
const groupOf = (s: Section): Group => GROUPS.find((g) => g.sections.includes(s))!.id;

const field = "h-12 w-full rounded-2xl border-0 bg-muted px-4 outline-none focus:ring-2 focus:ring-ring/25";

export function AdminScreen({ me, caps, network, manageable = [] }: { me: User; caps: Capability[]; network?: string | null;
  manageable?: Product[] }) {
  const sections = SECTIONS.filter((s) => (s.id === "decisions" ? DECIDES.some((c) => caps.includes(c)) : caps.includes(s.need)));
  const visible = new Set(sections.map((s) => s.id));
  const groups = GROUPS.map((g) => ({ ...g, sections: g.sections.filter((s) => visible.has(s)) })).filter((g) => g.sections.length > 0);
  // the open section and its scroll survive a reload and switching tabs
  const [picked, setPicked] = useState<Section | null>(() => readSession("admin-section") as Section | null);
  const section: Section = sections.some((s) => s.id === picked) ? picked! : sections[0]?.id ?? "day";
  const setSection = (s: Section) => {
    rememberScroll();
    setPicked(s);
    writeSession("admin-section", s);
    writeSession(`admin-group:${groupOf(s)}`, s);
  };
  // a group opens on the section last used in it
  const openGroup = (g: (typeof groups)[number]) => {
    const last = readSession(`admin-group:${g.id}`) as Section | null;
    setSection(last && g.sections.includes(last) ? last : g.sections[0]);
  };
  const group = groups.find((g) => g.sections.includes(section));
  useScrollMemory(`admin:${section}`);
  const [editing, setEditing] = useState<EditableShift | null>(null);
  const [version, setVersion] = useState(0);
  // ТЗ 28.10 §14: one shared, auto-refreshing set of counts (the bottom tab shows their sum)
  const counts = usePending(true);
  useEffect(() => {
    void refreshPending();
  }, [version, section]);
  // ТЗ 29.10 §2: the active group / section pill is brought into view only when the section changes, and only by
  // scrolling its own horizontal strip. (A scrollIntoView in a ref ran on every re-render — the 30-second counts, any
  // action — and scrolled the whole page back up to this menu.)
  const groupStrip = useRef<HTMLDivElement>(null);
  const sectionStrip = useRef<HTMLDivElement>(null);
  useEffect(() => {
    for (const strip of [groupStrip.current, sectionStrip.current]) revealInStrip(strip);
  }, [section]);

  return (
    <CapsContext.Provider value={caps}>
    <ProductsContext.Provider value={manageable}>
    <div className="mx-auto max-w-3xl space-y-5">
      <nav className="space-y-2">
        <div ref={groupStrip} className="no-scrollbar -mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
          <div className="flex w-max gap-1 rounded-2xl bg-card p-1 shadow-lift lg:w-full lg:flex-wrap">
            {groups.map((g) => {
              const active = group?.id === g.id;
              const n = g.id === "decisions" ? counts?.total ?? 0 : g.sections.reduce((sum, s) => sum + sectionCount(counts, s), 0);
              const red = (g.id === "decisions" ? RED : g.sections).some((s) => RED.includes(s) && sectionCount(counts, s) > 0);
              return (
                <button
                  key={g.id}
                  data-active={active || undefined}
                  onClick={() => {
                    tapHaptic();
                    openGroup(g);
                  }}
                  className={cn(
                    "press inline-flex h-10 shrink-0 items-center justify-center rounded-xl px-4 text-sm font-semibold whitespace-nowrap transition-colors duration-200",
                    active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {g.label}
                  <Count n={n} active={active} red={red} />
                </button>
              );
            })}
          </div>
        </div>
        {group && group.sections.length > 1 && (
          <div ref={sectionStrip} className="no-scrollbar -mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
            <div className="flex w-max gap-1.5">
              {group.sections.map((id) => (
                <button
                  key={id}
                  data-active={section === id || undefined}
                  onClick={() => {
                    tapHaptic();
                    setSection(id);
                  }}
                  className={cn(
                    "press inline-flex h-9 shrink-0 items-center rounded-full px-3.5 text-sm font-semibold whitespace-nowrap transition-colors duration-200",
                    section === id ? "bg-foreground text-background" : "bg-muted text-muted-foreground hover:text-foreground",
                  )}
                >
                  {SECTIONS.find((s) => s.id === id)!.label}
                  <Count n={sectionCount(counts, id)} active={section === id} red={RED.includes(id)} />
                </button>
              ))}
            </div>
          </div>
        )}
      </nav>

      <div key={section} className="screen-in">
        {section === "decisions" && <DecisionsSection counts={counts} visible={visible} onOpen={setSection} />}
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
        {section === "alice" && <AliceAdmin key="alice" product="alice" />}
        {section === "plus" && <AliceAdmin key="plus" product="plus" />}
        {section === "manuals" && <ManualsAdmin />}
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
    </ProductsContext.Provider>
    </CapsContext.Provider>
  );
}

/** Scrolls a horizontal strip so its active pill is visible — the page itself never moves. */
function revealInStrip(strip: HTMLElement | null) {
  const el = strip?.querySelector<HTMLElement>("[data-active]");
  if (!strip || !el) return;
  const pad = 16;
  const left = el.offsetLeft - strip.offsetLeft;
  if (left - pad < strip.scrollLeft) strip.scrollTo({ left: Math.max(0, left - pad) });
  else if (left + el.offsetWidth + pad > strip.scrollLeft + strip.clientWidth)
    strip.scrollTo({ left: left + el.offsetWidth + pad - strip.clientWidth });
}

function Count({ n, active, red }: { n: number; active: boolean; red: boolean }) {
  if (n <= 0) return null;
  return (
    <span className={cn("ml-1.5 inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
      active ? "bg-white/20" : red ? "bg-cancel text-white" : "bg-wait text-white")}>
      {n > 99 ? "99+" : n}
    </span>
  );
}

/** §27 «Решения»: everything that waits for this person's decision, in one list (the same counts and words as the
 * bot's /pending). A row opens the section where it is decided; the «Ожидает согласования» blocks stay there too. */
function DecisionsSection({ counts, visible, onOpen }: {
  counts: ReportCounts | null; visible: Set<Section>; onOpen: (s: Section) => void;
}) {
  if (!counts) return <Skeleton className="h-48 rounded-3xl" />;
  const rows = PENDING_LABELS.filter(([k]) => (Number(counts[k]) || 0) > 0);
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <div className="p-5 md:p-6">
        <h2 className="font-display text-xl font-semibold">Ждут решения</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {rows.length ? "Нажмите на строку — откроется раздел, где это решается. Счётчики обновляются сами." : "✅ Ничего не ждёт вашего решения."}
        </p>
      </div>
      {rows.length > 0 && (
        <ul className="divide-y border-t">
          {rows.map(([k, label], i) => {
            const target = sectionOf(k) as Section | null;
            const can = !!target && visible.has(target);
            const where = can ? SECTIONS.find((s) => s.id === target)!.label : null;
            return (
              <li key={k}>
                <button disabled={!can} style={{ "--i": i } as CSSProperties}
                        onClick={() => { if (target) { tapHaptic(); onOpen(target); } }}
                        className="press row-in flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-muted/60 disabled:opacity-100 md:px-6">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{label}</span>
                    {where && <span className="text-xs text-muted-foreground">{groupLabel(target!)} → {where}</span>}
                  </span>
                  <span className={cn("inline-grid min-w-7 place-items-center rounded-full px-2 text-sm leading-7 font-semibold text-white tabular",
                    target && RED.includes(target) ? "bg-cancel" : "bg-wait")}>{counts[k]}</span>
                  {can && <ChevronRight className="size-4 text-muted-foreground" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

const groupLabel = (s: Section) => GROUPS.find((g) => g.id === groupOf(s))!.label;

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
  const { data, loading, reload } = useLoad(() => api.admin.day(day), [day, version]);
  // ТЗ 29.10 §5: today and tomorrow — the project's own dates from the server — each with its own list of the unmarked
  const [tick, setTick] = useState(0);
  const near = useLoad(async () => {
    const first = await api.admin.day(today);
    const t = first.today && first.today !== first.date ? await api.admin.day(first.today) : first;
    return [t, await api.admin.day(t.tomorrow ?? addDays(t.date, 1))] as const;
  }, [version, tick]);
  const refresh = () => {
    setTick((n) => n + 1);
    void reload();
  };
  const serverToday = near.data?.[0].date ?? today;
  // ТЗ «Яндекс Плюс» §12.5: past midnight «today» and «tomorrow» move on — the lists and the countdown follow
  useEffect(() => {
    const id = window.setInterval(() => {
      if (near.data && moscowToday() !== near.data[0].date) setTick((n) => n + 1);
    }, 60_000);
    return () => window.clearInterval(id);
  }, [near.data]);
  const chips = [
    { label: "Сегодня", value: serverToday },
    { label: "Завтра", value: addDays(serverToday, 1) },
    { label: "Послезавтра", value: addDays(serverToday, 2) },
  ];
  const { weekday } = parseDay(day);
  const going = data?.items.filter((s) => s.status === "active" || s.status === "restored") ?? [];
  const isNear = day === serverToday || day === addDays(serverToday, 1);

  return (
    <div className="space-y-4">
      <UnmarkedPanel days={near.data ?? null} onDone={refresh} />
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
          {data ? `Выходят ${data.total_count} ${plural(data.total_count, "человек", "человека", "человек")}, ${data.total_volume} шт.` : " "}
        </p>
        {/* today and tomorrow are in the panel above; any other day — its own list here */}
        {data && data.date === day && !isNear && data.unmarked.length > 0 && (
          <div className="mt-5"><UnmarkedList data={data} onDone={refresh} /></div>
        )}

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
    </div>
  );
}

/** ТЗ 29.10 §5: «Не отметили» for today and for tomorrow, side by side as two tabs — nobody gets lost because they
 * have no shift and no day-off record. */
function UnmarkedPanel({ days, onDone }: { days: readonly [AdminDay, AdminDay] | null; onDone: () => void }) {
  const [which, setWhich] = useState<0 | 1>(0);
  if (!days) return <Skeleton className="h-24 rounded-3xl" />;
  const [t, m] = days;
  if (t.unmarked.length === 0 && m.unmarked.length === 0) {
    return (
      <div className="space-y-1 rounded-3xl bg-card px-5 py-4 text-sm text-muted-foreground shadow-lift">
        <p>✅ Сегодня и завтра все, кто работает, отметили день — смену или выходной.</p>
        {m.deadline && <Countdown deadline={m.deadline} day={m.date} />}
      </div>
    );
  }
  const d = days[which];
  return (
    <section className="space-y-3 rounded-3xl bg-wait-soft p-4 md:p-5">
      <div className="flex gap-1 rounded-2xl bg-card p-1">
        {([0, 1] as const).map((i) => (
          <button key={i} onClick={() => { tapHaptic(); setWhich(i); }}
                  className={cn("press inline-flex h-10 flex-1 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold",
                    which === i ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>
            {i === 0 ? "Сегодня" : "Завтра"} · {dayMonth(days[i].date)}
            {days[i].unmarked.length > 0 && (
              <span className={cn("rounded-full px-1.5 text-xs tabular", which === i ? "bg-white/20" : "bg-wait text-white")}>{days[i].unmarked.length}</span>
            )}
          </button>
        ))}
      </div>
      <UnmarkedList key={d.date} data={d} onDone={onDone} bare />
    </section>
  );
}

function left(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h} ч ${m} мин`;
  return m > 0 ? `${m} мин` : "меньше минуты";
}

/** ТЗ «Яндекс Плюс» §12.5: time left to mark the day — counted from the server's figure (not the phone's clock), ticking
 * without a reload; at zero it says the deadline has come (a marked person is never shown as late because of it). */
function Countdown({ deadline, day, onPassed }: { deadline: NonNullable<AdminDay["deadline"]>; day: string; onPassed?: () => void }) {
  const [end] = useState(() => Date.now() + deadline.seconds_left * 1000);
  const [now, setNow] = useState(() => Date.now());
  const passedRef = useRef(deadline.passed);
  useEffect(() => {
    if (deadline.passed) return;
    const id = window.setInterval(() => setNow(Date.now()), 10_000);
    return () => window.clearInterval(id);
  }, [deadline.passed]);
  const rest = Math.max(0, Math.round((end - now) / 1000));
  const passed = deadline.passed || rest <= 0;
  useEffect(() => {
    if (passed && !passedRef.current) {
      passedRef.current = true;
      onPassed?.();
    }
  }, [passed, onPassed]);
  return (
    <p className={cn("text-sm font-semibold tabular", passed ? "text-cancel" : rest < 3600 ? "text-wait" : "text-foreground")}>
      ⏳ {passed ? `Дедлайн наступил (отметить ${dayMonth(day)} нужно было до ${deadline.time} накануне)`
        : `До дедлайна: ${left(rest)} — отметить ${dayMonth(day)} до ${deadline.time}`}
    </p>
  );
}

function remindToast(res: { sent: number; failed: { name: string; error: string | null }[]; skipped: { name: string; why: string }[];
  repeat: boolean }) {
  const parts = [res.repeat ? `Уже отправлено: ${res.sent}` : `Напоминание отправлено: ${res.sent}`];
  if (res.skipped.length) parts.push(`пропущено ${res.skipped.length} (${res.skipped.map((x) => `${x.name} — ${x.why}`).join("; ")})`);
  if (res.failed.length) {
    toast.warning([...parts, `не доставлено ${res.failed.length}: ${res.failed.map((x) => `${x.name} — ${x.error ?? "ошибка"}`).join("; ")}`]
      .join(", "));
  } else {
    toast.success(parts.join(", "));
  }
}

/** Customer 28.10, ТЗ 29.10 §5–6: who hasn't marked the day (no shift, no day off) — a manager marks it for them right
 * here, or fines them through the same violation and fine as «Дисциплина»; ТЗ «Яндекс Плюс» §12 — or reminds them
 * (one person or everyone still unmarked, only within the manager's network). */
function UnmarkedList({ data, onDone, bare }: { data: AdminDay; onDone: () => void; bare?: boolean }) {
  const day = data.date;
  const people = data.unmarked;
  const canMark = data.can_mark;
  const [form, setForm] = useState<{ user: UnmarkedPerson; start: string; end: string; volume: string; location: string } | null>(null);
  const [fining, setFining] = useState<UnmarkedPerson | null>(null);
  const { busy, run } = useAction();
  const remind = async (ids: number[] | "all") => {
    try {
      const res = await run(ids === "all" ? "remind-all" : `remind-${ids[0]}`,
        (k) => api.admin.dayRemind(day, ids === "all" ? { all: true } : { user_ids: ids }, k));
      if (res) remindToast(res);
    } catch {
      /* toast shown */
    }
  };
  const mark = async (person: UnmarkedPerson, kind: "off" | "shift") => {
    try {
      await run(`${kind}-${person.user_id}`, (k) => api.admin.markDay(day, kind === "off" ? { user_id: person.user_id, kind } : {
        user_id: person.user_id, kind, start_time: form!.start, end_time: form!.end, volume: Number(form!.volume), location: form!.location,
      }, k), kind === "off" ? "Отмечен выходной" : "Записан в график");
      setForm(null);
      onDone();
    } catch {
      /* toast shown */
    }
  };
  if (people.length === 0) {
    return <p className="rounded-xl bg-card p-3 text-sm text-muted-foreground">✅ {dayMonth(day)}: все отметили день.</p>;
  }
  return (
    <div className={cn(!bare && "rounded-2xl bg-wait-soft p-4")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold text-wait">Не отметили {dayMonth(day)}: {people.length}</p>
        {data.can_remind && (
          <Button size="sm" variant="secondary" className="press h-9 rounded-lg" disabled={!!busy} onClick={() => remind("all")}>
            {busy === "remind-all" && <LoaderCircle className="animate-spin" />} 📣 Напомнить всем
          </Button>
        )}
      </div>
      {data.deadline && canMark && <Countdown key={data.deadline.at} deadline={data.deadline} day={day} onPassed={onDone} />}
      <p className="mt-0.5 text-sm text-muted-foreground">
        Ни смены, ни выходного. {canMark ? "Можно отметить за человека — он получит сообщение." : "День прошёл."}
        {data.can_fine === false && data.fine_note && people.some((p) => !p.violation || p.violation.status === "pending") ? ` ${data.fine_note}.` : ""}
      </p>
      <ul className="mt-3 space-y-2">
        {people.map((p) => {
          const v = p.violation;
          const decided = !!v && v.status !== "pending";
          return (
            <li key={p.user_id} className="rounded-xl bg-card p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1">
                  <button className="block max-w-full truncate text-left font-medium underline-offset-2 hover:underline"
                          onClick={() => openProfile(p.username, p.telegram_id, p.name)}>
                    {p.name}{p.network && <span className="font-normal text-muted-foreground"> · {p.network}</span>}
                  </button>
                  <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
                    <span className={cn("rounded-full px-2 py-0.5 font-semibold", TONE_CHIP.wait)}>
                      {dayMonth(day)} · Не в графике и не на выходном
                    </span>
                    {v && (
                      <span className={cn("rounded-full px-2 py-0.5 font-semibold",
                        TONE_CHIP[v.status === "waived" ? "archive" : v.status === "pending" ? "wait" : v.fine?.status === "rejected" ? "bad" : "ok"])}>
                        {v.state}{v.fine?.units ? ` · ${v.fine.units} ед.` : ""}
                      </span>
                    )}
                  </span>
                </span>
                {canMark && (
                  <span className="flex shrink-0 flex-wrap gap-1.5">
                    <Button size="sm" variant="secondary" className="press h-9 rounded-lg" disabled={!!busy}
                            onClick={() => mark(p, "off")}>Выходной</Button>
                    <Button size="sm" className="press h-9 rounded-lg" disabled={!!busy}
                            onClick={() => setForm(form?.user.user_id === p.user_id ? null
                              : { user: p, start: "10:00", end: "18:00", volume: "", location: "" })}>В график</Button>
                    {data.can_fine && !decided && (
                      <Button size="sm" variant="ghost" className="press h-9 rounded-lg text-cancel hover:bg-cancel-soft hover:text-cancel"
                              disabled={!!busy} onClick={() => setFining(p)}>Оштрафовать</Button>
                    )}
                  </span>
                )}
                {data.can_remind && (
                  <Button size="sm" variant="ghost" className="press h-9 shrink-0 rounded-lg" disabled={!!busy}
                          onClick={() => remind([p.user_id])}>
                    {busy === `remind-${p.user_id}` && <LoaderCircle className="animate-spin" />} Напомнить
                  </Button>
                )}
              </div>
              {form?.user.user_id === p.user_id && (
                <div className="row-in mt-3 grid grid-cols-2 gap-2">
                  <input type="time" aria-label="С" className={cn(field, "h-10")} value={form.start}
                         onChange={(e) => setForm({ ...form, start: e.target.value })} />
                  <input type="time" aria-label="До" className={cn(field, "h-10")} value={form.end}
                         onChange={(e) => setForm({ ...form, end: e.target.value })} />
                  <input inputMode="numeric" placeholder="Объём, шт." className={cn(field, "h-10")} value={form.volume}
                         onChange={(e) => setForm({ ...form, volume: e.target.value.replace(/\D/g, "").slice(0, 5) })} />
                  <input placeholder="Точка" className={cn(field, "h-10")} value={form.location}
                         onChange={(e) => setForm({ ...form, location: e.target.value.slice(0, 100) })} />
                  <Button className="press col-span-2 h-10 rounded-xl" disabled={!!busy || !form.volume || !form.location.trim()}
                          onClick={() => mark(p, "shift")}>
                    {busy === `shift-${p.user_id}` && <LoaderCircle className="animate-spin" />} Записать
                  </Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <UnmarkedFineDrawer
        open={fining ? { day, userId: fining.user_id, name: fining.name, suggestedUnits: data.suggested_units ?? 3,
          defaultComment: data.default_comment ?? "", unitRate: fining.unit_rate ?? 0 } : null}
        onClose={() => setFining(null)}
        onDone={() => { setFining(null); onDone(); }} />
    </div>
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
