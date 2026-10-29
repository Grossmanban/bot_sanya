"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ArrowRight, Check, ChevronLeft, ChevronRight, LoaderCircle, Play, QrCode, RotateCcw, Search, Send, Sparkles, X } from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, PRODUCT_SHORT, PRODUCT_TITLES, type AliceCounts, type AliceStats, type AliceTask, type Product } from "@/lib/api";
import { useAction, useLoad } from "@/lib/hooks";
import { haptic } from "@/lib/telegram";
import { TONE_CHIP, type Tone } from "@/lib/tone";
import { cn } from "@/lib/utils";
import { Camera } from "./camera";

const ICON: Record<Product, typeof Search> = { alice: Search, plus: Sparkles };

/** ТЗ 27.10 §28: «Работа» — the products this person has access to (the server checks it again everywhere).
 * «Яндекс Алиса» and «Яндекс Плюс» share one screen: the same steps, buttons and history, each with its own data. */
export function WorkScreen({ products, working = true, onOpenProfile }: { products: Product[]; working?: boolean; onOpenProfile?: () => void }) {
  const [open, setOpen] = useState<Product | null>(products.length === 1 ? products[0] : null);
  if (open && products.includes(open)) {
    return <AliceScreen key={open} product={open} onBack={products.length > 1 ? () => setOpen(null) : undefined} working={working}
                        onOpenProfile={onOpenProfile} />;
  }
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="font-display text-2xl font-semibold">Работа</h1>
      <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
        <ul className="divide-y">
          {products.map((p) => {
            const Icon = ICON[p] ?? Search;
            return (
            <li key={p}>
              <button className="press flex w-full items-center gap-3 px-5 py-4 text-left" onClick={() => setOpen(p)}>
                <span className="grid size-10 place-items-center rounded-2xl bg-primary text-primary-foreground"><Icon className="size-5" /></span>
                <span className="flex-1 font-semibold">{PRODUCT_TITLES[p]}</span>
                <ChevronRight className="size-5 text-muted-foreground" />
              </button>
            </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

const COUNTS: { key: keyof AliceCounts; label: string; tone: string }[] = [
  { key: "completed", label: "Всего завершено", tone: "" },
  { key: "moderation", label: "На модерации", tone: "text-wait" },
  { key: "rejected", label: "Отклонено", tone: "text-cancel" },
  { key: "accepted", label: "Принято, ожидает оплаты", tone: "text-go" },
  { key: "paid", label: "Оплачено", tone: "text-go" },
  { key: "yandex_rejected", label: "Отклонено Яндексом", tone: "text-cancel" },
];

const INTRO: Record<Product, string> = {
  alice: "Человек сам решает, пользоваться ли поиском Яндекса: настройка добровольная. Выполнение — QR-код, три фото с экранов "
    + "и поисковый запрос. После завершения фото уходят на модерацию.",
  plus: "Клиент сам решает, подключать ли Яндекс Плюс. Выполнение — QR-код и одно фото личного кабинета Яндекс Плюса. "
    + "После завершения фото уходит на модерацию.",
};

function AliceScreen({ product, onBack, working, onOpenProfile }: { product: Product; onBack?: () => void; working: boolean; onOpenProfile?: () => void }) {
  const { data, setData, reload } = useLoad(() => api.alice(product), [product]);
  const name = PRODUCT_SHORT[product];
  const { busy, run } = useAction();
  const [period, setPeriod] = useState<"today" | "week" | "all">("today");
  // «Закрыть»: the execution is folded away, not stopped — everything done so far is kept on the server
  const [folded, setFolded] = useState(false);

  async function act(name: string, fn: (k: string) => Promise<{ task: AliceTask | null; stats?: AliceStats }>, ok?: string,
                     raise = false) {
    try {
      const res = await run(name, fn, ok);
      if (res && data) setData({ ...data, task: res.task?.status === "in_progress" ? res.task : null, stats: res.stats ?? data.stats });
      if (res?.task && res.task.status !== "in_progress") void reload();
      return res;
    } catch (e) {
      if (raise) throw e;
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
        <h1 className="font-display text-2xl font-semibold">{name}</h1>
      </div>

      {task && folded ? (
        <section className="flex items-center gap-3 rounded-3xl bg-card p-5 shadow-lift">
          <span className="min-w-0 flex-1">
            <span className="block font-semibold">{task.title ?? `${name} ${task.number}`}{product === "alice" ? " не завершена" : " — не завершено"}</span>
            <span className="block text-sm text-muted-foreground">
              {task.step === 0 ? "QR-код ещё на экране" : task.step < (task.last_step ?? 4)
                ? (task.stages.length > 1 ? `Фото ${task.step} из ${task.stages.length}` : "Осталось сделать фото")
                : "Фото есть — осталось завершить"}
            </span>
          </span>
          <Button className="press h-11 rounded-xl" onClick={() => setFolded(false)}><Play /> Продолжить</Button>
        </section>
      ) : task ? <TaskFlow task={task} product={product} busy={busy} act={act} onClose={() => setFolded(true)} /> : (
        <section className="space-y-3 rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
          <p className="text-[15px] text-hero-muted">{INTRO[product]}</p>
          <Button className="press h-12 w-full rounded-2xl bg-white text-[15px] font-semibold text-hero hover:bg-white/90"
                  disabled={!!busy || !working} onClick={() => act("start", (k) => api.aliceStart(k, product))}>
            {busy === "start" ? <LoaderCircle className="animate-spin" /> : <Play />} Начать выполнение
          </Button>
          {!working && (
            <p className="text-sm text-hero-muted">
              Новые выполнения — только со статусом «Работаю».{" "}
              {onOpenProfile && <button className="font-semibold underline underline-offset-2" onClick={onOpenProfile}>Открыть профиль</button>}
            </p>
          )}
          {!data.has_qr && <p className="text-sm text-hero-muted">На вас не назначен QR-код. Обратитесь к администратору.</p>}
        </section>
      )}

      {failed.map((t) => (
        <section key={t.id} className="space-y-2 rounded-3xl bg-cancel-soft p-5 text-cancel">
          <p className="flex items-center gap-2 font-semibold"><AlertTriangle className="size-4" /> {t.title ?? `${name} ${t.number}`} — не отправлено</p>
          <p className="text-sm">{t.send_error ?? "Ошибка отправки"}. Мы повторим отправку автоматически, можно и сейчас.</p>
          <Button size="sm" className="press h-9 rounded-xl" disabled={!!busy}
                  onClick={() => act(`retry-${t.id}`, () => api.aliceRetry(t.id, product), "Отправлено на модерацию")}>
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
              <dd className={cn("font-display text-2xl font-semibold tabular", c.tone)}>{s[c.key] ?? 0}</dd>
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
                  <span className="block font-medium">{t.title ?? `${name} ${t.number}`}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {(t.completed_at ?? t.started_at).slice(8, 10)}.{(t.completed_at ?? t.started_at).slice(5, 7)}
                    {" "}{(t.completed_at ?? t.started_at).slice(11, 16)} · {t.query ? `«${t.query}»` : (t.qr_label ?? "")}
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

// §27: the shared colour scale; an abandoned or deleted task is archive (grey), not «waiting»
const ALICE_TONE: Record<AliceTask["status"], Tone> = {
  in_progress: "wait", sending: "wait", moderation: "wait",
  accepted: "ok", paid: "ok",
  rejected: "bad", send_failed: "bad", yandex_rejected: "bad",
  abandoned: "archive", deleted: "archive",
};

export function StatusChip({ task }: { task: AliceTask }) {
  return <span className={cn("shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold", TONE_CHIP[ALICE_TONE[task.status] ?? "wait"])}>{task.status_title}</span>;
}

/** The QR must be read by a phone camera from this screen: a wide white quiet zone (the dark theme around it hides
 * the code's edges), no rounded corners over the finder squares, sharp pixels; a tap — full screen. */
function QrView({ url, product }: { url: string; product: Product }) {
  const [full, setFull] = useState(false);
  const [lost, setLost] = useState(false);
  if (lost) {
    return (
      <p className="rounded-2xl bg-cancel-soft p-4 text-sm text-cancel">
        Картинка QR-кода недоступна. Обратитесь к администратору — он загрузит её заново в «Админ → {PRODUCT_SHORT[product]} → QR-коды».
      </p>
    );
  }
  const img = (big: boolean) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt="QR-код" draggable={false} onError={() => setLost(true)} style={{ imageRendering: "pixelated" }}
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

function TaskFlow({ task, product, busy, act, onClose }: {
  task: AliceTask; product: Product; busy: string | null; onClose: () => void;
  act: (name: string, fn: (k: string) => Promise<{ task: AliceTask | null; stats?: AliceStats }>, ok?: string, raise?: boolean) => Promise<unknown>;
}) {
  const [confirmStop, setConfirmStop] = useState(false);
  const [broken, setBroken] = useState<Record<number, boolean>>({});
  const stage = task.stages.find((s) => s.stage === task.step);
  const total = task.stages.length;           // «Алиса» — 3 photos, «Яндекс Плюс» — 1
  const last = task.last_step ?? total + 1;  // all photos fixed: the check and «Завершить выполнение»
  // §19: back to an earlier stage (the QR included) — the photos stay; forward again with «Далее»
  const goto = (n: number) => act(`goto-${n}`, () => api.aliceGoto(task.id, n, product));
  // «Далее» / «Завершить» name the stage whose photo is gone: take the person straight there
  const forward = async (name: string, fn: (k: string) => Promise<{ task: AliceTask | null; stats?: AliceStats }>, ok?: string) => {
    try {
      return await act(name, fn, ok, true);
    } catch (e) {
      const m = e instanceof ApiError ? /^photo_missing_(\d)$/.exec(e.code) : null;
      if (m && Number(m[1]) < task.step) await goto(Number(m[1]));
      return undefined;
    }
  };
  return (
    <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0">
          <h2 className="font-semibold">{task.title ?? `${PRODUCT_SHORT[product]} ${task.number}`}</h2>
          <span className="flex items-center gap-1 text-xs text-muted-foreground"><QrCode className="size-3.5" /> {task.qr_label ?? task.qr_number ?? "назначен"}</span>
        </span>
        <Button variant="secondary" size="sm" className="press h-9 shrink-0 rounded-xl" onClick={onClose}>
          <X /> Закрыть
        </Button>
      </div>
      <ol className="flex gap-1.5" aria-label="Этапы">
        {Array.from({ length: last + 1 }, (_, i) => i).map((i) => (
          <li key={i} className="flex-1">
            <button type="button" disabled={i >= task.step || !!busy} onClick={() => goto(i)}
                    aria-label={i === 0 ? "К QR-коду" : i === last ? "Проверка" : `К фото ${i}`}
                    className={cn("block h-1.5 w-full rounded-full", i < task.step ? "bg-go" : i === task.step ? "bg-primary" : "bg-muted")} />
          </li>
        ))}
      </ol>
      {task.step > 0 && (
        <button type="button" disabled={!!busy} onClick={() => goto(task.step - 1)}
                className="press inline-flex items-center gap-1 text-sm font-semibold text-muted-foreground">
          <ChevronLeft className="size-4" /> {task.step === 1 ? "Назад к QR-коду" : "Назад"}
        </button>
      )}

      {task.step === 0 && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Покажите QR-код человеку — он сканирует его камерой своего телефона. Нажмите на код, чтобы открыть его на весь экран;
            если не считывается — прибавьте яркость экрана.
          </p>
          {task.qr_url && <QrView url={task.qr_url} product={product} />}
          {task.qr_number && <p className="text-center text-sm font-semibold">№ {task.qr_number}</p>}
          <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                  onClick={() => act("scanned", () => api.aliceScanned(task.id, product))}>
            {busy === "scanned" ? <LoaderCircle className="animate-spin" /> : <QrCode />} {task.scanned_at ? "Дальше — к фото" : "QR-код отсканирован"}
          </Button>
        </div>
      )}

      {stage && (
        <div className="space-y-3">
          <div>
            <p className="text-xs text-muted-foreground">{total > 1 ? `Фото ${stage.stage} из ${total}` : "Фото"}</p>
            <h3 className="font-display text-lg font-semibold">{stage.title}</h3>
            <p className="text-sm text-muted-foreground">{stage.hint}</p>
          </div>
          {stage.stage === 3 && task.query && (
            <p className="rounded-2xl bg-muted/60 px-4 py-3 text-sm">
              Запрос — введите точно так: <b className="select-all">{task.query}</b>
            </p>
          )}
          {stage.photo_url && !broken[stage.stage] ? (
            <div className="space-y-3">
              <div className="relative">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={stage.photo_url} alt={stage.title} onError={() => setBroken({ ...broken, [stage.stage]: true })}
                     className="w-full rounded-2xl bg-muted object-contain" />
                <button aria-label="Удалить фото" disabled={!!busy}
                        onClick={() => act("drop", () => api.aliceDropPhoto(task.id, stage.stage, product))}
                        className="press absolute top-2 right-2 grid size-9 place-items-center rounded-full bg-black/60 text-white">
                  <X className="size-4" />
                </button>
              </div>
              <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                      onClick={() => forward("next", () => api.aliceNext(task.id, product))}>
                {busy === "next" ? <LoaderCircle className="animate-spin" /> : <ArrowRight />}
                {stage.stage < total ? "Далее" : total > 1 ? "Зафиксировать последнее фото" : "Далее"}
              </Button>
            </div>
          ) : (
            <>
              {stage.photo_url && (
                <p className="rounded-2xl bg-cancel-soft px-4 py-3 text-sm text-cancel">
                  Фото этого этапа недоступно — сделайте его заново.
                </p>
              )}
              <Camera key={`${task.id}-${stage.stage}`} busy={busy === "photo"}
                      onAttach={(blob) => { haptic("success"); setBroken({ ...broken, [stage.stage]: false }); void act("photo", () => api.alicePhoto(task.id, stage.stage, blob, product)); }} />
            </>
          )}
        </div>
      )}

      {task.step === last && (
        <div className="space-y-3">
          <ul className={cn("grid gap-2", total > 1 ? "grid-cols-3" : "mx-auto max-w-[60%] grid-cols-1")}>
            {task.stages.map((s) => (
              <li key={s.stage} className="space-y-1">
                <button type="button" onClick={() => goto(s.stage)} disabled={!!busy} className="block w-full">
                  {s.photo_url && !broken[s.stage] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={s.photo_url} alt={s.title} onError={() => setBroken({ ...broken, [s.stage]: true })}
                         className="aspect-[3/4] w-full rounded-xl bg-muted object-cover" />
                  ) : (
                    <span className="grid aspect-[3/4] w-full place-items-center rounded-xl bg-cancel-soft p-2 text-center text-xs text-cancel">
                      Фото недоступно — нажмите, чтобы переснять
                    </span>
                  )}
                </button>
                <p className="flex items-center gap-1 text-xs"><Check className="size-3 text-go" /> {s.title}</p>
              </li>
            ))}
          </ul>
          <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                  onClick={() => forward("finish", (k) => api.aliceFinish(task.id, k, product), "Отправлено на модерацию")}>
            {busy === "finish" ? <LoaderCircle className="animate-spin" /> : <Send />} Завершить выполнение
          </Button>
        </div>
      )}

      <Button variant="ghost" className="press h-11 w-full rounded-2xl font-semibold text-cancel hover:bg-cancel-soft hover:text-cancel"
              disabled={!!busy} onClick={() => setConfirmStop(true)}>
        ⛔ Прервать выполнение
      </Button>
      <AlertDialog open={confirmStop} onOpenChange={setConfirmStop}>
        <AlertDialogContent className="max-h-[90dvh] overflow-y-auto rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">⛔ Прервать выполнение задания?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-left text-sm">
                <p>При прерывании все прикреплённые фотографии будут удалены, а задание не попадёт в статистику выполненных заданий.</p>
                {total > 1 ? (
                  <p>
                    Используйте прерывание только тогда, когда задание не удалось завершить до конца: человек передумал; задание уже было выполнено;
                    не удалось собрать полный фотоотчёт; из трёх необходимых фотографий удалось сделать только одну или две; либо задание невозможно
                    корректно завершить по другой причине.
                  </p>
                ) : (
                  <p>
                    Используйте прерывание только тогда, когда задание не удалось завершить до конца: клиент передумал; подписка уже была
                    подключена раньше; не удалось сделать фото личного кабинета; либо задание невозможно корректно завершить по другой причине.
                  </p>
                )}
                <p>Если полный фотоотчёт собран и задание выполнено, не прерывайте его без необходимости.</p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="gap-2">
            <AlertDialogCancel className="h-11 rounded-xl">↩️ Продолжить выполнение</AlertDialogCancel>
            <AlertDialogAction variant="destructive" className="h-11 rounded-xl" disabled={!!busy}
                               onClick={() => act("abandon", () => api.aliceAbandon(task.id, product), "Выполнение прервано")}>
              ⛔ Да, прервать
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
