"use client";

import { useEffect, useState } from "react";
import { api, type ReportCounts } from "./api";

/** ТЗ 28.10 §14: the open-request counts, shared by the bottom tab and the admin sections. Fetched on start, every
 * 30 s, when the app comes back to the screen and right after any action — viewing never changes them. */
let counts: ReportCounts | null = null;
let active = 0;
let inflight: Promise<void> | null = null;
const listeners = new Set<(c: ReportCounts | null) => void>();

export function refreshPending(): Promise<void> {
  if (active === 0) return Promise.resolve();
  inflight ??= api.admin.reportCounts()
    .then((c) => {
      counts = c;
      listeners.forEach((fn) => fn(c));
    })
    .catch(() => undefined)
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

export function usePending(enabled: boolean): ReportCounts | null {
  const [value, setValue] = useState<ReportCounts | null>(counts);
  useEffect(() => {
    if (!enabled) return;
    active += 1;
    listeners.add(setValue);
    void refreshPending();
    const timer = setInterval(() => void refreshPending(), 30_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshPending();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active -= 1;
      listeners.delete(setValue);
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [enabled]);
  return enabled ? value : null;
}

/** The section badges: which counts belong to which admin section (one request — one section). */
export const SECTION_COUNTS: Record<string, (keyof ReportCounts)[]> = {
  reports: ["review", "no_show"],
  discipline: ["violations"],
  actual: ["actual_missing", "actual_overdue"],
  penalties: ["fines_pending", "fines_draft"],
  onboarding: ["onboarding_new", "internship_review", "training_requests"],
  team: ["registrations", "profile_changes", "payment_changes"],
  day: ["urgent", "unmarked"],
  events: ["events_pending"],
  alice: ["alice_moderation", "alice_failed"],
};

export function sectionCount(c: ReportCounts | null, section: string): number {
  return (SECTION_COUNTS[section] ?? []).reduce((n, k) => n + (Number(c?.[k]) || 0), 0);
}

/** §27 «Решения»: the same words as the bot's /pending (backend pending.LABELS), in the same order. */
export const PENDING_LABELS: [keyof ReportCounts, string][] = [
  ["review", "📝 Отчёты на проверке"], ["no_show", "🚫 Неявки без решения"], ["actual_missing", "📊 Дни без общего факта"],
  ["actual_overdue", "📊 Просрочено без общего факта"], ["fines_pending", "🧾 Штрафы тренеров на согласовании"],
  ["fines_draft", "💸 Штрафы без суммы"], ["violations", "⚖️ Дисциплина"], ["registrations", "👤 Заявки на доступ"],
  ["onboarding_new", "🧑‍🎓 Новые анкеты"], ["internship_review", "🎯 Стажировки на проверку"],
  ["training_requests", "⏳ Обучение: ожидает согласования"], ["profile_changes", "🪪 Изменения профилей"],
  ["payment_changes", "💳 Изменения реквизитов"], ["urgent", "⚡ Срочные заявки на сегодня"],
  ["unmarked", "🗓 Не отметили завтрашний день"], ["events_pending", "🏁 Заявки в событиях"],
  ["alice_moderation", "📸 «Алиса»: на модерации"], ["alice_failed", "📸 «Алиса»: не отправлено"],
];

/** Which admin section decides a given count. */
export function sectionOf(key: keyof ReportCounts): string | null {
  return Object.entries(SECTION_COUNTS).find(([, keys]) => keys.includes(key))?.[0] ?? null;
}
