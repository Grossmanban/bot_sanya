"use client";

import { useState } from "react";
import { Clock, MapPin, Package } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Pricing, Shift, ShiftPayload } from "@/lib/api";
import { rub } from "@/lib/format";
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
  if (!time.test(f.start_time)) return "Укажите время начала";
  if (!time.test(f.end_time)) return "Укажите время окончания";
  if (f.end_time <= f.start_time) return "Время «До» должно быть позже «С»";
  if (!f.location.trim()) return "Укажите точку";
  if (!/^\d+$/.test(f.volume.trim()) || Number(f.volume) <= 0) return "Объём — целое число больше нуля";
  return { start_time: f.start_time, end_time: f.end_time, location: f.location.trim(), volume: Number(f.volume) };
}

export function ShiftForm({
  form,
  onChange,
  pricing,
  locations,
  disabled,
}: {
  form: FormState;
  onChange: (f: FormState) => void;
  pricing: Pricing;
  locations: string[];
  disabled?: boolean;
}) {
  const [touched, setTouched] = useState(false);
  const set = (patch: Partial<FormState>) => {
    setTouched(true);
    onChange({ ...form, ...patch });
  };
  const volume = Number(form.volume);
  const hasVolume = /^\d+$/.test(form.volume) && volume > 0;
  const lazy = hasVolume && volume < pricing.lazy_threshold;
  const rate = lazy ? pricing.lazy_rate : pricing.base_rate;
  const badRange = touched && form.start_time && form.end_time && form.end_time <= form.start_time;

  return (
    <fieldset disabled={disabled} className="space-y-4 disabled:opacity-60">
      <p className="flex items-center gap-2 text-sm">
        <Clock className="size-4 text-primary" />
        Если выходишь — выбери время, точку и объём
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="start">С</Label>
          <Input id="start" type="time" className="h-12 text-base" value={form.start_time}
                 onChange={(e) => set({ start_time: e.target.value })} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="end">До</Label>
          <Input id="end" type="time" className={cn("h-12 text-base", badRange && "border-destructive")}
                 value={form.end_time} onChange={(e) => set({ end_time: e.target.value })} />
        </div>
      </div>
      {badRange && <p className="-mt-2 text-xs text-destructive">«До» должно быть позже «С»</p>}

      <div className="space-y-1.5">
        <Label htmlFor="location" className="flex items-center gap-1.5">
          <MapPin className="size-3.5" /> Точка
        </Label>
        <Input id="location" className="h-12 text-base" placeholder="Например: Европа, Центр" maxLength={100}
               value={form.location} onChange={(e) => set({ location: e.target.value })} />
        {locations.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {locations.map((l) => (
              <button key={l} type="button" onClick={() => set({ location: l })}
                      className={cn("rounded-full border px-3 py-1 text-xs font-medium",
                        form.location === l ? "border-primary bg-accent text-accent-foreground" : "bg-muted text-muted-foreground")}>
                {l}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="volume" className="flex items-center gap-1.5">
          <Package className="size-3.5" /> Объём, шт.
        </Label>
        <Input id="volume" inputMode="numeric" pattern="[0-9]*" className="h-12 text-base" placeholder="Например: 25"
               value={form.volume} onChange={(e) => set({ volume: e.target.value.replace(/\D/g, "").slice(0, 5) })} />
        {lazy ? (
          <p className="rounded-lg bg-wait/10 px-3 py-2 text-xs text-wait">
            ⚠️ «Налог на лень»: меньше {pricing.lazy_threshold} шт. за день — ставка снижается до {pricing.lazy_rate} ₽/шт.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            Ставка {pricing.base_rate} ₽/шт. При объёме меньше {pricing.lazy_threshold} шт. — {pricing.lazy_rate} ₽/шт.
          </p>
        )}
        {hasVolume && (
          <p className="text-xs text-muted-foreground">
            План: {volume} × {rate} ₽ = <b className="text-foreground">{rub(volume * rate)}</b>
          </p>
        )}
      </div>
    </fieldset>
  );
}
