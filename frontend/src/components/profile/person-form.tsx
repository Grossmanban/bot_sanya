"use client";

import { useState } from "react";
import { Check, ChevronLeft, LoaderCircle, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PersonData, ProfileStatus } from "@/lib/api";
import { plural } from "@/lib/format";
import { TONE_CHIP } from "@/lib/tone";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const field = "h-12 w-full rounded-2xl border-0 bg-muted px-4 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";
const STEPS = ["ФИО", "О себе", "Дни работы", "Проверка"];

export const STATUS_TEXT: Record<ProfileStatus, { label: string; tone: string }> = {
  // §27: the shared scale — «не заполнен» waits for the person, so it is yellow like the «не заполнены» in «Команде»
  complete: { label: "Заполнен, одобрен", tone: TONE_CHIP.ok },
  incomplete: { label: "Не заполнен", tone: TONE_CHIP.wait },
  pending: { label: "Ожидает рассмотрения", tone: TONE_CHIP.wait },
  rejected: { label: "Отклонён — нужно исправить", tone: TONE_CHIP.bad },
};

export function StatusPill({ status, prefix }: { status: ProfileStatus; prefix?: string }) {
  const s = STATUS_TEXT[status];
  return <span className={cn("inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold", s.tone)}>{prefix}{s.label}</span>;
}

/** ТЗ 28.10 §7: the profile, step by step — progress, what's missing, back and edit, the summary before sending.
 * The number of convenient days must equal the minimum — otherwise sending is blocked with the reason. */
export function PersonForm({ initial, busy, submitLabel, onSubmit }: {
  initial: PersonData; busy: boolean; submitLabel: string; onSubmit: (d: PersonData) => void;
}) {
  const [d, setD] = useState<PersonData>({ ...initial, work_days: [...(initial.work_days ?? [])] });
  const [step, setStep] = useState(0);
  const set = (patch: Partial<PersonData>) => setD((x) => ({ ...x, ...patch }));
  const name = (v: string) => v.replace(/[^A-Za-zА-Яа-яЁё' -]/g, "").slice(0, 50);
  const missing = [
    !d.last_name?.trim() && "фамилия", !d.first_name?.trim() && "имя", !d.middle_name?.trim() && "отчество",
    !d.age && "возраст", !d.metro?.trim() && "метро", !d.min_days && "минимум дней", d.work_days.length === 0 && "удобные дни",
  ].filter(Boolean) as string[];
  const daysOk = !!d.min_days && d.work_days.length === d.min_days;
  const ageOk = !!d.age && d.age >= 14 && d.age <= 80;
  const stepOk = [
    !!(d.last_name?.trim() && d.first_name?.trim() && d.middle_name?.trim()),
    ageOk && !!d.metro?.trim(),
    daysOk,
    missing.length === 0 && daysOk && ageOk,
  ];
  const toggleDay = (i: number) => set({ work_days: d.work_days.includes(i) ? d.work_days.filter((x) => x !== i) : [...d.work_days, i].sort() });

  return (
    <div className="space-y-4">
      <div className="flex gap-1.5" aria-label="Заполненность">
        {STEPS.map((s, i) => (
          <button key={s} type="button" onClick={() => setStep(i)} className="flex-1 space-y-1 text-left">
            <span className={cn("block h-1.5 rounded-full", i < step || stepOk[i] && i !== 3 ? "bg-go" : i === step ? "bg-primary" : "bg-muted")} />
            <span className={cn("block truncate text-[11px] font-semibold", i === step ? "text-foreground" : "text-muted-foreground")}>{s}</span>
          </button>
        ))}
      </div>
      {missing.length > 0 && (
        <p className="text-xs text-muted-foreground">Осталось заполнить: {missing.join(", ")}</p>
      )}

      {step === 0 && (
        <div className="space-y-3">
          {([["last_name", "Фамилия"], ["first_name", "Имя"], ["middle_name", "Отчество"]] as const).map(([k, label]) => (
            <label key={k} className="block space-y-1.5">
              <span className="text-sm text-muted-foreground">{label}</span>
              <input className={field} value={d[k] ?? ""} onChange={(e) => set({ [k]: name(e.target.value) })} />
            </label>
          ))}
        </div>
      )}
      {step === 1 && (
        <div className="space-y-3">
          <label className="block space-y-1.5">
            <span className="text-sm text-muted-foreground">Возраст</span>
            <input inputMode="numeric" className={field} value={d.age ?? ""}
                   onChange={(e) => set({ age: Number(e.target.value.replace(/\D/g, "").slice(0, 2)) || null })} />
            {d.age != null && !ageOk && <span className="text-xs text-cancel">От 14 до 80 лет</span>}
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm text-muted-foreground">Станция метро, где живёте</span>
            <input className={field} value={d.metro ?? ""} onChange={(e) => set({ metro: e.target.value.slice(0, 60) })} />
          </label>
        </div>
      )}
      {step === 2 && (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <span className="text-sm text-muted-foreground">Минимум рабочих дней в неделю</span>
            <div className="flex flex-wrap gap-1.5">
              {[1, 2, 3, 4, 5, 6, 7].map((n) => (
                <button key={n} type="button" onClick={() => set({ min_days: n })}
                        className={cn("press size-11 rounded-xl text-[15px] font-semibold", d.min_days === n ? "bg-primary text-primary-foreground" : "bg-muted")}>{n}</button>
              ))}
            </div>
          </div>
          <div className="space-y-1.5">
            <span className="text-sm text-muted-foreground">Удобные дни работы{d.min_days ? ` — выберите ровно ${d.min_days}` : ""}</span>
            <div className="flex flex-wrap gap-1.5">
              {WEEKDAYS.map((w, i) => (
                <button key={w} type="button" onClick={() => toggleDay(i)}
                        className={cn("press h-11 w-12 rounded-xl text-sm font-semibold", d.work_days.includes(i) ? "bg-primary text-primary-foreground" : "bg-muted")}>{w}</button>
              ))}
            </div>
            {!!d.min_days && !daysOk && (
              <p className="text-xs text-cancel">
                Выбрано {d.work_days.length} {plural(d.work_days.length, "день", "дня", "дней")}, а минимум — {d.min_days}: их должно быть столько же.
              </p>
            )}
          </div>
        </div>
      )}
      {step === 3 && (
        <dl className="divide-y rounded-2xl bg-muted/50 text-[15px]">
          {[
            ["ФИО", [d.last_name, d.first_name, d.middle_name].filter(Boolean).join(" "), 0],
            ["Возраст", d.age ?? "—", 1],
            ["Метро", d.metro || "—", 1],
            ["Минимум дней", d.min_days ?? "—", 2],
            ["Удобные дни", d.work_days.map((i) => WEEKDAYS[i]).join(", ") || "—", 2],
          ].map(([label, value, s]) => (
            <div key={label as string} className="flex items-center gap-3 px-4 py-3">
              <dt className="w-28 shrink-0 text-sm text-muted-foreground">{label}</dt>
              <dd className="min-w-0 flex-1 font-medium break-words">{value}</dd>
              <button type="button" aria-label={`Изменить: ${label}`} onClick={() => setStep(s as number)}
                      className="grid size-9 place-items-center rounded-lg text-muted-foreground hover:bg-muted"><Pencil className="size-4" /></button>
            </div>
          ))}
        </dl>
      )}

      <div className="flex gap-2">
        {step > 0 && (
          <Button variant="secondary" className="press h-12 rounded-2xl" onClick={() => setStep(step - 1)}><ChevronLeft /> Назад</Button>
        )}
        {step < 3 ? (
          <Button className="press h-12 flex-1 rounded-2xl text-[15px] font-semibold" disabled={!stepOk[step]} onClick={() => setStep(step + 1)}>
            Дальше
          </Button>
        ) : (
          <Button className="press h-12 flex-1 rounded-2xl text-[15px] font-semibold" disabled={busy || !stepOk[3]}
                  onClick={() => onSubmit({ ...d, last_name: d.last_name?.trim() ?? null, first_name: d.first_name?.trim() ?? null,
                    middle_name: d.middle_name?.trim() ?? null, metro: d.metro?.trim() ?? null })}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Check />} {submitLabel}
          </Button>
        )}
      </div>
    </div>
  );
}
