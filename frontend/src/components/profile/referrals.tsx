"use client";

import { useState } from "react";
import { Check, Copy, Eye, EyeOff, LoaderCircle, QrCode, Share2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Referral } from "@/lib/api";
import { useAction, useLoad } from "@/lib/hooks";
import { haptic, openProfile, webApp } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { LinkTimer, QrImage, useCountdown } from "../link-timer";

const STATUS: Record<Referral["status"], { label: string; tone: string }> = {
  new: { label: "Анкета у кураторов", tone: "bg-muted text-muted-foreground" },
  scheduled: { label: "Назначено обучение", tone: "bg-wait-soft text-wait" },
  internship: { label: "Проходит стажировку", tone: "bg-wait-soft text-wait" },
  done: { label: "Завершил стажировку", tone: "bg-go-soft text-go" },
  rejected: { label: "Отказ", tone: "bg-cancel-soft text-cancel" },
  returned: { label: "Заполняет анкету заново", tone: "bg-muted text-muted-foreground" },
};

// 19.11: the pipeline groups — nobody reads one flat list of twenty people with different statuses
const GROUPS: { label: string; statuses: Referral["status"][] }[] = [
  { label: "Анкета у кураторов", statuses: ["new"] },
  { label: "На обучении", statuses: ["scheduled"] },
  { label: "На стажировке", statuses: ["internship"] },
  { label: "В команде", statuses: ["done"] },
  { label: "Отклонённые", statuses: ["rejected", "returned"] },
];

/** One referral card: where the person is now, and the referrer's bonus state — «ждёт выплаты» until the payout
 * actually took the bonus (the money's word, not the stage's). 19.11: the card can be hidden from the list. */
function RefCard({ r, onDone }: { r: Referral; onDone: () => void }) {
  const { busy, run } = useAction();
  const p = r.progress;
  const st = p?.stage === 2 ? (p.finished ? { label: "Второй этап засчитан", tone: "bg-go-soft text-go" } : STATUS.done) : STATUS[r.status];
  const pct = p ? Math.min(100, Math.round((p.units / Math.max(p.target, 1)) * 100)) : 0;
  const hide = async (on: boolean) => {
    try {
      if (await run("hide", (k) => api.hideReferral(r.id, on, k), on ? "Скрыт: карточка внизу списка, в «Скрытых»" : "Возвращён в список")) onDone();
    } catch {
      /* toast shown */
    }
  };
  return (
    <li className="space-y-2 px-5 py-3.5 md:px-6">
      <div className="flex items-center justify-between gap-3">
        <button className="min-w-0 truncate text-left font-medium underline-offset-2 hover:underline"
                onClick={() => openProfile(r.username, r.telegram_id, r.name)}>{r.name}</button>
        <span className={cn("shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold", st.tone)}>
          {p?.review ? "На проверке" : st.label}
        </span>
      </div>
      {p && !p.finished && (
        <>
          <div className="relative h-1.5 overflow-hidden rounded-full bg-muted">
            <span className="absolute inset-y-0 left-0 rounded-full bg-primary" style={{ width: `${pct}%` }} />
          </div>
          <p className="text-xs text-muted-foreground">
            {p.stage === 1 ? "Стажировка" : "Работа после стажировки"}: {p.units} из {p.target}
            {r.stage1_counted != null && p.stage === 2 ? ` · стажировка засчитана: ${r.stage1_counted}` : ""}
          </p>
        </>
      )}
      {r.bonuses?.map((b) => (
        <p key={b.stage} className={cn("text-xs font-semibold", b.paid ? "text-go" : "text-wait")}>
          Бонус {b.amount} ₽ за {b.stage === 1 ? "стажировку" : "второй этап"}: {b.paid ? "выплачен" : "ждёт выплаты"}
        </p>
      ))}
      <div>
        <Button variant="ghost" size="sm" className="h-8 rounded-lg text-muted-foreground" disabled={!!busy}
                onClick={() => hide(!r.hidden)}>
          {busy === "hide" ? <LoaderCircle className="animate-spin" /> : r.hidden ? <Eye /> : <EyeOff />}
          {r.hidden ? "Вернуть в список" : "Скрыть"}
        </Button>
      </div>
    </li>
  );
}

/** «Мои рефералы» (the customer's module 3): my link and where everyone I invited is now — grouped by the
 * pipeline stage, bonuses in plain sight, hidden ones tucked under «Скрытые». */
export function Referrals() {
  const { data, reload } = useLoad(() => api.referrals(), []);
  const [copied, setCopied] = useState(false);
  const [qr, setQr] = useState<string | null>(null);
  const [qrBusy, setQrBusy] = useState(false);
  // the link changes every hour: at zero the new one (and a new QR) is fetched, the old QR is hidden
  const left = useCountdown(data?.seconds_left, () => { setQr(null); void reload(); });
  if (!data) return <Skeleton className="h-48 rounded-3xl" />;
  const link = data.link;
  const items = data.items;
  const visible = items.filter((r) => !r.hidden);
  const hiddenItems = items.filter((r) => r.hidden);

  async function makeQr() {
    setQrBusy(true);
    try {
      const res = await api.referralQr();
      setQr(res.svg);
      haptic("success");
    } catch {
      toast.error("Не удалось сделать QR-код");
    } finally {
      setQrBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      /* clipboard blocked: the link is on screen */
    }
    haptic("success");
    setCopied(true);
    toast.success("Ссылка скопирована");
    setTimeout(() => setCopied(false), 1800);
  }

  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h2 className="font-semibold">Ваша ссылка-приглашение</h2>
        <p className="text-sm text-muted-foreground">
          Ссылка откроет чат с ботом: друг нажмёт «Старт» и заполнит анкету, а бот сможет присылать ему уведомления. Когда он пройдёт стажировку ({data.targets.internship_units} ед.) и потом
          отработает {data.targets.second_stage_units} ед., вам начислят бонусы.
        </p>
        {link ? (
          <>
            <p className="truncate rounded-2xl bg-muted/60 px-4 py-3 text-sm font-medium">{link.replace("https://", "")}</p>
            <LinkTimer left={left} />
            <div className="grid grid-cols-2 gap-2">
              <Button variant="secondary" className="press h-11 rounded-xl" onClick={copy}>
                {copied ? <Check /> : <Copy />} {copied ? "Готово" : "Копировать"}
              </Button>
              <Button className="press h-11 rounded-xl"
                      onClick={() => webApp()?.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent("Присоединяйся к нашей команде!")}`)}>
                <Share2 /> Отправить
              </Button>
            </div>
            {qr ? (
              <div className="space-y-2 pt-1">
                <QrImage svg={qr} />
                <p className="text-center text-xs text-muted-foreground">QR ведёт на эту же ссылку и перестанет работать вместе с ней.</p>
              </div>
            ) : (
              <Button variant="secondary" className="press h-11 w-full rounded-xl" disabled={qrBusy} onClick={makeQr}>
                {qrBusy ? <LoaderCircle className="animate-spin" /> : <QrCode />} Сгенерировать QR-код
              </Button>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Ссылка появится, когда бот будет подключён.</p>
        )}
      </section>

      <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
        <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">Приглашённые <span className="text-muted-foreground">{visible.length}</span></h2>
        {visible.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted-foreground md:px-6">Пока никого. Отправьте ссылку другу.</p>
        ) : (
          GROUPS.map((g) => {
            const rows = visible.filter((r) => g.statuses.includes(r.status));
            if (rows.length === 0) return null;
            return (
              <div key={g.label}>
                <h3 className="flex items-center gap-2 border-t bg-muted/40 px-5 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground md:px-6">
                  {g.label} <span className="rounded-full bg-card px-2 py-0.5 tabular">{rows.length}</span>
                </h3>
                <ul className="divide-y">
                  {rows.map((r) => <RefCard key={r.id} r={r} onDone={reload} />)}
                </ul>
              </div>
            );
          })
        )}
        {hiddenItems.length > 0 && (
          <details className="border-t">
            <summary className="flex cursor-pointer items-center gap-2 px-5 py-3.5 font-semibold text-muted-foreground md:px-6">
              Скрытые <span className="rounded-full bg-muted px-2 text-xs tabular">{hiddenItems.length}</span>
            </summary>
            <ul className="divide-y border-t">
              {hiddenItems.map((r) => <RefCard key={r.id} r={r} onDone={reload} />)}
            </ul>
          </details>
        )}
      </section>
    </div>
  );
}
