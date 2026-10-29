"use client";

import { useEffect, useState } from "react";
import { Check, ChevronRight, Copy, Gift, Link2, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { addToHomeScreen, haptic, homeScreenStatus, type HomeScreenStatus } from "@/lib/telegram";

/** ТЗ 28.10 §11: the personal way into the app (for this person only) — apart from the invitation link
 * that brings a new person and a bonus (it lives in «Мои рефералы»). */
export function QuickAccess({ botUsername, onInvite }: { botUsername?: string | null; onInvite?: () => void }) {
  const [status, setStatus] = useState<HomeScreenStatus>("unsupported");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    void homeScreenStatus().then(setStatus);
  }, []);
  const link = botUsername ? `https://t.me/${botUsername}?startapp` : null;

  return (
    <>
      {(status !== "unsupported" || link) && (
        <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:px-6">
          <div className="flex items-center gap-4">
            <span className="grid size-10 place-items-center rounded-xl bg-muted"><Smartphone className="size-5" strokeWidth={1.8} /></span>
            <span className="min-w-0 flex-1">
              <span className="block font-medium">Быстрый доступ</span>
              <span className="block text-sm text-muted-foreground">Ваша личная ссылка — только для вас: открывает приложение сразу, без чата с ботом</span>
            </span>
          </div>
          {status === "added" ? (
            <p className="flex items-center gap-2 rounded-2xl bg-go-soft px-4 py-3 text-sm font-semibold text-go">
              <Check className="size-4" /> Иконка уже на главном экране
            </p>
          ) : status !== "unsupported" && (
            <Button className="press h-auto min-h-12 w-full rounded-2xl py-2 text-[15px] font-semibold whitespace-normal"
                    onClick={() => addToHomeScreen(() => { setStatus("added"); haptic("success"); toast.success("Иконка добавлена на главный экран"); })}>
              <Smartphone /> Добавить на главный экран
            </Button>
          )}
          {link && (
            <div className="flex items-center gap-2 rounded-2xl bg-muted/60 p-1.5 pl-4">
              <Link2 className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{link.replace("https://", "")}</span>
              <Button variant="secondary" size="sm" className="press h-9 shrink-0 rounded-xl bg-card"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(link);
                        } catch {
                          /* clipboard blocked: the link is on screen */
                        }
                        haptic("success");
                        setCopied(true);
                        toast.success("Ссылка скопирована — она для вас. Чтобы позвать человека на работу, используйте ссылку-приглашение");
                        setTimeout(() => setCopied(false), 1800);
                      }}>
                {copied ? <Check /> : <Copy />} {copied ? "Готово" : "Копировать"}
              </Button>
            </div>
          )}
          <details className="rounded-2xl bg-muted/40 px-4 py-3 text-sm">
            <summary className="cursor-pointer font-medium">Как добавить на экран «Домой» на iPhone</summary>
            <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-muted-foreground">
              <li>Нажмите «Добавить на главный экран» выше и подтвердите «Добавить» — иконка появится рядом с другими приложениями.</li>
              <li>Нет такой кнопки — обновите Telegram в App Store (нужна версия 8.0 или новее) и откройте приложение снова.</li>
              <li>Если обновить нельзя: в списке чатов смахните чат с ботом вправо и нажмите «Закрепить» — он всегда будет сверху, приложение открывается кнопкой «Приложение» слева от поля ввода.</li>
            </ol>
          </details>
        </section>
      )}
      {onInvite && (
        <button onClick={onInvite} className="press flex w-full items-center gap-4 rounded-3xl bg-card p-5 text-left shadow-lift md:px-6">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-go-soft text-go"><Gift className="size-5" /></span>
          <span className="min-w-0 flex-1">
            <span className="block font-medium">Пригласить человека на работу и получить денежный бонус</span>
            <span className="block text-sm text-muted-foreground">Ссылка-приглашение и QR-код — для кандидатов, не для входа самому</span>
          </span>
          <ChevronRight className="size-5 shrink-0 text-muted-foreground" />
        </button>
      )}
    </>
  );
}
