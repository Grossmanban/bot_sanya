"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ArrowRight, Check, ChevronRight, LoaderCircle, Play, QrCode, RotateCcw, Search, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, PRODUCT_TITLES, type AliceCounts, type AliceStats, type AliceTask, type Product } from "@/lib/api";
import { useAction, useLoad } from "@/lib/hooks";
import { haptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { Camera } from "./camera";

/** ТЗ 27.10 §28: «Работа» — the products this person has access to (the server checks it again everywhere). */
export function WorkScreen({ products }: { products: Product[] }) {
  const [open, setOpen] = useState<Product | null>(products.length === 1 ? products[0] : null);
  if (open === "alice") return <AliceScreen onBack={products.length > 1 ? () => setOpen(null) : undefined} />;
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="font-display text-2xl font-semibold">Работа</h1>
      <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
        <ul className="divide-y">
          {products.map((p) => (
            <li key={p}>
              <button className="press flex w-full items-center gap-3 px-5 py-4 text-left" onClick={() => setOpen(p)}>
                <span className="grid size-10 place-items-center rounded-2xl bg-primary text-primary-foreground"><Search className="size-5" /></span>
                <span className="flex-1 font-semibold">{PRODUCT_TITLES[p]}</span>
                <ChevronRight className="size-5 text-muted-foreground" />
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

const COUNTS: { key: keyof AliceCounts; label: string; tone: string }[] = [
  { key: "completed", label: "Всего завершено", tone: "" },
  { key: "moderation", label: "На модерации", tone: "text-wait" },
  { key: "rejected", label: "Отклонено", tone: "text-cancel" },
  { key: "accepted", label: "Принято, ждёт оплаты", tone: "text-go" },
  { key: "paid", label: "Оплачено", tone: "text-go" },
];

function AliceScreen({ onBack }: { onBack?: () => void }) {
  const { data, setData, reload } = useLoad(() => api.alice(), []);
  const { busy, run } = useAction();
  const [period, setPeriod] = useState<"today" | "week" | "all">("today");
  // «Закрыть»: the execution is folded away, not stopped — everything done so far is kept on the server
  const [folded, setFolded] = useState(false);

  async function act(name: string, fn: (k: string) => Promise<{ task: AliceTask | null; stats?: AliceStats }>, ok?: string) {
    try {
      const res = await run(name, fn, ok);
      if (res && data) setData({ ...data, task: res.task?.status === "in_progress" ? res.task : null, stats: res.stats ?? data.stats });
      if (res?.task && res.task.status !== "in_progress") void reload();
      return res;
    } catch {
      return undefined;
    }
  }

  if (!data) return <Skeleton className="h-64 rounded-3xl" />;
  const s = data.stats[period];
  const task = data.task;
  const failed = data.history.filter((t) => t.status === "send_failed");

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center gap-2">
        {onBack && <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={onBack}>← Назад</Button>}
        <h1 className="font-display text-2xl font-semibold">Яндекс Алиса</h1>
      </div>

      {task && folded ? (
        <section className="flex items-center gap-3 rounded-3xl bg-card p-5 shadow-lift">
          <span className="min-w-0 flex-1">
            <span className="block font-semibold">Выполнение {task.number} не завершено</span>
            <span className="block text-sm text-muted-foreground">
              {task.step === 0 ? "QR-код ещё на экране" : task.step <= 3 ? `Фото ${task.step} из 3` : "Все фото есть — осталось завершить"}
            </span>
          </span>
          <Button className="press h-11 rounded-xl" onClick={() => setFolded(false)}><Play /> Продолжить</Button>
        </section>
      ) : task ? <TaskFlow task={task} busy={busy} act={act} onClose={() => setFolded(true)} /> : (
        <section className="space-y-3 rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
          <p className="text-[15px] text-hero-muted">
            Человек сам решает, пользоваться ли поиском Яндекса: настройка добровольная. Выполнение — QR-код, три фото с экранов
            и поисковый запрос. После завершения фото уходят на модерацию.
          </p>
          <Button className="press h-12 w-full rounded-2xl bg-white text-[15px] font-semibold text-hero hover:bg-white/90"
                  disabled={!!busy} onClick={() => act("start", (k) => api.aliceStart(k))}>
            {busy === "start" ? <LoaderCircle className="animate-spin" /> : <Play />} Начать выполнение
          </Button>
          {!data.has_qr && <p className="text-sm text-hero-muted">На вас не назначен QR-код. Обратитесь к администратору.</p>}
        </section>
      )}

      {failed.map((t) => (
        <section key={t.id} className="space-y-2 rounded-3xl bg-cancel-soft p-5 text-cancel">
          <p className="flex items-center gap-2 font-semibold"><AlertTriangle className="size-4" /> Выполнение {t.number} не отправлено</p>
          <p className="text-sm">{t.send_error ?? "Ошибка отправки"}. Мы повторим отправку автоматически, можно и сейчас.</p>
          <Button size="sm" className="press h-9 rounded-xl" disabled={!!busy}
                  onClick={() => act(`retry-${t.id}`, () => api.aliceRetry(t.id), "Отправлено на модерацию")}>
            <RotateCcw /> Повторить отправку
          </Button>
        </section>
      ))}

      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-semibold">Статистика</h2>
          <div className="flex rounded-xl bg-muted p-1 text-xs font-semibold">
            {(["today", "week", "all"] as const).map((p) => (
              <button key={p} onClick={() => setPeriod(p)} className={cn("press h-8 rounded-lg px-3", period === p ? "bg-card shadow-lift" : "text-muted-foreground")}>
                {p === "today" ? "Сегодня" : p === "week" ? "Неделя" : "Всё время"}
              </button>
            ))}
          </div>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
          {COUNTS.map((c) => (
            <div key={c.key} className="rounded-2xl bg-muted/60 p-3">
              <dt className="text-xs text-muted-foreground">{c.label}</dt>
              <dd className={cn("font-display text-2xl font-semibold tabular", c.tone)}>{s[c.key]}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-muted-foreground">Принятое модератором ещё не оплачено: оплата — после подтверждения Яндекса.</p>
      </section>

      {data.history.length > 0 && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">История</h2>
          <ul className="divide-y">
            {data.history.map((t) => (
              <li key={t.id} className="flex items-center justify-between gap-3 px-5 py-3 md:px-6">
                <span className="min-w-0">
                  <span className="block font-medium">{t.number}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {(t.completed_at ?? t.started_at).slice(8, 10)}.{(t.completed_at ?? t.started_at).slice(5, 7)} · «{t.query}»
                    {t.reject_reason ? ` · ${t.reject_reason}` : ""}
                  </span>
                </span>
                <StatusChip task={t} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function StatusChip({ task }: { task: AliceTask }) {
  const tone = task.status === "rejected" || task.status === "send_failed" ? "bg-cancel-soft text-cancel"
    : task.status === "accepted" || task.status === "paid" ? "bg-go-soft text-go" : "bg-wait-soft text-wait";
  return <span className={cn("shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold", tone)}>{task.status_title}</span>;
}

/** The QR must be read by a phone camera from this screen: a wide white quiet zone (the dark theme around it hides
 * the code's edges), no rounded corners over the finder squares, sharp pixels; a tap — full screen. */
function QrView({ url }: { url: string }) {
  const [full, setFull] = useState(false);
  const img = (big: boolean) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt="QR-код" draggable={false} style={{ imageRendering: "pixelated" }}
         className={cn("block h-auto w-full", big && "max-h-[80dvh] object-contain")} />
  );
  return (
    <>
      <button type="button" aria-label="QR-код на весь экран" onClick={() => setFull(true)}
              className="mx-auto block w-full max-w-sm rounded-2xl bg-white p-[8%]">
        {img(false)}
      </button>
      {full && createPortal(  // over everything, the tab bar too: a parent's transform would trap a fixed layer
        <button type="button" aria-label="Закрыть QR-код" onClick={() => setFull(false)}
                className="fixed inset-0 z-[1000] flex flex-col items-center justify-center gap-4 bg-white p-[10vw]">
          {img(true)}
          <span className="text-sm text-neutral-500">Нажмите, чтобы закрыть</span>
        </button>,
        document.body,
      )}
    </>
  );
}

function TaskFlow({ task, busy, act, onClose }: {
  task: AliceTask; busy: string | null; onClose: () => void;
  act: (name: string, fn: (k: string) => Promise<{ task: AliceTask | null; stats?: AliceStats }>, ok?: string) => Promise<unknown>;
}) {
  const [confirmStop, setConfirmStop] = useState(false);
  const stage = task.stages.find((s) => s.stage === task.step);
  return (
    <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0">
          <h2 className="font-semibold">Выполнение {task.number}</h2>
          <span className="flex items-center gap-1 text-xs text-muted-foreground"><QrCode className="size-3.5" /> {task.qr_number ?? "назначен"}</span>
        </span>
        <Button variant="secondary" size="sm" className="press h-9 shrink-0 rounded-xl" onClick={onClose}>
          <X /> Закрыть
        </Button>
      </div>
      <ol className="flex gap-1.5">
        {[0, 1, 2, 3, 4].map((i) => (
          <li key={i} className={cn("h-1.5 flex-1 rounded-full", i < task.step ? "bg-go" : i === task.step ? "bg-primary" : "bg-muted")} />
        ))}
      </ol>

      {task.step === 0 && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Покажите QR-код человеку — он сканирует его камерой своего телефона. Нажмите на код, чтобы открыть его на весь экран;
            если не считывается — прибавьте яркость экрана.
          </p>
          {task.qr_url && <QrView url={task.qr_url} />}
          {task.qr_number && <p className="text-center text-sm font-semibold">№ {task.qr_number}</p>}
          <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                  onClick={() => act("scanned", () => api.aliceScanned(task.id))}>
            {busy === "scanned" ? <LoaderCircle className="animate-spin" /> : <QrCode />} QR-код отсканирован
          </Button>
        </div>
      )}

      {stage && (
        <div className="space-y-3">
          <div>
            <p className="text-xs text-muted-foreground">Фото {stage.stage} из 3</p>
            <h3 className="font-display text-lg font-semibold">{stage.title}</h3>
            <p className="text-sm text-muted-foreground">{stage.hint}</p>
          </div>
          {stage.stage === 3 && (
            <p className="rounded-2xl bg-muted/60 px-4 py-3 text-sm">
              Запрос — введите точно так: <b className="select-all">{task.query}</b>
            </p>
          )}
          {stage.photo_url ? (
            <div className="space-y-3">
              <div className="relative">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={stage.photo_url} alt={stage.title} className="w-full rounded-2xl bg-muted object-contain" />
                <button aria-label="Удалить фото" disabled={!!busy}
                        onClick={() => act("drop", () => api.aliceDropPhoto(task.id, stage.stage))}
                        className="press absolute top-2 right-2 grid size-9 place-items-center rounded-full bg-black/60 text-white">
                  <X className="size-4" />
                </button>
              </div>
              <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                      onClick={() => act("next", () => api.aliceNext(task.id))}>
                {busy === "next" ? <LoaderCircle className="animate-spin" /> : <ArrowRight />}
                {stage.stage < 3 ? "Далее" : "Зафиксировать последнее фото"}
              </Button>
            </div>
          ) : (
            <Camera key={`${task.id}-${stage.stage}`} busy={busy === "photo"}
                    onAttach={(blob) => { haptic("success"); void act("photo", () => api.alicePhoto(task.id, stage.stage, blob)); }} />
          )}
        </div>
      )}

      {task.step === 4 && (
        <div className="space-y-3">
          <ul className="grid grid-cols-3 gap-2">
            {task.stages.map((s) => (
              <li key={s.stage} className="space-y-1">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                {s.photo_url && <img src={s.photo_url} alt={s.title} className="aspect-[3/4] w-full rounded-xl bg-muted object-cover" />}
                <p className="flex items-center gap-1 text-xs"><Check className="size-3 text-go" /> {s.title}</p>
              </li>
            ))}
          </ul>
          <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                  onClick={() => act("finish", (k) => api.aliceFinish(task.id, k), "Отправлено на модерацию")}>
            {busy === "finish" ? <LoaderCircle className="animate-spin" /> : <Send />} Завершить выполнение
          </Button>
        </div>
      )}

      {confirmStop ? (
        <div className="flex items-center gap-2 rounded-2xl bg-muted/60 p-3 text-sm">
          <span className="flex-1">Прервать выполнение? Фото удалятся, в статистику не попадёт.</span>
          <Button size="sm" variant="ghost" className="h-8 rounded-lg" onClick={() => setConfirmStop(false)}>Нет</Button>
          <Button size="sm" className="h-8 rounded-lg bg-cancel text-white hover:bg-cancel/90" disabled={!!busy}
                  onClick={() => act("abandon", () => api.aliceAbandon(task.id), "Выполнение прервано")}>Прервать</Button>
        </div>
      ) : (
        <button className="text-xs text-muted-foreground underline-offset-2 hover:underline" onClick={() => setConfirmStop(true)}>
          Человек передумал — прервать выполнение
        </button>
      )}
    </section>
  );
}
