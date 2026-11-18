"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { CalendarPlus, CalendarX, Check, GraduationCap, LoaderCircle, MessageCircle, MoreHorizontal, Play, RotateCcw, Search, Send, ShieldCheck, Star, Timer, UserCog, UserX, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Application, type Role, type StageProgress, type TrainingRequest, type User } from "@/lib/api";
import { ddmm, plural } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { openProfile } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { AgreementMark, AttemptsHistory, CancelCounter, CancelWarning, ContactTimer, PriorityBadge, ReasonForm, StageBadge, Submitted, TrainingLine, WaitingBadge, agreedCard,
  requestLines } from "../training/shared";

type Tab = "new" | "scheduled" | "internship" | "done" | "rejected";
const TABS: { id: Tab; label: string }[] = [
  { id: "new", label: "Ожидают рассмотрения" },
  { id: "scheduled", label: "На обучении" },
  { id: "internship", label: "Стажировка" },
  { id: "done", label: "Завершённые" },
  { id: "rejected", label: "Отклонённые" },
];
const DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";
type Trainer = { id: number; name: string; username: string | null; role?: Role };
type Sort = "new" | "age" | "skill" | "days" | "wait";
type Dir = "desc" | "asc";

/** The questionnaire's moment — newer first; the id keeps the order stable. */
const byNewest = (a: Application, b: Application) => (b.created_at ?? "").localeCompare(a.created_at ?? "") || b.id - a.id;

/** ТЗ 30.10 §6: «Количество дней в неделю» from the questionnaire; no value — always at the end. */
const byDays = (dir: Dir) => (a: Application, b: Application) => {
  const x = a.min_days || null;
  const y = b.min_days || null;
  if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
  return dir === "desc" ? y - x : x - y;
};

/** «Онбординг» (the customer's module 3): candidates from the questionnaire → training → internship → bonuses. */
export function OnboardingSection({ me }: { me: User }) {
  const [tab, setTab] = useState<Tab>("new");
  const { data, reload } = useLoad(() => api.admin.onboarding(tab), [tab]);
  const [age, setAge] = useState<[string, string]>(["", ""]);
  const [skill, setSkill] = useState<[number, number]>([1, 10]);
  const [days, setDays] = useState<number[]>([]);
  const [metro, setMetro] = useState("");
  const [minDays, setMinDays] = useState("");
  // 19.11: search by the person's name (or @username) — the first thing to reach for on a long list
  const [q, setQ] = useState("");
  // 19.11: quick chips — «конкретный вид людей»: the ⭐ ones, or without cancelled trainings and no-shows
  const [prioOnly, setPrioOnly] = useState(false);
  const [respOnly, setRespOnly] = useState(false);
  const [sort, setSort] = useState<Sort>("new");
  const [dir, setDir] = useState<Dir>("desc");
  // ТЗ 30.10 §4: the order follows the confirmations made elsewhere (the bot, the trainer) without a manual reload
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void reload();
    }, 30_000);
    return () => window.clearInterval(id);
  }, [reload]);

  const items = useMemo(() => {
    const metroQ = metro.trim().toLowerCase();
    const qQ = q.trim().toLowerCase();
    const list = (data?.items ?? []).filter((a) =>
      (!qQ || a.full_name.toLowerCase().includes(qQ) || `@${a.username ?? ""}`.includes(qQ))
      && (!age[0] || a.age >= Number(age[0])) && (!age[1] || a.age <= Number(age[1]))
      && a.communication >= skill[0] && a.communication <= skill[1]
      && days.every((d) => a.training_days.includes(d))
      && (!metroQ || a.metro.toLowerCase().includes(metroQ))
      && (!minDays || a.min_days >= Number(minDays))
      && (!prioOnly || a.priority) && (!respOnly || !(a.cancel_count ?? 0)));
    const chosen = sort === "age" ? (a: Application, b: Application) => a.age - b.age
      : sort === "skill" ? (a: Application, b: Application) => b.communication - a.communication
      // «кто дольше ждёт» — by the server's waiting hours (only «Ожидают рассмотрения» has them; elsewhere — newest)
      : sort === "wait" ? (a: Application, b: Application) => (b.waiting?.hours ?? 0) - (a.waiting?.hours ?? 0)
      : sort === "days" ? byDays(dir) : () => 0;
    // «На обучении» (§4): both confirmed first, then the rest by their progress; inside — the chosen sort, then the questionnaire
    const agreement = (a: Application) => (tab === "scheduled" ? a.agreement_rank ?? 9 : 0);
    // 19.11: the priority questionnaires (⭐) come right after the agreement (§4) — before the chosen sort, on every tab
    const prio = (a: Application) => Number(!!a.priority);
    return list.sort((a, b) => agreement(a) - agreement(b) || prio(b) - prio(a) || chosen(a, b) || byNewest(a, b));
  }, [data, age, skill, days, metro, minDays, prioOnly, respOnly, q, sort, dir, tab]);
  const filtered = !!(age[0] || age[1]) || skill[0] > 1 || skill[1] < 10 || days.length > 0
    || !!metro.trim() || !!minDays || prioOnly || respOnly || !!q.trim();
  // the chip counts the ⭐ ones in the current tab (before the other filters)
  const prioCount = (data?.items ?? []).filter((a) => a.priority).length;
  // 18.11: in all the pickers the owners and the trainers/instructors come first (the inviter tops it on the card)
  const trainersSorted = useMemo(() => sortedTrainers(data?.trainers ?? []), [data]);

  return (
    <div className="space-y-4">
      <div className="no-scrollbar -mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
        <div className="flex w-max gap-1 rounded-2xl bg-card p-1 shadow-lift">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
                    className={cn("press inline-flex h-10 items-center gap-1.5 rounded-xl px-3.5 text-sm font-semibold whitespace-nowrap",
                      tab === t.id ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>
              {t.label}
              {(data?.counts[t.id] ?? 0) > 0 && (
                <span className={cn("rounded-full px-1.5 text-xs tabular", tab === t.id ? "bg-white/20" : "bg-muted")}>{data!.counts[t.id]}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* 19.11: quick kind-of-person chips, always in sight; combined with the filters below */}
      <div className="flex flex-wrap items-center gap-1.5">
        <label className="relative min-w-full flex-1 sm:min-w-0">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input aria-label="Поиск по имени" placeholder="Поиск по имени или @username" value={q}
                 onChange={(e) => setQ(e.target.value.slice(0, 40))}
                 className={cn(field, "pl-9 pr-8")} />
          {q && (
            <button type="button" aria-label="Очистить поиск" onClick={() => setQ("")}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1 text-muted-foreground hover:text-foreground">
              <RotateCcw className="size-3.5" />
            </button>
          )}
        </label>
        <button type="button" aria-pressed={prioOnly} onClick={() => setPrioOnly(!prioOnly)}
                title="Только приоритетные анкеты — обучение в первую очередь"
                className={cn("press inline-flex h-9 items-center gap-1.5 rounded-xl px-3.5 text-sm font-semibold", prioOnly ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
          <Star className={cn("size-4", prioOnly && "fill-current")} /> Приоритетные
          {prioCount > 0 && (
            <span className={cn("rounded-full px-1.5 text-xs tabular", prioOnly ? "bg-white/20" : "bg-card")}>{prioCount}</span>
          )}
        </button>
        <button type="button" aria-pressed={respOnly} onClick={() => setRespOnly(!respOnly)}
                title="Без отмен обучения и неявок — кто уже показал ответственность"
                className={cn("press inline-flex h-9 items-center gap-1.5 rounded-xl px-3.5 text-sm font-semibold", respOnly ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
          <ShieldCheck className="size-4" /> Ответственные
        </button>
      </div>

      <details className="group rounded-3xl bg-card p-5 shadow-lift md:p-6" open={filtered || undefined}>
        <summary className="cursor-pointer font-semibold">Фильтры и сортировка{filtered ? " · включены" : ""}</summary>
        <div className="mt-4 space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-24 text-muted-foreground">Возраст</span>
            <input inputMode="numeric" aria-label="Возраст от" placeholder="от" className={cn(field, "h-10 w-20")} value={age[0]}
                   onChange={(e) => setAge([e.target.value.replace(/\D/g, "").slice(0, 2), age[1]])} />
            <input inputMode="numeric" aria-label="Возраст до" placeholder="до" className={cn(field, "h-10 w-20")} value={age[1]}
                   onChange={(e) => setAge([age[0], e.target.value.replace(/\D/g, "").slice(0, 2)])} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-24 text-muted-foreground">Навыки</span>
            <select aria-label="Навыки от" className={cn(field, "h-10 w-20")} value={skill[0]} onChange={(e) => setSkill([Number(e.target.value), skill[1]])}>
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <span className="text-muted-foreground">—</span>
            <select aria-label="Навыки до" className={cn(field, "h-10 w-20")} value={skill[1]} onChange={(e) => setSkill([skill[0], Number(e.target.value)])}>
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-24 text-muted-foreground">Метро</span>
            <input aria-label="Метро содержит" placeholder="станция" className={cn(field, "h-10 w-40")} value={metro}
                   onChange={(e) => setMetro(e.target.value.slice(0, 30))} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-24 text-muted-foreground">Дней в неделю</span>
            <input inputMode="numeric" aria-label="Дней в неделю не меньше" placeholder="от" className={cn(field, "h-10 w-20")} value={minDays}
                   onChange={(e) => setMinDays(e.target.value.replace(/\D/g, "").slice(0, 1))} />
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-24 text-muted-foreground">Свободен в</span>
            {DAYS.map((d, i) => (
              <button key={d} onClick={() => setDays(days.includes(i) ? days.filter((x) => x !== i) : [...days, i])}
                      className={cn("press h-9 w-10 rounded-xl text-sm font-semibold", days.includes(i) ? "bg-primary text-primary-foreground" : "bg-muted")}>
                {d}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-24 text-muted-foreground">Сортировка</span>
            <select className={cn(field, "h-10 w-auto min-w-0 max-w-full")} value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="new">сначала новые</option>
              <option value="wait">кто дольше ждёт</option>
              <option value="age">по возрасту</option>
              <option value="skill">по навыкам</option>
              <option value="days">количество дней в неделю</option>
            </select>
            {sort === "days" && (
              <div className="flex rounded-xl bg-muted p-0.5" role="group" aria-label="Направление сортировки">
                {([["desc", "по убыванию"], ["asc", "по возрастанию"]] as const).map(([d, label]) => (
                  <button key={d} type="button" onClick={() => setDir(d)} aria-pressed={dir === d}
                          className={cn("press h-9 rounded-lg px-3 text-sm font-semibold whitespace-nowrap", dir === d ? "bg-card shadow-lift" : "text-muted-foreground")}>
                    {label}
                  </button>
                ))}
              </div>
            )}
            {filtered && (
              <Button variant="ghost" size="sm" className="rounded-lg" onClick={() => { setAge(["", ""]); setSkill([1, 10]); setDays([]); setMetro(""); setMinDays(""); setPrioOnly(false); setRespOnly(false); setQ(""); }}>
                Сбросить
              </Button>
            )}
          </div>
        </div>
      </details>

      {data && data.requests.length > 0 && (
        <Requests items={data.requests} trainers={trainersSorted} points={data.points} onDone={reload} />
      )}

      {!data ? <Skeleton className="h-48 rounded-3xl" /> : items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">
          {q.trim() ? `Никого не нашлось по «${q.trim()}».` : filtered ? "Под фильтры никто не подходит." : "Здесь пока пусто."}
        </p>
      ) : (
        <ul className="space-y-3">
          {tab === "scheduled" && items.some((a) => a.agreement === "agreed") && (
            <li className="px-1 text-xs text-muted-foreground">Сверху — кандидаты, у которых дату и время подтвердили и кандидат, и тренер.</li>
          )}
          {items.map((a) => (
            <Candidate key={a.id} a={a} me={me} trainers={trainersSorted} points={data.points} onDone={reload} />
          ))}
        </ul>
      )}
    </div>
  );
}

type Panel = "trainer" | "training" | "reject" | "contact" | "stage" | "cancel" | null;

function Candidate({ a, me, trainers, points, onDone }: {
  a: Application; me: User; trainers: Trainer[]; points: string[]; onDone: () => void;
}) {
  const [panel, setPanel] = useState<Panel>(null);
  const [broken, setBroken] = useState(false);
  const { busy, run } = useAction();
  const p = a.progress;
  const openTg = () => openProfile(a.username, a.telegram_id, a.full_name);
  const link = "font-semibold text-foreground underline decoration-muted-foreground/40 underline-offset-2 hover:decoration-foreground";
  const act = async (name: string, fn: (k: string) => Promise<unknown>, ok: string) => {
    try {
      if (await run(name, fn, ok)) {
        setPanel(null);
        onDone();
      }
    } catch {
      /* toast shown */
    }
  };
  const toggle = (x: Panel) => setPanel(panel === x ? null : x);
  const open = a.status === "new" || a.status === "scheduled";
  const scheduled = a.status === "scheduled";
  const [more, setMore] = useState(false);

  // §27 (согласовано 29.10): all the card's actions, in order; 2–3 main ones for the stage stay in sight, the rest — «Ещё…»
  const all: { id: string; el: React.ReactNode }[] = [];
  const add = (show: unknown, id: string, el: React.ReactNode) => { if (show) all.push({ id, el }); };
  add(open, "trainer",
    <Button size="sm" className="h-9 rounded-xl" onClick={() => toggle("trainer")}>
      <UserCog /> {a.trainer ? "Сменить тренера" : "Назначить тренера"}
    </Button>);
  add(scheduled, "training",
    <Button size="sm" variant="secondary" className="h-9 rounded-xl" onClick={() => toggle("training")}>
      <CalendarPlus /> {a.training_date ? "Изменить обучение" : "Назначить дату"}
    </Button>);
  add(scheduled && a.trainer && !a.training_started_at, "start",
    <Button size="sm" variant="secondary" className="h-9 rounded-xl" disabled={!!busy}
            onClick={() => act("start", (k) => api.admin.startTraining(a.id, k), "Доступ к приложению открыт")}>
      <Play /> Выдать доступ к приложению
    </Button>);
  add(scheduled && a.trainer, "intern",
    <Button size="sm" className="h-9 rounded-xl bg-go text-white hover:bg-go/90" disabled={!!busy}
            onClick={() => act("intern", (k) => api.admin.startInternship(a.id, k), "Стажировка началась: у кандидата цель по стажировке")}>
      {busy === "intern" ? <LoaderCircle className="animate-spin" /> : <GraduationCap />} Начать стажировку
    </Button>);
  add(a.status === "internship" || (a.status === "done" && !a.stage2_at), "stage",
    <Button size="sm" className={cn("h-9 rounded-xl", (p?.review || a.finish_requested_at) && "bg-go text-white hover:bg-go/90")}
            onClick={() => toggle("stage")}>
      <Check /> {a.status === "internship" ? "Принять в команду" : "Проверить второй этап"}
    </Button>);
  add(a.status === "rejected", "return",
    <Button variant="secondary" size="sm" className="h-9 rounded-xl" disabled={!!busy}
            onClick={() => act("return", (k) => api.admin.returnApplication(a.id, k), "Возвращён в резерв: блокировка снята, бот попросит заполнить анкету заново")}>
      {busy === "return" ? <LoaderCircle className="animate-spin" /> : <RotateCcw />} Вернуть в резерв
    </Button>);
  add(scheduled && a.trainer, "resend",
    <Button variant="secondary" size="sm" className="h-9 rounded-xl" disabled={!!busy}
            onClick={() => act("resend", (k) => api.admin.trainerMessage(a.id, k), "Сообщение отправлено ещё раз, отсчёт заново")}>
      <Send /> Написать о тренере ещё раз
    </Button>);
  add(scheduled && a.trainer && a.contact_timer_at, "reset",
    <Button variant="ghost" size="sm" className="h-9 rounded-xl" disabled={!!busy}
            onClick={() => act("reset", (k) => api.admin.timerReset(a.id, k), "Таймер сброшен")}>
      <Timer /> Сбросить таймер
    </Button>);
  add(scheduled && a.trainer && a.referrer, "ref",
    <Button variant="ghost" size="sm" className="h-9 rounded-xl" disabled={!!busy}
            onClick={() => act("ref", (k) => api.admin.remindReferrer(a.id, k), "Пригласивший получил просьбу помочь")}>
      <Users /> Напомнить пригласившему
    </Button>);
  // 19.11: the training priority — the candidate's own mark or this switch, on any questionnaire
  add(true, "priority",
    <Button variant={a.priority ? "secondary" : "ghost"} size="sm" className="h-9 rounded-xl" disabled={!!busy}
            onClick={() => act("priority", (k) => api.admin.setPriority(a.id, !a.priority, k),
              a.priority ? "Приоритет снят" : "Приоритет включён: анкета наверху списка, в сообщениях тренеру — ⭐")}>
      {busy === "priority" ? <LoaderCircle className="animate-spin" /> : <Star className={cn("size-4", a.priority && "fill-current text-primary")} />}
      {a.priority ? "Снять приоритет" : "Сделать приоритетной"}
    </Button>);
  add(open, "contact",
    <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={() => toggle("contact")}>
      <MessageCircle /> Контакт вручную
    </Button>);
  add(scheduled, "cancel",
    <Button variant="secondary" size="sm" className="h-9 rounded-xl" onClick={() => toggle("cancel")}>
      <CalendarX /> Отменить обучение
    </Button>);
  add(open || a.status === "internship", "reject",
    <Button variant="ghost" size="sm" className="h-9 rounded-xl text-cancel hover:bg-cancel-soft hover:text-cancel" onClick={() => toggle("reject")}>
      <UserX /> {scheduled ? "Отказать в обучении" : "Отказать"}
    </Button>);
  // the next step of the candidate's path is what stays in sight
  const primary: string[] = a.status === "new" ? ["trainer", "reject"]
    : scheduled ? (!a.trainer ? ["trainer"] : !a.training_date ? ["training"]
      : !a.training_started_at ? ["start", "training"] : ["intern"])
    : a.status === "internship" || a.status === "done" ? ["stage"]
    : a.status === "rejected" ? ["return"] : [];
  let main = all.filter((x) => primary.includes(x.id));
  let rest = all.filter((x) => !primary.includes(x.id));
  if (main.length === 0) [main, rest] = [rest, []];

  return (
    <li className={cn("row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6", agreedCard(a))}>
      <div className="flex items-start gap-3">
        {broken ? (
          <span className="grid size-14 shrink-0 place-items-center rounded-2xl bg-muted font-display text-lg font-semibold">
            {a.full_name.split(" ").slice(0, 2).map((w) => w[0]).join("")}
          </span>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={a.avatar_url} alt="" onError={() => setBroken(true)} className="size-14 shrink-0 rounded-2xl bg-muted object-cover" />
        )}
        <div className="min-w-0 flex-1">
          <button type="button" className="text-left font-semibold underline-offset-2 hover:underline" onClick={openTg}>{a.full_name}</button>
          <p className="text-sm text-muted-foreground">
            {a.age} {plural(a.age, "год", "года", "лет")} · м. {a.metro}{a.username ? ` · @${a.username}` : ""}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Навыки {a.communication}/10 · от {a.min_days} {plural(a.min_days, "дня", "дней", "дней")} в неделю
            {a.earlier ? ` · анкет раньше: ${a.earlier}` : ""}
          </p>
          <Submitted a={a} className="mt-0.5" />
          <div className="mt-2 flex flex-wrap items-center gap-1">
            <PriorityBadge a={a} />
            <StageBadge a={a} />
            {a.waiting && <WaitingBadge a={a} />}
            {a.training_days.map((d) => <span key={d} className="rounded-lg bg-muted px-2 py-0.5 text-xs font-semibold">{DAYS[d]}</span>)}
          </div>
        </div>
      </div>

      {/* §2: the inviter and the trainer are different people */}
      <div className="grid gap-1 text-sm">
        <p className="text-muted-foreground">
          Пригласил(а):{" "}
          {a.referrer ? (a.referrer_tg
            ? <button type="button" className={link} onClick={() => openProfile(a.referrer_tg!.username, a.referrer_tg!.telegram_id, a.referrer)}>{a.referrer}</button>
            : <b className="text-foreground">{a.referrer}</b>) : "—"}
        </p>
        {a.trainer && (
          <p className="text-muted-foreground">
            Тренер:{" "}
            <button type="button" className={link} onClick={() => openProfile(a.trainer!.username, a.trainer_tg?.telegram_id, a.trainer!.name)}>{a.trainer.name}</button>
          </p>
        )}
        {a.contact_timer_at && <ContactTimer since={a.contact_timer_at} />}
        {a.contact_answer && scheduled && a.trainer && (
          <p className="text-xs">{a.contact_answer === "wrote" ? <b className="text-go">Кандидат написал тренеру</b>
            : <b className="text-wait">Кандидат просит написать ему первым</b>}</p>
        )}
        {(scheduled || (a.cancel_count ?? 0) > 0) && <CancelCounter a={a} />}
        <AttemptsHistory a={a} />
      </div>

      {scheduled && <TrainingLine a={a} />}
      <AgreementMark a={a} />
      {(a.requests?.length ?? 0) > 0 && (
        <p className="rounded-2xl bg-wait-soft px-4 py-2.5 text-sm text-wait">
          Ожидает согласования: {a.requests!.map((r) => r.title).join(", ")} — решение выше, в списке запросов.
        </p>
      )}
      <CancelWarning a={a}>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" className="h-9 rounded-xl" disabled={!!busy}
                  onClick={() => act("ack", (k) => api.admin.cancelAck(a.id, k), "Обучение продолжается")}>Продолжить обучение</Button>
          <Button size="sm" variant="ghost" className="h-9 rounded-xl text-cancel" onClick={() => setPanel("reject")}>Отказать</Button>
        </div>
      </CancelWarning>
      {a.contacted_at && a.status !== "rejected" && (
        <p className="text-xs text-muted-foreground">
          Написали через{" "}
          <button type="button" className={link} onClick={() => openProfile(a.contact_manager)}>@{a.contact_manager}</button>{" "}
          {ddmm(a.contacted_at.slice(0, 10))}:{" "}
          {a.contact_answer === "wrote" ? <b className="text-go">кандидат написал менеджеру</b>
            : a.contact_answer === "cant_write" ? <b className="text-wait">просит написать ему первым</b> : "ответа пока нет"}
        </p>
      )}
      {a.status === "rejected" && <p className="text-sm text-cancel">Отказ{a.reject_reason ? `: ${a.reject_reason}` : ""}{a.decided_at ? `, ${ddmm(a.decided_at.slice(0, 10))}` : ""} · аккаунт заблокирован</p>}
      {p && (
        <div className="space-y-1.5">
          <div className="relative h-2 overflow-hidden rounded-full bg-muted">
            <span className={cn("absolute inset-y-0 left-0 rounded-full", p.review ? "bg-go" : "bg-primary")}
                  style={{ width: `${Math.min(100, (p.units / Math.max(p.target, 1)) * 100)}%` }} />
          </div>
          <p className="text-xs text-muted-foreground">
            {p.stage === 1 ? "Стажировка (зачёт по оплаченным выплатам)" : "Работа после стажировки"}:{" "}
            <b className="text-foreground">{p.units} из {p.target}</b>
            {p.stage === 1 && !!p.paid && p.fact != null && p.fact !== p.units ? ` · сделано по факту ${p.fact}` : ""}
            {p.pending ? `, ещё ${p.pending} на проверке` : ""}{p.review ? " · ждёт проверки" : ""}
            {a.finish_requested_at ? " · тренер отметил завершение" : ""}
            {a.stage1_counted != null ? ` · стажировка засчитана: ${a.stage1_counted}` : ""}
          </p>
          {/* 18.11: the owner corrects the paid counter by hand; the target closes the internship */}
          {p.stage === 1 && a.status === "internship" && <PaidUnits a={a} p={p} onDone={onDone} />}
        </div>
      )}

      <div className="-mx-1 flex flex-wrap gap-1.5">
        {main.map((x) => <Fragment key={x.id}>{x.el}</Fragment>)}
        {rest.length > 0 && (
          <Button variant="ghost" size="sm" className="h-9 rounded-xl" aria-expanded={more} onClick={() => setMore(!more)}>
            <MoreHorizontal /> {more ? "Скрыть" : "Ещё…"}
          </Button>
        )}
      </div>
      {more && rest.length > 0 && (
        <div className="row-in -mx-1 flex flex-wrap gap-1.5 border-t pt-3">
          {rest.map((x) => <Fragment key={x.id}>{x.el}</Fragment>)}
        </div>
      )}

      {panel === "trainer" && <TrainerForm a={a} trainers={trainers} busy={!!busy}
                                           onSave={(id, send) => act("trainer", (k) => api.admin.assignTrainer(a.id, id, send, k),
                                             send ? "Тренер назначен, кандидат получил сообщение" : "Тренер назначен")} />}
      {panel === "training" && <TrainingForm a={a} trainers={trainers} points={points} busy={!!busy}
                                             onSave={(body) => act("train", (k) => api.admin.assignTraining(a.id, body, k),
                                               "Кандидат и тренер получили сообщение и подтвердят")} />}
      {panel === "contact" && <ContactForm me={me} busy={!!busy}
                                           onSave={(manager) => act("contact", () => api.admin.contactApplication(a.id, manager), "Сообщение отправлено кандидату")} />}
      {panel === "cancel" && <CancelForm busy={!!busy} hasDate={!!a.training_date}
                                         onSave={(reason, kind) => act("cancel", (k) => api.admin.cancelTraining(a.id, reason, kind, k),
                                           "Обучение отменено: кандидат вернулся в «Ожидают рассмотрения»")} />}
      {panel === "reject" && (
        <ReasonForm kind="reject_candidate" tone="cancel" busy={!!busy} confirm="Да, отказать"
                    note="Кандидат получит отказ, аккаунт будет заблокирован, бот исключит его из «Предзакрепа». Если отказали по ошибке — «Вернуть в резерв» в «Отклонённых»."
                    onSave={(reason) => act("reject", (k) => api.admin.rejectApplication(a.id, reason, k), "Кандидату отказано")} />
      )}
      {panel === "stage" && p && <StageForm a={a} busy={!!busy}
                                            onSave={(counted, bonus) => act("stage", (k) => api.admin.confirmStage(a.id, p.stage, counted, bonus, k),
                                              p.stage === 1 ? "Принят в команду: приглашение в основную группу отправлено" : "Засчитано")} />}
    </li>
  );
}

/** 18.11: the picker's order — the inviter first, then the owners, the trainers and the instructors (marked),
 * then the rest of the employees. */
const TRAINER_RANK: Record<string, number> = { owner: 1, trainer: 2, instructor: 3, admin: 4, employee: 5 };
const TRAINER_TAG: Record<string, string> = { owner: "владелец · ", trainer: "тренер · ", instructor: "инструктор · ", admin: "админ · " };

function sortedTrainers(trainers: Trainer[], referrerId: number | null = null): Trainer[] {
  return [...trainers].sort((x, y) =>
    (Number(y.id === referrerId) - Number(x.id === referrerId))
    || ((TRAINER_RANK[x.role ?? "employee"] ?? 5) - (TRAINER_RANK[y.role ?? "employee"] ?? 5))
    || x.name.localeCompare(y.name));
}

function TrainerForm({ a, trainers, busy, onSave }: { a: Application; trainers: Trainer[]; busy: boolean;
  onSave: (trainerId: number, send: boolean) => void }) {
  // §2 + 18.11: the inviter first, marked; the owners and the trainers/instructors next; any employee may train
  const sorted = sortedTrainers(trainers, a.referrer_id ?? null);
  const [trainer, setTrainer] = useState<number | null>(a.trainer?.id ?? (a.referrer_id && trainers.some((t) => t.id === a.referrer_id) ? a.referrer_id : null));
  const [preview, setPreview] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const pick = async (id: number | null) => {
    setTrainer(id);
    setPreview(null);
  };
  const show = async () => {
    if (!trainer) return;
    setLoading(true);
    try {
      setPreview((await api.admin.trainerPreview(a.id, trainer)).text);
    } finally {
      setLoading(false);
    }
  };
  return (
    <div className="row-in space-y-2 rounded-2xl bg-muted/50 p-3">
      <select className={field} aria-label="Тренер" value={trainer ?? ""} onChange={(e) => pick(e.target.value ? Number(e.target.value) : null)}>
        <option value="">Тренер — любой сотрудник</option>
        {sorted.map((t) => (
          <option key={t.id} value={t.id}>{t.id === a.referrer_id ? "★ " : ""}{TRAINER_TAG[t.role ?? "employee"] ?? ""}{t.name}{t.username ? ` @${t.username}` : ""}{t.id === a.referrer_id ? " — пригласил(а)" : ""}</option>
        ))}
      </select>
      {!preview ? (
        <Button variant="secondary" className="press h-11 w-full rounded-xl" disabled={!trainer || loading} onClick={show}>
          {loading && <LoaderCircle className="animate-spin" />} Посмотреть сообщение кандидату
        </Button>
      ) : (
        <>
          <div className="rounded-xl bg-card p-3 text-sm whitespace-pre-wrap break-words [&_a]:font-semibold [&_a]:underline"
               dangerouslySetInnerHTML={{ __html: preview }} />
          <div className="grid gap-2 sm:grid-cols-2">
            <Button className="press h-11 rounded-xl" disabled={busy} onClick={() => onSave(trainer!, true)}>
              {busy && <LoaderCircle className="animate-spin" />} <Send /> Отправить кандидату
            </Button>
            <Button variant="secondary" className="press h-11 rounded-xl" disabled={busy} onClick={() => onSave(trainer!, false)}>
              Назначить без сообщения
            </Button>
          </div>
        </>
      )}
      <p className="text-xs text-muted-foreground">
        Сообщение уходит только после «Отправить». С этого момента идёт отсчёт 48 ч; тренеру откроется раздел «Обучение». Текст меняется в «Настройки → Тексты сообщений».
      </p>
    </div>
  );
}

function CancelForm({ busy, hasDate, onSave }: { busy: boolean; hasDate: boolean; onSave: (reason: string, kind: "cancel" | "no_show") => void }) {
  const [kind, setKind] = useState<"cancel" | "no_show">("cancel");
  return (
    <ReasonForm kind="cancel_training" busy={busy} confirm={kind === "no_show" ? "Отметить неявку и отменить" : "Да, отменить обучение"}
                note="Это не отказ: попытка обучения получит статус «Отменено», закрепление за тренером завершится, кандидат вернётся в «Ожидают рассмотрения» — его можно назначить тому же или другому тренеру. Отмена увеличит счётчик «Отмены обучения»; перенос даты — не отмена."
                onSave={(reason) => onSave(reason, kind)}>
      {hasDate && (
        <div className="flex gap-1.5">
          {([["cancel", "Отмена"], ["no_show", "Кандидат не пришёл"]] as const).map(([k, label]) => (
            <button key={k} type="button" onClick={() => setKind(k)}
                    className={cn("press h-9 rounded-full px-3.5 text-sm font-medium", kind === k ? "bg-primary text-primary-foreground" : "bg-card")}>{label}</button>
          ))}
        </div>
      )}
    </ReasonForm>
  );
}

/** «Ожидает согласования» (§3–§4): the trainers' and candidates' requests — the same list the bot's /pending counts. */
function Requests({ items, trainers, points, onDone }: { items: TrainingRequest[]; trainers: Trainer[]; points: string[]; onDone: () => void }) {
  return (
    <section className="space-y-3 rounded-3xl bg-wait-soft p-4 md:p-5">
      <h3 className="font-semibold text-wait">Ожидает согласования: {items.length}</h3>
      <ul className="space-y-2">
        {items.map((r) => <RequestCard key={r.id} r={r} trainers={trainers} points={points} onDone={onDone} />)}
      </ul>
    </section>
  );
}

function RequestCard({ r, trainers, points, onDone }: { r: TrainingRequest; trainers: Trainer[]; points: string[]; onDone: () => void }) {
  const [mode, setMode] = useState<null | "reject" | "schedule">(null);
  const { busy, run } = useAction();
  const decide = async (body: Parameters<typeof api.admin.decideRequest>[1], ok: string) => {
    try {
      if (await run("decide", (k) => api.admin.decideRequest(r.id, body, k), ok)) onDone();
    } catch {
      /* toast shown */
    }
  };
  const needsDate = r.kind === "change" && !r.payload.date && !r.payload.time && !r.payload.location && !r.payload.trainer_id;
  return (
    <li className="space-y-2 rounded-2xl bg-card p-3">
      <p className="flex items-center gap-1.5 font-semibold">
        {r.priority && <Star className="size-4 shrink-0 fill-current text-primary" aria-label="Приоритетная анкета" />}
        {r.title} · {r.candidate}
      </p>
      <p className="text-xs text-muted-foreground">
        {r.initiator_role === "trainer" ? "Тренер" : "Кандидат"}{" "}
        {r.initiator_tg ? (
          <button type="button" className="font-semibold text-foreground underline-offset-2 hover:underline"
                  onClick={() => openProfile(r.initiator_tg!.username, r.initiator_tg!.telegram_id, r.initiator)}>{r.initiator}</button>
        ) : r.initiator} · {ddmm(r.created_at.slice(0, 10))}
      </p>
      {requestLines(r).map((l) => <p key={l} className="text-sm">{l}</p>)}
      {r.reason && <p className="text-sm">Причина: {r.reason}</p>}
      <div className="flex flex-wrap gap-2">
        {needsDate ? (
          <Button size="sm" className="h-9 rounded-xl" onClick={() => setMode(mode === "schedule" ? null : "schedule")}>
            <CalendarPlus /> Назначить новую дату
          </Button>
        ) : (
          <Button size="sm" className="h-9 rounded-xl" disabled={!!busy} onClick={() => decide({ approve: true }, "Одобрено, участники получили сообщение")}>
            {busy === "decide" ? <LoaderCircle className="animate-spin" /> : <Check />} Одобрить
          </Button>
        )}
        <Button size="sm" variant="secondary" className="h-9 rounded-xl" onClick={() => setMode(mode === "reject" ? null : "reject")}>Отклонить</Button>
      </div>
      {mode === "reject" && (
        <ReasonForm kind="reject_request" busy={!!busy} confirm="Отклонить запрос"
                    onSave={(reason) => decide({ approve: false, reason }, "Запрос отклонён")} />
      )}
      {mode === "schedule" && (
        <ScheduleOnly points={points} busy={!!busy} trainers={trainers}
                      onSave={(body) => decide({ approve: true, ...body }, "Новая дата назначена, кандидат и тренер подтвердят")} />
      )}
    </li>
  );
}

function ScheduleOnly({ points, trainers, busy, onSave }: { points: string[]; trainers: Trainer[]; busy: boolean;
  onSave: (body: { date: string; time: string; location: string; trainer_id?: number }) => void }) {
  const [date, setDate] = useState(moscowToday());
  const [time, setTime] = useState("11:00");
  const [location, setLocation] = useState(points[0] ?? "");
  const [trainer, setTrainer] = useState<number | null>(null);
  return (
    <div className="row-in space-y-2 rounded-2xl bg-muted/50 p-3">
      <div className="grid grid-cols-2 gap-2">
        <input type="date" className={field} min={moscowToday()} value={date} onChange={(e) => setDate(e.target.value)} />
        <input type="time" className={field} value={time} onChange={(e) => e.target.value && setTime(e.target.value)} />
      </div>
      {points.length > 0 ? (
        <select className={field} aria-label="Место" value={location} onChange={(e) => setLocation(e.target.value)}>
          {points.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      ) : <input className={field} placeholder="Место" value={location} onChange={(e) => setLocation(e.target.value.slice(0, 100))} />}
      <select className={field} aria-label="Тренер" value={trainer ?? ""} onChange={(e) => setTrainer(e.target.value ? Number(e.target.value) : null)}>
        <option value="">Тренер — без изменений</option>
        {trainers.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select>
      <Button className="press h-11 w-full rounded-xl" disabled={busy || !location.trim()}
              onClick={() => onSave({ date, time, location: location.trim(), ...(trainer ? { trainer_id: trainer } : {}) })}>
        {busy && <LoaderCircle className="animate-spin" />} Одобрить с новой датой
      </Button>
    </div>
  );
}

function TrainingForm({ a, trainers, points, busy, onSave }: {
  a: Application; trainers: Trainer[]; points: string[]; busy: boolean;
  onSave: (body: { date: string; time: string; location: string; trainer_id?: number | null }) => void;
}) {
  const [date, setDate] = useState(a.training_date ?? moscowToday());
  const [time, setTime] = useState(a.training_time ?? "11:00");
  const [location, setLocation] = useState(a.training_location ?? points[0] ?? "");
  const [trainer, setTrainer] = useState<number | null>(a.trainer?.id ?? null);
  return (
    <div className="row-in space-y-2 rounded-2xl bg-muted/50 p-3">
      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Дата</span>
          <input type="date" className={field} min={moscowToday()} value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="space-y-1"><span className="text-xs text-muted-foreground">Время</span>
          <input type="time" className={field} value={time} onChange={(e) => e.target.value && setTime(e.target.value)} /></label>
      </div>
      {points.length > 0 ? (
        <select className={field} aria-label="Место" value={location} onChange={(e) => setLocation(e.target.value)}>
          {points.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      ) : (
        <input className={field} placeholder="Место" value={location} onChange={(e) => setLocation(e.target.value.slice(0, 100))} />
      )}
      <select className={field} aria-label="Тренер" value={trainer ?? ""} onChange={(e) => setTrainer(e.target.value ? Number(e.target.value) : null)}>
        {trainers.map((t) => <option key={t.id} value={t.id}>{t.name}{t.username ? ` @${t.username}` : ""}</option>)}
      </select>
      <Button className="press h-11 w-full rounded-xl" disabled={busy || !location.trim() || !trainer}
              onClick={() => onSave({ date, time, location: location.trim(), trainer_id: trainer })}>
        {busy && <LoaderCircle className="animate-spin" />} Отправить кандидату и тренеру
      </Button>
      <p className="text-xs text-muted-foreground">Кандидат и тренер получат сообщение и подтвердят его кнопкой. Перенос — не отмена. Напоминания кандидату — накануне вечером и утром.</p>
    </div>
  );
}

function ContactForm({ me, busy, onSave }: { me: User; busy: boolean; onSave: (manager: string) => void }) {
  const [manager, setManager] = useState(me.username ?? "");
  return (
    <div className="row-in space-y-2 rounded-2xl bg-muted/50 p-3">
      <p className="text-sm">Кандидат получит: «Хорошие новости! Мы готовы вас рассмотреть… Свяжитесь с нашим представителем: @{manager || "…"}» и кнопки «Я написал менеджеру» / «Не могу написать первым».</p>
      <input className={field} placeholder="@username менеджера" value={manager}
             onChange={(e) => setManager(e.target.value.replace(/[^A-Za-z0-9_@]/g, "").slice(0, 33))} />
      <Button className="press h-11 w-full rounded-xl" disabled={busy || !manager.replace("@", "")} onClick={() => onSave(manager)}>
        {busy && <LoaderCircle className="animate-spin" />} Написать кандидату
      </Button>
    </div>
  );
}

/** 18.11: the owner corrects the internship's paid counter by hand — reaching the target closes the
 * internship («Принять в команду» happens by itself, without the referrer's bonus). */
function PaidUnits({ a, p, onDone }: { a: Application; p: StageProgress; onDone: () => void }) {
  const [value, setValue] = useState<string | null>(null);
  const { busy, run } = useAction();
  const paid = p.paid ?? p.units;
  const save = async () => {
    const n = Number(value);
    if (!value || Number.isNaN(n)) return;
    try {
      const res = await run("paid", (k) => api.admin.setInternshipUnits(a.id, Math.min(Math.max(0, Math.round(n)), p.target), k),
        n >= p.target ? "Цель закрыта — стажировка завершена, кандидат в команде" : "Зачёт стажировки обновлён");
      if (res) onDone();
      setValue(null);
    } catch {
      /* toast shown */
    }
  };
  return (
    <div className="flex items-center gap-2">
      {value === null ? (
        <button className="text-xs font-semibold text-primary" onClick={() => setValue(String(Math.round(paid)))}>
          Править зачёт ({Math.round(paid)}/{p.target})
        </button>
      ) : (
        <>
          <input inputMode="numeric" aria-label="Зачтено выплатами, шт." value={value} autoFocus
                 onChange={(e) => setValue(e.target.value.replace(/\D/g, "").slice(0, 3))}
                 className="h-8 w-16 rounded-lg border-0 bg-card px-2 text-sm font-semibold tabular outline-none focus:ring-2 focus:ring-ring/25" />
          <Button size="sm" className="press h-8 rounded-lg px-2" disabled={!!busy} onClick={save}>Сохранить</Button>
          <Button size="sm" variant="ghost" className="h-8 rounded-lg px-2" onClick={() => setValue(null)}>Отмена</Button>
        </>
      )}
    </div>
  );
}

function StageForm({ a, busy, onSave }: { a: Application; busy: boolean; onSave: (counted: number, bonus: number) => void }) {
  const p = a.progress!;
  const [counted, setCounted] = useState(String(Math.min(p.units, p.target) || p.units));
  const [bonus, setBonus] = useState<number | "custom">(p.stage === 1 ? 500 : 1000);
  const [custom, setCustom] = useState("");
  const amount = bonus === "custom" ? Number(custom) || 0 : bonus;
  return (
    <div className="row-in space-y-3 rounded-2xl bg-muted/50 p-3">
      <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">
          Засчитать, шт. — сделано {p.units}{p.stage === 1 ? " (можно меньше: ошибки, брак)" : ""}
        </span>
        <input inputMode="numeric" className={cn(field, "tabular")} value={counted} onChange={(e) => setCounted(e.target.value.replace(/\D/g, "").slice(0, 6))} />
      </label>
      {a.referrer ? (
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">Бонус пригласившему ({a.referrer})</p>
          <div className="flex flex-wrap gap-1.5">
            {([0, 500, 1000, "custom"] as const).map((b) => (
              <button key={b} type="button" onClick={() => setBonus(b)}
                      className={cn("press h-9 rounded-full px-3.5 text-sm font-medium", bonus === b ? "bg-primary text-primary-foreground" : "bg-card")}>
                {b === 0 ? "Без бонуса" : b === "custom" ? "Своя сумма" : `${b} ₽`}
              </button>
            ))}
          </div>
          {bonus === "custom" && (
            <input inputMode="numeric" className={field} placeholder="Сумма, ₽" value={custom}
                   onChange={(e) => setCustom(e.target.value.replace(/\D/g, "").slice(0, 6))} />
          )}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Кандидат пришёл без приглашения — бонус начислять некому.</p>
      )}
      <Button className="press h-11 w-full rounded-xl" disabled={busy || counted === ""}
              onClick={() => onSave(Number(counted), a.referrer ? amount : 0)}>
        {busy && <LoaderCircle className="animate-spin" />}
        {p.stage === 1 ? "Принять в команду" : "Засчитать второй этап"}{a.referrer && amount ? ` и начислить ${amount} ₽` : ""}
      </Button>
    </div>
  );
}
