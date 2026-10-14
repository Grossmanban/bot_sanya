"use client";

import { Minus, Plus } from "lucide-react";
import type { Point, Pricing, Shift, ShiftPayload } from "@/lib/api";
import { rub } from "@/lib/format";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";

export interface FormState {
  start_time: string;
  end_time: string;
  location: string;
  volume: string;
}

export function initialForm(prefill?: Partial<ShiftPayload> | null, fallbackLocation?: string): FormState {
  return {
    start_time: prefill?.start_time ?? "16:00",
    end_time: prefill?.end_time ?? "19:00",
    location: prefill?.location ?? fallbackLocation ?? "",
    volume: prefill?.volume ? String(prefill.volume) : "",
  };
}

export function prefillFrom(shift: Shift): Partial<ShiftPayload> {
  return {
    start_time: shift.start_time ?? undefined,
    end_time: shift.end_time ?? undefined,
    location: shift.location ?? undefined,
    volume: shift.volume ?? undefined,
  };
}

/** Returns the payload or an error message. Mirrors the server-side checks. */
export function validateForm(f: FormState): ShiftPayload | string {
  const time = /^([01]\d|2[0-3]):[0-5]\d$/;
  if (!time.test(f.start_time)) return "Укажи время начала";
  if (!time.test(f.end_time)) return "Укажи время окончания";
  if (f.end_time <= f.start_time) return "Конец должен быть позже начала";
  if (!f.location.trim()) return "Укажи точку";
  if (!/^\d+$/.test(f.volume.trim()) || Number(f.volume) <= 0) return "Объём — целое число больше нуля";
  return { start_time: f.start_time, end_time: f.end_time, location: f.location.trim(), volume: Number(f.volume) };
}

const STEP = 5;

export function ShiftForm({
  form,
  onChange,
  pricing,
  locations,
  points = [],
  load = {},
  ownLocation,
  disabled,
}: {
  form: FormState;
  onChange: (f: FormState) => void;
  pricing: Pricing;
  locations: string[];
  /** admin-defined points; when present the location is picked from them */
  points?: Point[];
  /** people already booked per point on this day */
  load?: Record<string, number>;
  /** my own booking on this day already counts in `load` */
  ownLocation?: string | null;
  disabled?: boolean;
}) {
  const set = (patch: Partial<FormState>) => onChange({ ...form, ...patch });
  const volume = Number(form.volume) || 0;
  const hasVolume = volume > 0;
  const lazy = hasVolume && volume < pricing.lazy_threshold;
  const rate = lazy ? pricing.lazy_rate : pricing.base_rate;
  const badRange = !!form.start_time && !!form.end_time && form.end_time <= form.start_time;
  const field = "w-full rounded-2xl border-0 bg-muted px-4 outline-none transition-shadow focus:ring-2 focus:ring-ring/25";

  const step = (delta: number) => {
    tapHaptic();
    set({ volume: String(Math.max(0, volume + delta) || "") });
  };

  return (
    <fieldset disabled={disabled} className="space-y-5 disabled:opacity-50">
      <div className="grid grid-cols-2 gap-3">
        <label className="space-y-1.5">
          <span className="text-sm text-muted-foreground">Начало</span>
          <input type="time" className={cn(field, "h-14 font-display text-lg font-semibold tabular")} value={form.start_time}
                 onChange={(e) => set({ start_time: e.target.value })} />
        </label>
        <label className="space-y-1.5">
          <span className="text-sm text-muted-foreground">Конец</span>
          <input type="time" className={cn(field, "h-14 font-display text-lg font-semibold tabular", badRange && "ring-2 ring-cancel/50")}
                 value={form.end_time} onChange={(e) => set({ end_time: e.target.value })} />
        </label>
      </div>
      {badRange && <p className="-mt-3 text-sm text-cancel">Конец должен быть позже начала</p>}

      {points.length > 0 ? (
        <div className="space-y-1.5">
          <span className="text-sm text-muted-foreground">Точка</span>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {points.map((p) => {
              const taken = Object.entries(load).reduce(
                (n, [name, count]) => (name.toLocaleLowerCase("ru") === p.name.toLocaleLowerCase("ru") ? n + count : n),
                0,
              ) - (ownLocation && ownLocation.toLocaleLowerCase("ru") === p.name.toLocaleLowerCase("ru") ? 1 : 0);
              const full = !!p.capacity && taken >= p.capacity;
              const picked = form.location.toLocaleLowerCase("ru") === p.name.toLocaleLowerCase("ru");
              return (
                <button
                  key={p.id}
                  type="button"
                  disabled={full && !picked}
                  onClick={() => {
                    tapHaptic();
                    set({ location: p.name });
                  }}
                  className={cn(
                    "press rounded-2xl px-3.5 py-2.5 text-left disabled:opacity-40",
                    picked ? "bg-primary text-primary-foreground" : "bg-muted",
                  )}
                >
                  <span className="block truncate text-[15px] font-semibold">{p.name}</span>
                  <span className={cn("block text-xs", picked ? "opacity-70" : "text-muted-foreground")}>
                    {full ? "мест нет" : p.capacity ? `занято ${taken} из ${p.capacity}` : taken ? `уже ${taken}` : "свободно"}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <>
          <label className="block space-y-1.5">
            <span className="text-sm text-muted-foreground">Точка</span>
            <input className={cn(field, "h-13 text-base")} placeholder="Например, Европа" maxLength={100}
                   value={form.location} onChange={(e) => set({ location: e.target.value })} />
          </label>
          {locations.length > 0 && (
            <div className="-mt-2 flex flex-wrap gap-2">
              {locations.map((l) => (
                <button key={l} type="button" onClick={() => set({ location: l })}
                        className={cn("press rounded-full px-3.5 py-1.5 text-sm font-medium",
                          form.location === l ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:text-foreground")}>
                  {l}
                </button>
              ))}
            </div>
          )}
        </>
      )}

      <div className="space-y-1.5">
        <span className="text-sm text-muted-foreground">Объём, шт.</span>
        <div className="flex items-center gap-2">
          <button type="button" aria-label={`Минус ${STEP}`} onClick={() => step(-STEP)}
                  className="press grid size-14 shrink-0 place-items-center rounded-2xl bg-muted hover:bg-secondary">
            <Minus className="size-5" />
          </button>
          <input inputMode="numeric" pattern="[0-9]*" aria-label="Объём" placeholder="0"
                 className={cn(field, "h-14 text-center font-display text-2xl font-semibold tabular")}
                 value={form.volume} onChange={(e) => set({ volume: e.target.value.replace(/\D/g, "").slice(0, 5) })} />
          <button type="button" aria-label={`Плюс ${STEP}`} onClick={() => step(STEP)}
                  className="press grid size-14 shrink-0 place-items-center rounded-2xl bg-muted hover:bg-secondary">
            <Plus className="size-5" />
          </button>
        </div>
        <p className={cn("pt-1 text-sm", lazy ? "text-wait" : "text-muted-foreground")}>
          {!hasVolume
            ? `${pricing.base_rate} ₽ за штуку. Меньше ${pricing.lazy_threshold} шт. за день — ${pricing.lazy_rate} ₽.`
            : lazy
              ? `Налог на лень: меньше ${pricing.lazy_threshold} шт., ставка ${pricing.lazy_rate} ₽. План ${rub(volume * rate)}.`
              : `${volume} × ${rate} ₽, план ${rub(volume * rate)}`}
        </p>
      </div>
    </fieldset>
  );
}
