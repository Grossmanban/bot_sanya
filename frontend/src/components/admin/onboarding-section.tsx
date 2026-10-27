"use client";

import { useMemo, useState } from "react";
import { CalendarPlus, CalendarX, Check, GraduationCap, LoaderCircle, MessageCircle, RotateCcw, Send, UserX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Application, type User } from "@/lib/api";
import { ddmm, dayMonth, plural } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { openProfile } from "@/lib/telegram";
import { cn } from "@/lib/utils";

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
type Sort = "new" | "age" | "skill";

/** «Онбординг» (the customer's module 3): candidates from the questionnaire → training → internship → bonuses. */
export function OnboardingSection({ me }: { me: User }) {
  const [tab, setTab] = useState<Tab>("new");
  const { data, reload } = useLoad(() => api.admin.onboarding(tab), [tab]);
  const [age, setAge] = useState<[string, string]>(["", ""]);
  const [skill, setSkill] = useState<[number, number]>([1, 10]);
  const [days, setDays] = useState<number[]>([]);
  const [sort, setSort] = useState<Sort>("new");

  const items = useMemo(() => {
    const list = (data?.items ?? []).filter((a) =>
      (!age[0] || a.age >= Number(age[0])) && (!age[1] || a.age <= Number(age[1]))
      && a.communication >= skill[0] && a.communication <= skill[1]
      && days.every((d) => a.training_days.includes(d)));
    if (sort === "age") list.sort((a, b) => a.age - b.age);
    if (sort === "skill") list.sort((a, b) => b.communication - a.communication);
    return list;
  }, [data, age, skill, days, sort]);
  const filtered = !!(age[0] || age[1]) || skill[0] > 1 || skill[1] < 10 || days.length > 0;

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
            <select className={cn(field, "h-10 w-auto")} value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
              <option value="new">сначала новые</option>
              <option value="age">по возрасту</option>
              <option value="skill">по навыкам</option>
            </select>
            {filtered && (
              <Button variant="ghost" size="sm" className="rounded-lg" onClick={() => { setAge(["", ""]); setSkill([1, 10]); setDays([]); }}>
                Сбросить
              </Button>
            )}
          </div>
        </div>
      </details>

      {!data ? <Skeleton className="h-48 rounded-3xl" /> : items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">
          {filtered ? "Под фильтры никто не подходит." : "Здесь пока пусто."}
        </p>
      ) : (
        <ul className="space-y-3">
          {items.map((a) => (
            <Candidate key={a.id} a={a} me={me} trainers={data.trainers} points={data.points} onDone={reload} />
          ))}
        </ul>
      )}
    </div>
  );
}

type Panel = "training" | "reject" | "contact" | "stage" | "cancel" | null;

function Candidate({ a, me, trainers, points, onDone }: {
  a: Application; me: User; trainers: { id: number; name: string; username: string | null }[]; points: string[]; onDone: () => void;
}) {
  const [panel, setPanel] = useState<Panel>(null);
  const [broken, setBroken] = useState(false);
  const { busy, run } = useAction();
  const p = a.progress;
  const openTg = () => openProfile(a.username, a.telegram_id);
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

  return (
    <li className="row-in space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
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
          <p className="font-semibold">{a.full_name}</p>
          <p className="text-sm text-muted-foreground">
            {a.age} {plural(a.age, "год", "года", "лет")} · м. {a.metro}{a.username ? ` · @${a.username}` : ""}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Навыки {a.communication}/10 · от {a.min_days} {plural(a.min_days, "дня", "дней", "дней")} в неделю · анкета {ddmm(a.created_at.slice(0, 10))}
            {a.referrer && (
              <>
                {" · пригласил(а) "}
                {a.referrer_tg ? (
                  <button type="button" className={link} onClick={() => openProfile(a.referrer_tg!.username, a.referrer_tg!.telegram_id)}>
                    {a.referrer}
                  </button>
                ) : a.referrer}
              </>
            )}
            {a.earlier ? ` · анкет раньше: ${a.earlier}` : ""}
          </p>
          <div className="mt-2 flex flex-wrap gap-1">
            {a.training_days.map((d) => <span key={d} className="rounded-lg bg-muted px-2 py-0.5 text-xs font-semibold">{DAYS[d]}</span>)}
          </div>
        </div>
      </div>

      {a.training_date && a.status === "scheduled" && (
        <p className={cn("rounded-2xl px-4 py-3 text-sm", a.training_confirmed_at ? "bg-go-soft" : "bg-wait-soft")}>
          📅 Обучение {dayMonth(a.training_date)} в {a.training_time}, {a.training_location}
          {a.trainer ? ` · тренер ${a.trainer.name}` : ""}
          <span className={cn("block text-xs font-semibold", a.training_confirmed_at ? "text-go" : "text-wait")}>
            {a.training_confirmed_at ? "кандидат подтвердил запись" : "ждём подтверждения кандидата"}
          </span>
        </p>
      )}
      {a.contacted_at && a.status !== "rejected" && (
        <p className="text-xs text-muted-foreground">
          Написали через{" "}
          <button type="button" className={link} onClick={() => openProfile(a.contact_manager)}>@{a.contact_manager}</button>{" "}
          {ddmm(a.contacted_at.slice(0, 10))}:{" "}
          {a.contact_answer === "wrote" ? <b className="text-go">кандидат написал менеджеру</b>
            : a.contact_answer === "cant_write" ? <b className="text-wait">просит написать ему первым</b> : "ответа пока нет"}
        </p>
      )}
      {a.status === "rejected" && <p className="text-sm text-cancel">Отказ{a.reject_reason ? `: ${a.reject_reason}` : ""}{a.decided_at ? `, ${ddmm(a.decided_at.slice(0, 10))}` : ""}</p>}
      {p && (
        <div className="space-y-1.5">
          <div className="relative h-2 overflow-hidden rounded-full bg-muted">
            <span className={cn("absolute inset-y-0 left-0 rounded-full", p.review ? "bg-go" : "bg-primary")}
                  style={{ width: `${Math.min(100, (p.units / Math.max(p.target, 1)) * 100)}%` }} />
          </div>
          <p className="text-xs text-muted-foreground">
            {p.stage === 1 ? "Стажировка (учебные, не оплачиваются)" : "Работа после стажировки"}: <b className="text-foreground">{p.units} из {p.target}</b>
            {p.pending ? `, ещё ${p.pending} на проверке` : ""}{p.review ? " · ждёт проверки" : ""}
            {a.stage1_counted != null ? ` · стажировка засчитана: ${a.stage1_counted}` : ""}
          </p>
        </div>
      )}

      <div className="-mx-1 flex flex-wrap gap-1.5">
        <Button variant="secondary" size="sm" className="h-9 rounded-xl" onClick={openTg}><Send /> Профиль TG</Button>
        {(a.status === "new" || a.status === "scheduled") && (
          <>
            <Button size="sm" className="h-9 rounded-xl" onClick={() => setPanel(panel === "training" ? null : "training")}>
              <CalendarPlus /> {a.status === "new" ? "Назначить обучение" : "Изменить обучение"}
            </Button>
            <Button variant="secondary" size="sm" className="h-9 rounded-xl" onClick={() => setPanel(panel === "contact" ? null : "contact")}>
              <MessageCircle /> Связаться
            </Button>
          </>
        )}
        {a.status === "scheduled" && (
          <Button size="sm" className="h-9 rounded-xl bg-go text-white hover:bg-go/90" disabled={!!busy}
                  onClick={() => act("intern", (k) => api.admin.startInternship(a.id, k), "Стажировка началась: доступ к приложению открыт")}>
            {busy === "intern" ? <LoaderCircle className="animate-spin" /> : <GraduationCap />} Начать стажировку
          </Button>
        )}
        {(a.status === "internship" || (a.status === "done" && !a.stage2_at)) && (
          <Button size="sm" className={cn("h-9 rounded-xl", p?.review && "bg-go text-white hover:bg-go/90")}
                  onClick={() => setPanel(panel === "stage" ? null : "stage")}>
            <Check /> {a.status === "internship" ? "Проверить стажировку" : "Проверить второй этап"}
          </Button>
        )}
        {a.status === "scheduled" && (
          <Button variant="secondary" size="sm" className="h-9 rounded-xl" onClick={() => setPanel(panel === "cancel" ? null : "cancel")}>
            <CalendarX /> Отменить обучение
          </Button>
        )}
        {(a.status === "new" || a.status === "scheduled") && (
          <Button variant="ghost" size="sm" className="h-9 rounded-xl text-cancel hover:bg-cancel-soft hover:text-cancel"
                  onClick={() => setPanel(panel === "reject" ? null : "reject")}>
            <UserX /> {a.status === "scheduled" ? "Отказать в обучении" : "Отказать"}
          </Button>
        )}
        {a.status === "rejected" && (
          <Button variant="secondary" size="sm" className="h-9 rounded-xl" disabled={!!busy}
                  onClick={() => act("return", (k) => api.admin.returnApplication(a.id, k), "Возвращён в резерв: бот попросит заполнить анкету заново")}>
            {busy === "return" ? <LoaderCircle className="animate-spin" /> : <RotateCcw />} Вернуть в резерв
          </Button>
        )}
      </div>

      {panel === "training" && <TrainingForm a={a} trainers={trainers} points={points} busy={!!busy}
                                             onSave={(body) => act("train", (k) => api.admin.assignTraining(a.id, body, k),
                                               a.status === "new" ? "Обучение назначено, кандидат получил сообщение" : "Обучение изменено, кандидат получил сообщение")} />}
      {panel === "contact" && <ContactForm me={me} busy={!!busy}
                                           onSave={(manager) => act("contact", () => api.admin.contactApplication(a.id, manager), "Сообщение отправлено кандидату")} />}
      {panel === "cancel" && (
        <div className="row-in space-y-2 rounded-2xl bg-muted/50 p-3">
          <p className="text-sm">Обучение отменится, кандидат вернётся в «Ожидают рассмотрения» — это не отказ. Он и тренер получат сообщение, дату можно назначить заново.</p>
          <Button className="press h-11 w-full rounded-xl" disabled={!!busy}
                  onClick={() => act("cancel", (k) => api.admin.cancelTraining(a.id, k), "Обучение отменено, кандидат снова ожидает рассмотрения")}>
            {busy === "cancel" && <LoaderCircle className="animate-spin" />} Да, отменить обучение
          </Button>
        </div>
      )}
      {panel === "reject" && <RejectForm busy={!!busy}
                                         onSave={(reason) => act("reject", (k) => api.admin.rejectApplication(a.id, reason, k), "Кандидату отказано")} />}
      {panel === "stage" && p && <StageForm a={a} busy={!!busy}
                                            onSave={(counted, bonus) => act("stage", (k) => api.admin.confirmStage(a.id, p.stage, counted, bonus, k),
                                              bonus ? `Засчитано, бонус ${bonus} ₽ начислен пригласившему` : "Засчитано")} />}
    </li>
  );
}

function TrainingForm({ a, trainers, points, busy, onSave }: {
  a: Application; trainers: { id: number; name: string; username: string | null }[]; points: string[]; busy: boolean;
  onSave: (body: { date: string; time: string; location: string; trainer_id: number }) => void;
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
        <option value="">Тренер (любой сотрудник)</option>
        {trainers.map((t) => <option key={t.id} value={t.id}>{t.name}{t.username ? ` @${t.username}` : ""}</option>)}
      </select>
      <Button className="press h-11 w-full rounded-xl" disabled={busy || !location.trim() || !trainer}
              onClick={() => onSave({ date, time, location: location.trim(), trainer_id: trainer! })}>
        {busy && <LoaderCircle className="animate-spin" />} Отправить кандидату
      </Button>
      <p className="text-xs text-muted-foreground">Кандидат получит сообщение с кнопкой «Подтвердить запись», тренер — уведомление. Напоминания — накануне вечером и утром в день обучения.</p>
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

function RejectForm({ busy, onSave }: { busy: boolean; onSave: (reason: string | null) => void }) {
  const [reason, setReason] = useState("");
  return (
    <div className="row-in space-y-2 rounded-2xl bg-cancel-soft p-3">
      <input className={field} placeholder="Причина (необязательно) — кандидат её увидит" value={reason}
             onChange={(e) => setReason(e.target.value.slice(0, 300))} />
      <p className="text-xs text-muted-foreground">Кандидат получит отказ, бот перестанет ему отвечать, анкета уйдёт в «Отклонённые». Вернуть можно оттуда.</p>
      <Button className="press h-11 w-full rounded-xl bg-cancel text-white hover:bg-cancel/90" disabled={busy} onClick={() => onSave(reason.trim() || null)}>
        {busy && <LoaderCircle className="animate-spin" />} Да, отказать
      </Button>
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
        {p.stage === 1 ? "Засчитать стажировку" : "Засчитать второй этап"}{a.referrer && amount ? ` и начислить ${amount} ₽` : ""}
      </Button>
    </div>
  );
}
