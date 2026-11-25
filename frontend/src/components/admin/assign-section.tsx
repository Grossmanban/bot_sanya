"use client";

import { useRef, useState } from "react";
import { Coins, MapPin, Pencil, RefreshCw, Send } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type AssignDay, type User } from "@/lib/api";
import { WEEKDAYS, addDays, bonusText, dayMonth, parseDay, planText, plural, timeRange } from "@/lib/format";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import type { EditableShift } from "./admin-shift-sheet";
import { Templates } from "./broadcast-section";
import { RichEditor, type RichEditorHandle } from "./rich-editor";

type Item = AssignDay["items"][number];
type Mode = "all" | "point";

const GOING = ["active", "restored", "awaiting_approval"];  // stands in the schedule that day
const LIMIT = 4000;  // Telegram's message limit

/** What the schedule texts may carry — every person gets their own values; a tap puts it where the caret is. */
const VARIABLES = [
  { name: "имя", hint: "имя сотрудника" },
  { name: "дата", hint: "дата графика — например, 25.11 (ср)" },
  { name: "точка", hint: "точка сотрудника на этот день" },
  { name: "коллеги", hint: "кто ещё выходит в этот день — по строке на каждого" },
  { name: "надбавка", hint: "пороги и суммы надбавки точки в этот день (если она действует)" },
  { name: "отправитель", hint: "ваши фамилия и имя" },
];

const same = (a: string | null, b: string | null) => (a ?? "").toLocaleLowerCase("ru") === (b ?? "").toLocaleLowerCase("ru");

/** What the person has on the day, in one line. */
function recordText(it: Item): string {
  if (!it.shift_id) return "нет записи";
  if (it.status === "pre_declared_off") return "выходной";
  if (it.status === "cancelled" || it.status === "finally_cancelled") return "смена отменена";
  if (!it.location) return `${it.start_time ? timeRange(it) : "смена без времени"} · точка не назначена`;
  if (!it.start_time) return it.volume == null ? `${it.location} · время и объём уточняются` : `${it.location}, ${it.volume} шт. · время уточняется`;
  return planText(it);
}

export function AssignSection({ version, me, onEdit }: { version: number; me: User; onEdit: (s: EditableShift) => void }) {
  const today = moscowToday();
  const tomorrow = addDays(today, 1);
  const [day, setDay] = useState(tomorrow);
  const { data, loading, reload } = useLoad(() => api.admin.assignDay(day), [version, day]);
  const [picking, setPicking] = useState<number | null>(null);           // whose point is being picked
  const [replace, setReplace] = useState<{ item: Item; location: string | null } | null>(null);  // awaits a confirmation
  const { busy, run } = useAction();
  const other = day !== today && day !== tomorrow;

  const items = data?.items ?? [];
  const points = data?.points ?? [];
  const going = items.filter((it) => it.shift_id && GOING.includes(it.status ?? ""));
  const past = day < today;  // a past day can be looked at, not distributed (the server refuses it)

  /** who is where on the day — the same picture the «Распред» shows, within what this manager may distribute */
  const byPlace = new Map<string, Item[]>();
  for (const it of going) byPlace.set(it.location ?? "", [...(byPlace.get(it.location ?? "") ?? []), it]);
  const groups = [...byPlace.entries()].sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b, "ru")));
  const assigned = going.filter((it) => it.location).length;

  async function assign(item: Item, location: string | null) {
    try {
      const res = await run(`assign-${item.user_id}`, (k) => api.admin.assignPoint(item.user_id, day, location, k));
      if (res?.changed) {
        toast.success(!location ? "Точка снята" : item.location ? `Точка изменена: ${location}`
          // a shift that had no point is not «created» — it only got its point
          : item.shift_id ? `Точка назначена: ${location}` : `Смена создана: точка «${location}», время уточняется`);
      }
      setPicking(null);
      await reload();
    } catch {
      /* toast shown */
    }
  }

  /** No record — a shift is made with the point; no point — it is set; another point — confirmed first. */
  function pick(item: Item, location: string | null) {
    tapHaptic();
    if (same(item.location, location)) {
      setPicking(null);
      return;
    }
    if (!item.location || !item.shift_id) {
      void assign(item, location);
      return;
    }
    setReplace({ item, location });
  }

  /** the shift's time and volume are filled in the usual edit sheet («День») */
  async function openShift(item: Item) {
    try {
      const res = await api.admin.day(day);
      const shift = res.items.find((x) => x.id === item.shift_id);
      if (shift) onEdit({ ...shift });
    } catch {
      /* toast shown by the request layer */
    }
  }

  const tab = (active: boolean) =>
    cn("press relative h-10 flex-1 rounded-xl text-sm font-semibold transition-colors",
      active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground");
  const caption = day === today ? "Сегодня" : day === tomorrow ? "Завтра"
    : `${WEEKDAYS[parseDay(day).weekday]}${day < today ? " · прошедший день" : ""}`;

  return (
    <div className="space-y-4">
      <div className="flex gap-1 rounded-2xl bg-card p-1 shadow-lift">
        {[today, tomorrow].map((d) => (
          <button key={d} onClick={() => { tapHaptic(); setDay(d); }} className={tab(day === d)}>
            {d === today ? "На сегодня" : "На завтра"}
          </button>
        ))}
        <label className={cn(tab(other), "grid cursor-pointer place-items-center")}>
          <span className="pointer-events-none truncate px-2">{other ? `📅 ${dayMonth(day)}` : "📅 Дата"}</span>
          <input type="date" aria-label="Распределение на другую дату" value={day}
                 onChange={(e) => { if (e.target.value) { tapHaptic(); setDay(e.target.value); } }}
                 className="absolute inset-0 cursor-pointer opacity-0" />
        </label>
      </div>

      {!data || data.date !== day ? (
        <Skeleton className="h-80 rounded-3xl" />
      ) : (
        <>
          <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm text-muted-foreground">{caption}</p>
                <h2 className="font-display text-2xl font-semibold">{dayMonth(day)}</h2>
                <p className="mt-1 text-[15px] text-muted-foreground">
                  {items.length
                    ? `${items.length} ${plural(items.length, "человек", "человека", "человек")} в вашем ведении · назначено ${assigned} · выходят ${going.length}`
                    : "Нет людей, которых вы вправе распределять."}
                </p>
                {past && (
                  <p className="mt-2 rounded-xl bg-wait-soft px-3 py-2 text-sm text-wait">
                    Прошедший день: точки здесь не назначают — правка смены в «Дне».
                  </p>
                )}
              </div>
              <button onClick={reload} aria-label="Обновить" className="press grid size-10 shrink-0 place-items-center rounded-full bg-muted">
                <RefreshCw className={cn("size-4", loading && "animate-spin")} />
              </button>
            </div>

            {/* who is where on the day */}
            {groups.length > 0 && (
              <ul className="mt-4 space-y-2">
                {groups.map(([place, people]) => {
                  const point = points.find((p) => same(p.name, place));
                  const badge = bonusText(point?.bonus_today);
                  return (
                    <li key={place || "-"} className="rounded-2xl bg-muted/50 px-4 py-3">
                      <p className="flex flex-wrap items-center gap-x-2 text-[15px] font-semibold">
                        <MapPin className="size-4 text-muted-foreground" />
                        {place || "Без точки"}
                        <span className="text-sm font-normal text-muted-foreground">{people.length}</span>
                        {badge && (
                          <span className="inline-flex items-center gap-1 text-xs font-medium text-go">
                            <Coins className="size-3.5" />
                            {point?.bonus_today?.weekday_ok === false ? "надбавка · в этот день не действует" : "надбавка"}
                          </span>
                        )}
                      </p>
                      <p className="mt-0.5 text-sm text-muted-foreground">{people.map((p) => p.name).join(", ")}</p>
                      {badge && <p className="mt-1 text-xs text-muted-foreground">{badge}</p>}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
            <div className="px-5 pt-5 pb-2 md:px-6">
              <h3 className="font-semibold">Кому какая точка</h3>
              <p className="text-sm text-muted-foreground">
                Нет смены — создадим её с этой точкой, время уточняется. Есть смена — поставим или поменяем точку.
              </p>
            </div>
            {points.length === 0 ? (
              <p className="px-5 pb-5 text-sm text-muted-foreground md:px-6">Точек пока нет — добавьте их в «Настройки → Точки».</p>
            ) : items.length === 0 ? null : (
              <ul className="divide-y border-t">
                {items.map((it) => {
                  const open = picking === it.user_id;
                  const stuck = !it.assignable || past;
                  return (
                    <li key={it.user_id} className={cn("px-5 py-3.5 md:px-6", stuck && "opacity-60")}>
                      {/* the name on its own line — on a phone the buttons took its room */}
                      <div className="flex items-center justify-between gap-3">
                        <p className="min-w-0 truncate font-medium">
                          {it.name}
                          {it.trainee && <span className="ml-1.5 rounded-md bg-wait-soft px-1.5 py-0.5 text-xs font-semibold text-wait">стажёр</span>}
                        </p>
                        <div className="flex shrink-0 items-center gap-1.5">
                          {!past && it.shift_id && it.assignable && (!it.start_time || it.volume == null) && (
                            <Button size="icon" variant="ghost" className="press size-9 rounded-xl" disabled={!!busy}
                                    aria-label="Изменить время и объём" title="Изменить время и объём" onClick={() => openShift(it)}>
                              <Pencil className="size-4" />
                            </Button>
                          )}
                          {stuck ? (
                            past ? null : <span className="text-xs text-muted-foreground">в «Дне»</span>
                          ) : (
                            <Button size="sm" variant={open ? "secondary" : "outline"} className="press h-9 rounded-xl px-3"
                                    aria-expanded={open} disabled={!!busy && busy !== `assign-${it.user_id}`}
                                    onClick={() => { tapHaptic(); setPicking(open ? null : it.user_id); }}>
                              {open ? "Закрыть" : it.location ? "Другая точка" : "Назначить"}
                            </Button>
                          )}
                        </div>
                      </div>
                      <p className="mt-0.5 text-sm text-muted-foreground">{recordText(it)}</p>
                      {open && (
                        <div className="row-in mt-3 space-y-2">
                          <div className="flex flex-wrap gap-2">
                            {points.map((p) => {
                              const picked = same(it.location, p.name);
                              const full = !!p.capacity && p.taken >= p.capacity && !picked;
                              return (
                                <button key={p.id} type="button" disabled={full} aria-pressed={picked}
                                        onClick={() => pick(it, p.name)}
                                        className={cn("press inline-flex h-10 items-center gap-1.5 rounded-xl px-3.5 text-sm font-semibold disabled:opacity-40",
                                          picked ? "bg-primary text-primary-foreground" : "bg-muted")}>
                                  {p.bonus_today?.tiers.length ? <Coins className="size-4 text-go" /> : null}
                                  {p.name}
                                  {p.capacity ? <span className={cn("text-xs font-normal", picked ? "opacity-70" : "text-muted-foreground")}>{p.taken} из {p.capacity}</span> : null}
                                </button>
                              );
                            })}
                            {it.location && (
                              <button type="button" onClick={() => pick(it, null)}
                                      className="press inline-flex h-10 items-center rounded-xl px-3.5 text-sm font-semibold text-muted-foreground hover:bg-muted">
                                Снять точку
                              </button>
                            )}
                          </div>
                          {/* the allowances of the points, spelled out — and whether they act on this very weekday */}
                          {points.filter((p) => p.bonus_today?.tiers.length).map((p) => (
                            <p key={p.id} className="text-xs text-muted-foreground">
                              <span className={p.bonus_today?.weekday_ok ? "text-go" : "line-through"}>
                                {bonusText(p.bonus_today)}
                              </span>
                              {p.bonus_today?.weekday_ok ? "" : " — в этот день не действует"}
                            </p>
                          ))}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <NoticeComposer day={day} going={going.length} signedByRole={me.role === "owner" || me.role === "admin"} />
        </>
      )}

      <AlertDialog open={!!replace} onOpenChange={(o) => !o && setReplace(null)}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">
              {replace?.location ? "Заменить точку?" : "Снять точку?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {replace && (
                <>
                  {replace.item.name} — на {dayMonth(day)} уже {replace.item.location
                    ? `назначена точка «${replace.item.location}»`
                    : "есть смена"}. {replace.location ? `Заменить на «${replace.location}»?` : "Снять точку? Человек останется в графике без точки."}
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Оставить</AlertDialogCancel>
            <AlertDialogAction className="rounded-xl"
                               onClick={() => { if (replace) void assign(replace.item, replace.location); }}>
              {replace?.location ? "Заменить" : "Снять"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** 19.11: «Отправить график» — a personal message per person through the usual broadcast machinery: the texts
 * of the «Распределение» kind, the visual editor, the variables; by the button only. */
function NoticeComposer({ day, going, signedByRole }: { day: string; going: number; signedByRole: boolean }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("all");
  const [signed, setSigned] = useState(true);
  const [confirm, setConfirm] = useState(false);
  const [length, setLength] = useState(0);
  const editor = useRef<RichEditorHandle>(null);
  const { busy, run } = useAction();

  async function send() {
    const out = editor.current?.getHtml() ?? { html: "", length: 0 };
    if (!out.html.replace(/<[^>]+>/g, "").trim()) {
      toast.error("Напишите текст или выберите шаблон");
      return;
    }
    try {
      const res = await run("notice", (k) => api.admin.assignNotice(day, out.html, mode, signed, k));
      if (res) {
        toast.success(res.repeated
          ? "Этот график уже отправлен"
          : `График отправлен: ${going} ${plural(going, "человек", "человека", "человек")}`);
      }
      setConfirm(false);
    } catch {
      /* toast shown */
    }
  }

  if (!open) {
    return (
      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h3 className="font-semibold">Отправить график</h3>
        <p className="text-sm text-muted-foreground">
          {going
            ? `Каждому из ${going} ${plural(going, "человека", "человек", "человек")} придёт сообщение с его точкой и коллегами на ${dayMonth(day)}.`
            : "Сначала распределите точки — или люди записываются сами."}
        </p>
        <Button className="press mt-4 h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!going}
                onClick={() => { tapHaptic(); setOpen(true); }}>
          <Send /> Составить сообщение
        </Button>
      </section>
    );
  }
  const seg = (on: boolean) => cn("press h-9 flex-1 rounded-xl text-sm font-semibold", on ? "bg-primary text-primary-foreground" : "text-muted-foreground");
  return (
    <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div>
        <h3 className="font-semibold">Отправить график</h3>
        <p className="text-sm text-muted-foreground">
          Каждому — своё сообщение. Значения подставятся сами: точка, коллеги и надбавка этой точки в этот день.
        </p>
      </div>
      <div>
        <span className="text-sm text-muted-foreground">Что видеть о коллегах</span>
        <div className="mt-1.5 flex gap-1 rounded-2xl bg-muted p-1">
          <button className={seg(mode === "all")} onClick={() => setMode("all")}>Весь список</button>
          <button className={seg(mode === "point")} onClick={() => setMode("point")}>Только своя точка</button>
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          {mode === "all" ? "Каждому — кто на какую точку назначен в этот день." : "Каждому — его точка и коллеги на этой же точке."}
        </p>
      </div>
      <Templates editor={editor} kind="assign" variables={VARIABLES} autoPick />
      <RichEditor ref={editor} placeholder="Например: Привет, {имя}! Завтра работаем на точке {точка}." onChange={setLength} />
      <p className={cn("-mt-2 text-right text-xs tabular", length > LIMIT ? "text-cancel" : "text-muted-foreground")}>
        {length} / {LIMIT}
      </p>
      {signedByRole && (
        <label className="flex cursor-pointer items-center gap-3">
          <span className="min-w-0 flex-1 text-sm">
            Подписать сообщение
            <span className="block text-xs text-muted-foreground">Владелец и администратор могут отправить без подписи; тренер и инструктор подписываются всегда.</span>
          </span>
          <Switch checked={signed} onCheckedChange={setSigned} />
        </label>
      )}
      <div className="flex gap-2">
        <Button variant="ghost" className="h-12 flex-1 rounded-2xl" onClick={() => setOpen(false)}>Назад</Button>
        <Button className="press h-12 flex-[2] rounded-2xl text-[15px] font-semibold" disabled={!!busy || !going}
                onClick={() => setConfirm(true)}>
          <Send /> Отправить {going} {plural(going, "человеку", "людям", "людям")}
        </Button>
      </div>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Отправить график?</AlertDialogTitle>
            <AlertDialogDescription>
              Сообщение уйдёт в бот: {going} {plural(going, "человек", "человека", "человек")} — каждому с его точкой
              на {dayMonth(day)}.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Проверю ещё</AlertDialogCancel>
            <AlertDialogAction className="rounded-xl" onClick={send}>Отправить</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
