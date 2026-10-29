"use client";

import { useRef, useState } from "react";
import { AlertTriangle, Archive, Check, ExternalLink, History, ImageUp, LoaderCircle, RotateCcw, Save, Trash2, UserPlus, X } from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, PRODUCT_TITLES, type AliceCounts, type AliceQr, type AliceTask, type Product } from "@/lib/api";
import { compressImage } from "@/lib/image";
import { addDays } from "@/lib/format";
import { useCan } from "@/lib/caps";
import { moscowToday, useAction, useLoad } from "@/lib/hooks";
import { openProfile, webApp } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { ReasonChips } from "../reason-chips";
import { StatusChip } from "../work/work-screen";

type Tab = "queue" | "qr" | "queries" | "stats";
const field = "h-10 max-w-full min-w-0 rounded-xl border-0 bg-muted px-3 text-sm outline-none focus:ring-2 focus:ring-ring/25";
const STATUSES: [string, string][] = [
  ["moderation", "На модерации"], ["accepted", "Принято, ожидает оплаты"], ["rejected", "Отклонено"], ["paid", "Оплачено"],
  ["send_failed", "Не отправлено"], ["yandex_rejected", "Отклонено Яндексом"], ["deleted", "Удалённые"], ["all", "Все"],
];

/** ТЗ 27.10 §32–40: moderation, QR codes, search queries, statistics of «Яндекс Алиса»; ТЗ «Яндекс Плюс»: the same
 * section for «Яндекс Плюс» — its own QR codes, queue, statistics and group, one photo per task, no search queries. */
export function AliceAdmin({ product = "alice" }: { product?: Product }) {
  const info = useLoad(() => api.admin.alice(product), [product]);
  const [tab, setTab] = useState<Tab>("queue");
  const command = info.data?.command ?? (product === "alice" ? "/alice_here" : "/plus_here");
  const tabs: [Tab, string][] = [["queue", `Модерация${info.data?.counts.moderation ? ` · ${info.data.counts.moderation}` : ""}`],
    ["qr", "QR-коды"], ...(product === "alice" ? [["queries", "Запросы"] as [Tab, string]] : []), ["stats", "Статистика"]];
  return (
    <div className="space-y-4">
      <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
        <h2 className="font-display text-xl font-semibold">{PRODUCT_TITLES[product]}</h2>
        <p className="mt-1.5 text-[15px] text-hero-muted">
          {info.data?.forum
            ? `Фотоотчёты уходят в группу «${info.data.forum.title ?? "форум"}», у каждого сотрудника своя тема.`
            : `Группа для фотоотчётов не подключена: создайте отдельную группу с темами, сделайте бота администратором (право «Управление темами») и отправьте там ${command} от владельца.`}
          {" "}Доступ к продукту выдаётся в карточке сотрудника («Команда»).
        </p>
      </section>
      {info.data && <RateCard key={`${product}-${info.data.rate}`} product={product} rate={info.data.rate} onSaved={info.reload} />}
      <div className={cn("grid rounded-2xl bg-muted p-1", tabs.length === 4 ? "grid-cols-4" : "grid-cols-3")}>
        {tabs.map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
                  className={cn("press h-10 rounded-xl text-xs font-semibold sm:text-sm", tab === t ? "bg-card shadow-lift" : "text-muted-foreground")}>
            {label}
          </button>
        ))}
      </div>
      {tab === "queue" && <Queue key={product} product={product} people={info.data?.people ?? []} onChanged={info.reload} />}
      {tab === "qr" && <Qrs key={product} product={product} people={info.data?.people ?? []} />}
      {tab === "queries" && product === "alice" && <Queries />}
      {tab === "stats" && <Stats key={product} product={product} />}
    </div>
  );
}

/** The product's own rate per paid task, right in its section (owners only; the same setting as «Настройки → Ставки»):
 * «Яндекс Алиса» — alice_rate, «Яндекс Плюс» — plus_rate. Payouts already made keep the rate they used. */
function RateCard({ product, rate, onSaved }: { product: Product; rate: number; onSaved: () => void }) {
  const canMoney = useCan("money");
  const [value, setValue] = useState(String(rate));
  const { busy, run } = useAction();
  if (!canMoney) return null;
  const n = Number(value);
  const save = async () => {
    try {
      await run("rate", () => api.admin.saveSettings(product === "plus" ? { plus_rate: n } : { alice_rate: n }),
        `Ставка «${PRODUCT_TITLES[product]}»: ${n} ₽ за выполнение`);
      onSaved();
    } catch { /* toast shown */ }
  };
  return (
    <section className="flex flex-wrap items-center gap-3 rounded-3xl bg-card p-4 shadow-lift md:px-5">
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">Ставка «{PRODUCT_TITLES[product]}»</span>
        <span className="block text-xs text-muted-foreground">₽ за оплаченное выполнение · своя для каждого продукта · проведённые выплаты не меняются</span>
      </span>
      <input inputMode="numeric" aria-label="Ставка, ₽" className={cn(field, "w-24 tabular")} value={value}
             onChange={(e) => setValue(e.target.value.replace(/\D/g, "").slice(0, 6))} />
      <span className="text-sm text-muted-foreground">₽</span>
      <Button size="sm" className="press h-10 rounded-xl" disabled={!!busy || value === "" || n === rate || n > 100000} onClick={() => save()}>
        {busy === "rate" ? <LoaderCircle className="animate-spin" /> : <Save />} Сохранить
      </Button>
    </section>
  );
}

function Queue({ product, people, onChanged }: { product: Product; people: { id: number; name: string }[]; onChanged: () => void }) {
  const [status, setStatus] = useState("moderation");
  const [userId, setUserId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const { data, setData } = useLoad(() => api.admin.aliceQueue({ status, user_id: userId ? Number(userId) : undefined,
    from: from || undefined, to: to || undefined, product }), [status, userId, from, to, product]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <select aria-label="Статус" className={field} value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select aria-label="Сотрудник" className={field} value={userId} onChange={(e) => setUserId(e.target.value)}>
          <option value="">Все сотрудники</option>
          {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <input type="date" aria-label="С" className={field} value={from} onChange={(e) => setFrom(e.target.value)} />
        <input type="date" aria-label="По" className={field} value={to} onChange={(e) => setTo(e.target.value)} />
      </div>
      {!data ? <Skeleton className="h-40 rounded-3xl" /> : data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift">Здесь пусто.</p>
      ) : data.items.map((t) => (
        <TaskCard key={t.id} task={t} onDone={(nt) => {
          setData({ items: data.items.map((x) => (x.id === nt.id ? nt : x))
            .filter((x) => (status === "all" ? x.status !== "deleted" : x.status === status)) });
          onChanged();
        }} />
      ))}
    </div>
  );
}

function TaskCard({ task, onDone }: { task: AliceTask; onDone: (t: AliceTask) => void }) {
  const { busy, run } = useAction();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [big, setBig] = useState<string | null>(null);
  async function decide(approve: boolean) {
    try {
      const res = await run(approve ? "ok" : "no", (k) => api.admin.aliceDecide(task.id, approve, approve ? null : reason.trim() || null, k),
        approve ? "Принято" : "Отклонено");
      if (res) onDone(res.task);
    } catch {
      /* toast shown */
    }
  }
  const when = task.completed_at ?? task.started_at;
  return (
    <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{task.title ?? task.number}
            <button className="ml-2 font-normal underline-offset-2 hover:underline" onClick={() => openProfile(task.username, task.telegram_id, task.name)}>
              {task.name}
            </button>
          </p>
          <p className="text-xs text-muted-foreground">
            {when.slice(8, 10)}.{when.slice(5, 7)} {when.slice(11, 16)} · {task.qr_label ?? `QR ${task.qr_number ?? task.qr_id}`}
            {task.query ? ` · запрос «${task.query}»` : ""}
          </p>
        </div>
        <StatusChip task={task} />
      </div>
      <div className={cn("grid gap-2", task.stages.length > 1 ? "grid-cols-3" : "max-w-[60%] grid-cols-1")}>
        {task.stages.map((s) => (
          <figure key={s.stage} className="space-y-1">
            {s.photo_url ? (
              <button className="block w-full" onClick={() => setBig(big === s.photo_url ? null : s.photo_url)}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={s.photo_url} alt={s.title} className="aspect-[3/4] w-full rounded-xl bg-muted object-cover" />
              </button>
            ) : <div className="aspect-[3/4] rounded-xl bg-muted" />}
            <figcaption className="text-[11px] leading-tight text-muted-foreground">{task.stages.length > 1 ? `${s.stage}. ` : ""}{s.title}</figcaption>
          </figure>
        ))}
      </div>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {big && <img src={big} alt="Фото крупно" className="w-full rounded-2xl bg-muted object-contain" onClick={() => setBig(null)} />}
      {!!task.duplicates?.length && (
        <p className="flex items-center gap-1.5 text-sm text-wait">
          <AlertTriangle className="size-4 shrink-0" /> Такое же фото уже было в {task.duplicates.map((d) => `#${d}`).join(", ")}
        </p>
      )}
      {task.reject_reason && <p className="text-sm text-cancel">Причина отказа: {task.reject_reason}</p>}
      {task.send_error && <p className="text-sm text-cancel">Не отправлено в группу: {task.send_error}</p>}
      {task.moderated_by && <p className="text-xs text-muted-foreground">Решил: {task.moderated_by}</p>}
      <DeleteTask task={task} onDone={onDone} />
      {task.message_link && (
        <button className="flex items-center gap-1 text-xs font-semibold text-primary" onClick={() => webApp()?.openTelegramLink(task.message_link!)}>
          <ExternalLink className="size-3.5" /> Открыть в группе
        </button>
      )}
      {task.status === "send_failed" && (
        <Button variant="secondary" className="press h-10 w-full rounded-xl" disabled={!!busy}
                onClick={async () => { try { const r = await run("retry", (k) => api.admin.aliceRetry(task.id, k), "Отправка повторена"); if (r) onDone(r.task); } catch { /* */ } }}>
          <RotateCcw /> Отправить в группу ещё раз{task.send_error ? ` (${task.send_error})` : ""}
        </Button>
      )}
      {task.status === "moderation" && (rejecting ? (
        <div className="space-y-2">
          <ReasonChips kind={task.product === "plus" ? "plus_reject" : "alice_reject"} value={reason} onPick={setReason} />
          <textarea rows={2} autoFocus value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)}
                    placeholder={task.product === "plus" ? "Причина (по желанию): например, не видно подписки" : "Причина (по желанию): например, не видно запроса"}
                    className="w-full resize-none rounded-xl border-0 bg-muted px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-ring/25" />
          <div className="grid grid-cols-2 gap-2">
            <Button variant="secondary" className="press h-10 rounded-xl" onClick={() => setRejecting(false)}>Отмена</Button>
            <Button className="press h-10 rounded-xl bg-cancel text-white hover:bg-cancel/90" disabled={!!busy} onClick={() => decide(false)}>
              {busy === "no" ? <LoaderCircle className="animate-spin" /> : <X />} Отклонить
            </Button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" className="press h-11 rounded-xl text-cancel" disabled={!!busy} onClick={() => setRejecting(true)}>
            <X /> Отклонить
          </Button>
          <Button className="press h-11 rounded-xl bg-go text-white hover:bg-go/90" disabled={!!busy} onClick={() => decide(true)}>
            {busy === "ok" ? <LoaderCircle className="animate-spin" /> : <Check />} Подтвердить
          </Button>
        </div>
      ))}
    </section>
  );
}

function Qrs({ product, people }: { product: Product; people: { id: number; name: string }[] }) {
  const [archived, setArchived] = useState(false);
  const { data, setData } = useLoad(() => api.admin.aliceQrs(archived, product), [archived, product]);
  const { busy, run } = useAction();
  const [number, setNumber] = useState("");
  const file = useRef<HTMLInputElement>(null);

  async function create(f: File) {
    try {
      const blob = await compressImage(f, 1200, 0.92);
      const res = await run("create", (k) => api.admin.aliceQrCreate(blob, number.trim(), k, product), "QR-код добавлен");
      if (res) {
        setData(res);
        setNumber("");
      }
    } catch (e) {
      if (e instanceof Error && !("status" in e)) toast.error(e.message);
    }
  }

  return (
    <div className="space-y-3">
      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift">
        <h3 className="font-semibold">Новый QR-код «{PRODUCT_TITLES[product]}»</h3>
        <p className="text-sm text-muted-foreground">
          Один QR можно назначить нескольким сотрудникам; у сотрудника — один QR. В каждом выполнении сохраняется QR, с которым
          его начали, — смена назначения историю не переписывает.
        </p>
        <div className="flex flex-wrap gap-2">
          <input className={cn(field, "min-w-0 flex-1 basis-40")} placeholder="Название или номер (по желанию)" value={number} maxLength={60}
                 onChange={(e) => setNumber(e.target.value)} />
          <Button className="press h-10 rounded-xl" disabled={!!busy} onClick={() => file.current?.click()}>
            {busy === "create" ? <LoaderCircle className="animate-spin" /> : <ImageUp />} Загрузить картинку
          </Button>
          <input ref={file} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void create(f); }} />
        </div>
      </section>
      <div className="flex items-center justify-between px-1">
        <h3 className="font-semibold">{archived ? "Архив QR-кодов" : "Действующие QR-коды"}</h3>
        <button className="text-sm font-semibold text-primary" onClick={() => setArchived((v) => !v)}>
          {archived ? "← Действующие" : "Показать архив →"}
        </button>
      </div>
      {!data ? <Skeleton className="h-40 rounded-3xl" /> : data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift">QR-кодов нет.</p>
      ) : data.items.map((q) => <QrCard key={q.id} qr={q} people={people} onChanged={setData} />)}
    </div>
  );
}

function QrCard({ qr, people, onChanged }: { qr: AliceQr; people: { id: number; name: string }[]; onChanged: (d: { items: AliceQr[] }) => void }) {
  const { busy, run } = useAction();
  const [adding, setAdding] = useState("");
  const [history, setHistory] = useState<{ at: string; what: string; by: string | null }[] | null>(null);
  const [number, setNumber] = useState(qr.number ?? "");
  const [lost, setLost] = useState(false);  // the picture file is gone: ask for it again
  const image = useRef<HTMLInputElement>(null);
  const act = async (name: string, fn: (k: string) => Promise<{ items: AliceQr[] }>, ok: string) => {
    try {
      const res = await run(name, fn, ok);
      if (res) onChanged(res);
    } catch {
      /* toast shown */
    }
  };
  const free = people.filter((p) => !qr.people.some((x) => x.user_id === p.id));
  return (
    <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift">
      <div className="flex gap-4">
        {lost ? (
          <button className="grid size-24 shrink-0 place-items-center rounded-xl bg-cancel-soft p-2 text-center text-[11px] font-semibold leading-tight text-cancel"
                  onClick={() => image.current?.click()}>
            Картинка потерялась — загрузите заново
          </button>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={qr.image_url} alt="QR" onError={() => setLost(true)} className="size-24 shrink-0 rounded-xl bg-white object-contain p-1" />
        )}
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex gap-2">
            <input className={cn(field, "w-full")} aria-label="Номер QR" placeholder="Без номера" value={number}
                   disabled={!!qr.archived_at} onChange={(e) => setNumber(e.target.value)} />
            {number !== (qr.number ?? "") && (
              <Button size="sm" className="press h-10 rounded-xl" disabled={!!busy}
                      onClick={() => act("num", (k) => api.admin.aliceQrPatch(qr.id, { number }, k), "Номер сохранён")}><Save /></Button>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Отправлено {qr.used.submitted} · принято {qr.used.accepted} · оплачено {qr.used.paid} · отклонено {qr.used.rejected}
          </p>
        </div>
      </div>
      <ul className="flex flex-wrap gap-2">
        {qr.people.map((p) => (
          <li key={p.user_id} className="flex items-center gap-1 rounded-full bg-muted py-1 pr-1 pl-3 text-sm">
            {p.name}
            {!qr.archived_at && (
              <button aria-label="Снять назначение" disabled={!!busy} className="grid size-6 place-items-center rounded-full hover:bg-card"
                      onClick={() => act(`off-${p.user_id}`, (k) => api.admin.aliceAssign(qr.id, p.user_id, false, k), "Назначение снято")}>
                <X className="size-3.5" />
              </button>
            )}
          </li>
        ))}
        {qr.people.length === 0 && <li className="text-sm text-muted-foreground">Никому не назначен</li>}
      </ul>
      {!qr.archived_at && (
        <div className="flex flex-wrap gap-2">
          <select aria-label="Назначить сотруднику" className={cn(field, "w-full min-w-0 flex-1 basis-48")} value={adding} onChange={(e) => setAdding(e.target.value)}>
            <option value="">Назначить сотруднику…</option>
            {free.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <Button size="sm" className="press h-10 rounded-xl" disabled={!adding || !!busy}
                  onClick={() => act("on", (k) => api.admin.aliceAssign(qr.id, Number(adding), true, k), "QR-код назначен").then(() => setAdding(""))}>
            <UserPlus /> Назначить
          </Button>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="ghost" className="h-8 rounded-lg" onClick={async () => setHistory(history ? null : (await api.admin.aliceQrHistory(qr.id)).items)}>
          <History /> История
        </Button>
        {!qr.archived_at && (
          <>
            <Button size="sm" variant="ghost" className="h-8 rounded-lg" onClick={() => image.current?.click()}><ImageUp /> Заменить картинку</Button>
            <input ref={image} type="file" accept="image/*" hidden onChange={async (e) => {
              const f = e.target.files?.[0]; e.target.value = "";
              if (f) { const blob = await compressImage(f, 1200, 0.92); void act("img", (k) => api.admin.aliceQrImage(qr.id, blob, k), "Картинка заменена"); }
            }} />
            <Button size="sm" variant="ghost" className="h-8 rounded-lg text-muted-foreground" disabled={!!busy}
                    onClick={() => act("arch", (k) => api.admin.aliceQrPatch(qr.id, { archive: true }, k), "QR в архиве, назначения сняты")}>
              <Archive /> В архив
            </Button>
          </>
        )}
      </div>
      {history && (
        <ul className="max-h-60 space-y-1 overflow-y-auto rounded-xl bg-muted/50 p-3 text-xs">
          {history.length === 0 && <li className="text-muted-foreground">Пусто</li>}
          {history.map((h, i) => (
            <li key={i}>{h.at.slice(8, 10)}.{h.at.slice(5, 7)} {h.at.slice(11, 16)} — {h.what}{h.by ? ` (${h.by})` : ""}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Queries() {
  const { data, setData } = useLoad(() => api.admin.aliceQueries(), []);
  const [text, setText] = useState<string | null>(null);
  const { busy, run } = useAction();
  if (!data) return <Skeleton className="h-40 rounded-3xl" />;
  const value = text ?? data.items.join("\n");
  return (
    <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift">
      <h3 className="font-semibold">Поисковые запросы <span className="text-muted-foreground">{data.items.length}</span></h3>
      <p className="text-sm text-muted-foreground">
        Один запрос в строке — ровно в том виде, как его нужно ввести. Сотруднику достаётся случайный, без повторов из его
        последних выполнений. Удалённые из списка запросы остаются в уже сделанных выполнениях.
      </p>
      <textarea rows={14} value={value} onChange={(e) => setText(e.target.value)}
                className="w-full rounded-xl border-0 bg-muted px-3 py-2.5 font-mono text-sm outline-none focus:ring-2 focus:ring-ring/25" />
      <Button className="press h-11 w-full rounded-xl font-semibold" disabled={text === null || !!busy}
              onClick={async () => {
                try {
                  const res = await run("save", (k) => api.admin.aliceSetQueries(value, k), "Список сохранён");
                  if (res) { setData(res); setText(null); }
                } catch { /* toast shown */ }
              }}>
        {busy ? <LoaderCircle className="animate-spin" /> : <Save />} Сохранить список
      </Button>
    </section>
  );
}

const LABELS: [keyof AliceCounts, string][] = [["completed", "Завершено"], ["moderation", "Модерация"], ["rejected", "Отклонено"],
  ["accepted", "Ждёт оплаты"], ["paid", "Оплачено"]];

function Stats({ product }: { product: Product }) {
  const today = moscowToday();
  const [from, setFrom] = useState(addDays(today, -6));
  const [to, setTo] = useState(today);
  const { data } = useLoad(() => api.admin.aliceStats(from, to, product), [from, to, product]);
  const table = (rows: ({ key: string; name: string } & AliceCounts)[]) => (
    <div className="overflow-x-auto">
      <table className="w-full text-sm tabular">
        <thead className="text-xs text-muted-foreground">
          <tr><th className="py-1 text-left font-normal" />{LABELS.map(([, l]) => <th key={l} className="px-1 font-normal">{l}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-border/60">
              <td className="max-w-40 truncate py-1.5 pr-2">{r.name}</td>
              {LABELS.map(([k]) => <td key={k} className="text-center">{r[k]}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <input type="date" aria-label="С" className={field} value={from} onChange={(e) => setFrom(e.target.value)} />
        <input type="date" aria-label="По" className={field} value={to} onChange={(e) => setTo(e.target.value)} />
      </div>
      {!data ? <Skeleton className="h-40 rounded-3xl" /> : (
        <>
          <section className="grid grid-cols-5 gap-2 rounded-3xl bg-card p-4 shadow-lift">
            {LABELS.map(([k, l]) => (
              <div key={k} className="text-center">
                <p className="font-display text-xl font-semibold tabular">{data.total[k]}</p>
                <p className="text-[11px] text-muted-foreground">{l}</p>
              </div>
            ))}
          </section>
          <section className="space-y-2 rounded-3xl bg-card p-5 shadow-lift">
            <h3 className="font-semibold">По сотрудникам</h3>
            {table(data.people.map((p) => ({ ...p, key: String(p.user_id) })))}
          </section>
          <section className="space-y-2 rounded-3xl bg-card p-5 shadow-lift">
            <h3 className="font-semibold">По QR-кодам</h3>
            {table(data.qrs.map((q) => ({ ...q, key: String(q.qr_id), name: q.number ? `QR ${q.number}` : `QR ${q.qr_id}` })))}
          </section>
          <section className="space-y-2 rounded-3xl bg-card p-5 shadow-lift">
            <h3 className="font-semibold">По дням</h3>
            {table(data.days.map((d) => ({ ...d, key: d.date, name: `${d.date.slice(8, 10)}.${d.date.slice(5, 7)}` })))}
          </section>
        </>
      )}
    </div>
  );
}


/** ТЗ 28.10 §23: an admin removes any task — confirmed, the reason optional; the photos go to «Архив», the publication
 * leaves the topic. A failed part is repeated by the same button without a second archive copy. */
function DeleteTask({ task, onDone }: { task: AliceTask; onDone: (t: AliceTask) => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const { busy, run } = useAction();
  const t = task as AliceTask & { archive_state?: string | null; unpublish_state?: string | null };
  const partial = task.status === "deleted" && (t.archive_state !== "done" || t.unpublish_state === "failed");
  if (task.status === "deleted" && !partial) return <p className="text-xs text-muted-foreground">Удалено, фото в «Архиве».</p>;
  const go = async () => {
    try {
      const res = await run("delete", (k) => api.admin.aliceDelete(task.id, reason.trim() || null, k),
        partial ? "Повторено" : "Задание удалено");
      if (res) { setOpen(false); onDone(res.task); }
    } catch { /* toast shown */ }
  };
  return (
    <>
      <button className="flex items-center gap-1 text-xs font-semibold text-cancel" disabled={!!busy}
              onClick={() => (partial ? go() : setOpen(true))}>
        <Trash2 className="size-3.5" /> {partial ? "Удалено не полностью — повторить" : "Удалить задание"}
      </button>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Удалить {task.title ?? task.number}?</AlertDialogTitle>
            <AlertDialogDescription>
              Фото с подписью уйдут в чат «Архив», публикация исчезнет из темы сотрудника, задание пропадёт из списков и статистики.
              Запись и связи с выплатами сохранятся. Исполнитель получит сообщение.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <input className={field + " w-full"} placeholder="Причина (по желанию)" value={reason} onChange={(e) => setReason(e.target.value.slice(0, 300))} />
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
            <AlertDialogAction variant="destructive" className="rounded-xl" disabled={!!busy} onClick={(e) => { e.preventDefault(); void go(); }}>
              {busy === "delete" && <LoaderCircle className="animate-spin" />} Удалить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
