"use client";

import { useState } from "react";
import { Check, Copy, Share2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Referral } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { haptic, webApp } from "@/lib/telegram";
import { cn } from "@/lib/utils";

const STATUS: Record<Referral["status"], { label: string; tone: string }> = {
  new: { label: "Анкета у кураторов", tone: "bg-muted text-muted-foreground" },
  scheduled: { label: "Назначено обучение", tone: "bg-wait-soft text-wait" },
  internship: { label: "Проходит стажировку", tone: "bg-wait-soft text-wait" },
  done: { label: "Завершил стажировку", tone: "bg-go-soft text-go" },
  rejected: { label: "Отказ", tone: "bg-cancel-soft text-cancel" },
  returned: { label: "Заполняет анкету заново", tone: "bg-muted text-muted-foreground" },
};

/** «Мои рефералы» (the customer's module 3): my link and where everyone I invited is now. */
export function Referrals() {
  const { data } = useLoad(() => api.referrals(), []);
  const [copied, setCopied] = useState(false);
  if (!data) return <Skeleton className="h-48 rounded-3xl" />;
  const link = data.link;

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
            <div className="grid grid-cols-2 gap-2">
              <Button variant="secondary" className="press h-11 rounded-xl" onClick={copy}>
                {copied ? <Check /> : <Copy />} {copied ? "Готово" : "Копировать"}
              </Button>
              <Button className="press h-11 rounded-xl"
                      onClick={() => webApp()?.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent("Присоединяйся к нашей команде!")}`)}>
                <Share2 /> Отправить
              </Button>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Ссылка появится, когда бот будет подключён.</p>
        )}
      </section>

      <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
        <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">Приглашённые <span className="text-muted-foreground">{data.items.length}</span></h2>
        {data.items.length === 0 ? (
          <p className="px-5 pb-5 text-sm text-muted-foreground md:px-6">Пока никого. Отправьте ссылку другу.</p>
        ) : (
          <ul className="divide-y">
            {data.items.map((r) => {
              const p = r.progress;
              const st = p?.stage === 2 ? (p.finished ? { label: "Второй этап засчитан", tone: "bg-go-soft text-go" } : STATUS.done) : STATUS[r.status];
              const pct = p ? Math.min(100, Math.round((p.units / Math.max(p.target, 1)) * 100)) : 0;
              return (
                <li key={r.id} className="space-y-2 px-5 py-3.5 md:px-6">
                  <div className="flex items-center justify-between gap-3">
                    <span className="min-w-0 truncate font-medium">{r.name}</span>
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
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
