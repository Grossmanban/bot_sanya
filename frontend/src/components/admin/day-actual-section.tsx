"use client";

import { useState } from "react";
import { ChevronDown, CircleCheck, History, LoaderCircle, TriangleAlert, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type DayActual } from "@/lib/api";
import { dayMonth, parseDay, plural, rub, WEEKDAYS } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";

/** Owners enter the day's global actual output; the team result decides below-plan reports (ТЗ 3.5). */
export function DayActualSection({ onChanged }: { onChanged: () => void }) {
  const { data, reload } = useLoad(() => api.admin.dayActuals(), []);
  if (!data) return <Skeleton className="h-80 rounded-3xl" />;
  const missing = data.missing.length;

  return (
    <div className="space-y-4">
      <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
        <h2 className="font-display text-xl font-semibold">Общий факт дня</h2>
        <p className="mt-1.5 text-[15px] text-hero-muted">
          Сколько всего сделала команда. Если общий план выполнен, отчёты с недобором принимаются автоматически.
          Пока факт не введён, штрафовать за недобор нельзя.
        </p>
        {missing > 0 && (
          <p className="mt-3 inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1.5 text-sm font-semibold">
            <TriangleAlert className="size-4 text-wait" />
            Не введён за {missing} {plural(missing, "день", "дня", "дней")}
          </p>
        )}
      </section>

      {data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift">За последние две недели смен не было.</p>
      ) : (
        <ul className="space-y-3">
          {data.items.map((d) => (
            <DayCard key={d.date} day={d} missing={data.missing.includes(d.date)} onSaved={async () => {
              await reload();
              onChanged();
            }} />
          ))}
        </ul>
      )}
    </div>
  );
}

function dayTitle(d: DayActual) {
  if (d.is_today) return "Сегодня";
  const { weekday } = parseDay(d.date);
  return `${WEEKDAYS[weekday]}, ${dayMonth(d.date)}`;
}

function DayCard({ day, missing, onSaved }: { day: DayActual; missing: boolean; onSaved: () => void }) {
  const [editing, setEditing] = useState(day.actual == null && (missing || day.is_today));
  const [value, setValue] = useState(day.actual != null ? String(day.actual) : "");
  const [showLog, setShowLog] = useState(false);
  const [focus, setFocus] = useState(false);
  const { busy, run } = useAction();
  const n = Number(value);
  const preview = value !== "" ? n >= day.plan : null;

  async function save() {
    try {
      const res = await run("save", (k) => api.admin.setDayActual(day.date, n, k));
      if (!res) return;
      if (res.met) {
        toast.success(res.approved ? `Команда выполнила план. Принято автоматически: ${res.approved}` : "Команда выполнила план");
      } else {
        toast("План команды не выполнен — недоборы проверяются вручную");
      }
      setEditing(false);
      onSaved();
    } catch {
      /* toast shown */
    }
  }

  return (
    <li className={cn("row-in space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6", missing && "ring-1 ring-wait/40")}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold">{dayTitle(day)}</p>
          <p className="text-sm text-muted-foreground">
            {day.shifts} {plural(day.shifts, "смена", "смены", "смен")} · отчёты {day.reported} из {day.shifts}
            {day.pending > 0 && <span className="font-semibold text-wait"> · ждут {day.pending}</span>}
          </p>
        </div>
        {day.met != null && (
          <span className={cn("shrink-0 rounded-full px-3 py-1 text-xs font-semibold", day.met ? "bg-go-soft text-go" : "bg-cancel-soft text-cancel")}>
            {day.met ? "план выполнен" : "план не выполнен"}
          </span>
        )}
      </div>

      <dl className="grid grid-cols-3 gap-3">
        <Stat label="План" value={day.plan} />
        <Stat label="Из отчётов" value={day.facts} muted />
        <Stat label="Общий факт" value={day.actual} tone={day.met == null ? undefined : day.met ? "go" : "cancel"} />
      </dl>

      {editing ? (
        <div className="space-y-2">
          <div className="flex gap-2">
            <input inputMode="numeric" autoFocus={focus} aria-label="Общий факт, шт." placeholder="Общий факт, шт." value={value}
                   onChange={(e) => setValue(e.target.value.replace(/\D/g, "").slice(0, 7))}
                   className="h-12 min-w-0 flex-1 rounded-2xl border-0 bg-muted px-4 font-display text-lg font-semibold tabular outline-none placeholder:font-sans placeholder:text-[15px] placeholder:font-normal focus:ring-2 focus:ring-ring/25" />
            <Button className="press h-12 rounded-2xl px-5 font-semibold" disabled={!!busy || value === ""} onClick={save}>
              {busy && <LoaderCircle className="animate-spin" />} Сохранить
            </Button>
          </div>
          {preview != null && (
            <p className={cn("text-sm", preview ? "text-go" : "text-cancel")}>
              {preview
                ? `Команда выполнит план${day.pending ? ` — ${day.pending} ${plural(day.pending, "отчёт будет принят", "отчёта будут приняты", "отчётов будут приняты")} автоматически` : ""}`
                : `Не хватает ${day.plan - n} шт. до плана — недоборы останутся на ручной проверке`}
            </p>
          )}
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
          <span>{day.set_by ? `Ввёл ${day.set_by}, ${day.set_at?.slice(11, 16)} ${dayMonth(day.set_at!.slice(0, 10))}` : "Ещё не введён"}</span>
          <Button variant="secondary" size="sm" className="press h-9 rounded-xl" onClick={() => {
            tapHaptic();
            setFocus(true);
            setEditing(true);
          }}>
            {day.actual == null ? "Ввести" : "Изменить"}
          </Button>
        </div>
      )}

      {day.fined.length > 0 && <AnnulBlock day={day} onDone={onSaved} />}

      {day.log.length > 0 && (
        <div>
          <button className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground" onClick={() => setShowLog((v) => !v)}>
            <History className="size-4" /> История изменений ({day.log.length})
            <ChevronDown className={cn("size-4 transition-transform", showLog && "rotate-180")} />
          </button>
          {showLog && (
            <ul className="mt-2 space-y-1 text-sm">
              {day.log.map((l) => (
                <li key={l.id} className="flex justify-between gap-3 tabular">
                  <span>{l.old_value ?? "—"} → <b>{l.new_value}</b> <span className="text-muted-foreground">(план {l.plan})</span></span>
                  <span className="text-right text-muted-foreground">{l.actor_name}, {l.created_at.slice(11, 16)} {dayMonth(l.created_at.slice(0, 10))}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

function Stat({ label, value, tone, muted }: { label: string; value: number | null; tone?: "go" | "cancel"; muted?: boolean }) {
  return (
    <div className="rounded-2xl bg-muted/60 px-3 py-2.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("font-display text-xl font-semibold tabular", tone === "go" && "text-go", tone === "cancel" && "text-cancel",
        muted && "text-muted-foreground", value == null && "text-muted-foreground")}>
        {value ?? "—"}
      </dd>
    </div>
  );
}

/** «Авто-компенсация»: the team met the plan, so shortfall fines given earlier can be annulled. */
function AnnulBlock({ day, onDone }: { day: DayActual; onDone: () => void }) {
  const { busy, run } = useAction();
  const total = day.fined.reduce((s, f) => s + (f.amount ?? 0), 0);
  async function annul(ids: number[] | null, name: string) {
    try {
      const res = await run(name, (k) => api.admin.annulDayFines(day.date, ids, k));
      if (res) toast.success(`Аннулировано: ${res.annulled}`);
      onDone();
    } catch {
      /* toast shown */
    }
  }
  return (
    <div className="space-y-3 rounded-2xl bg-wait-soft p-4">
      <p className="text-sm">
        <b>Команда справилась</b>, но за недобор уже выданы штрафы ({rub(total)}). Их можно аннулировать.
      </p>
      <ul className="space-y-2">
        {day.fined.map((f) => (
          <li key={f.shift_id} className="flex items-center justify-between gap-3 text-sm">
            <span className="flex min-w-0 gap-1.5"><span className="truncate">{f.name}</span><b className="shrink-0">{rub(f.amount)}</b></span>
            <Button size="sm" variant="secondary" className="press h-8 shrink-0 rounded-lg bg-card" disabled={!!busy}
                    onClick={() => annul([f.shift_id], `one-${f.shift_id}`)}>
              {busy === `one-${f.shift_id}` ? <LoaderCircle className="animate-spin" /> : <Undo2 />} Аннулировать
            </Button>
          </li>
        ))}
      </ul>
      {day.fined.length > 1 && (
        <Button className="press h-11 w-full rounded-xl font-semibold" disabled={!!busy} onClick={() => annul(null, "all")}>
          {busy === "all" ? <LoaderCircle className="animate-spin" /> : <CircleCheck />} Аннулировать все
        </Button>
      )}
    </div>
  );
}
