"use client";

import { useEffect, useState } from "react";
import { Check, Copy, Link2, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { addToHomeScreen, haptic, homeScreenStatus, type HomeScreenStatus } from "@/lib/telegram";

/** Open the app without the chat: an icon on the phone's home screen and a direct link (t.me/bot?startapp). */
export function QuickAccess({ botUsername }: { botUsername?: string | null }) {
  const [status, setStatus] = useState<HomeScreenStatus>("unsupported");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    void homeScreenStatus().then(setStatus);
  }, []);
  const link = botUsername ? `https://t.me/${botUsername}?startapp` : null;
  if (status === "unsupported" && !link) return null;

  return (
    <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:px-6">
      <div className="flex items-center gap-4">
        <span className="grid size-10 place-items-center rounded-xl bg-muted"><Smartphone className="size-5" strokeWidth={1.8} /></span>
        <span className="min-w-0 flex-1">
          <span className="block font-medium">Быстрый доступ</span>
          <span className="block text-sm text-muted-foreground">Открывайте приложение сразу, не заходя в чат с ботом</span>
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
                    toast.success("Ссылка скопирована — её можно закрепить или отправить коллегам");
                    setTimeout(() => setCopied(false), 1800);
                  }}>
            {copied ? <Check /> : <Copy />} {copied ? "Готово" : "Копировать"}
          </Button>
        </div>
      )}
    </section>
  );
}
