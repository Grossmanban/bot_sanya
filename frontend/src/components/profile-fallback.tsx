"use client";

import { useEffect, useState } from "react";
import { Check, Copy, LoaderCircle, Lock, Send } from "lucide-react";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { botUsername, haptic, onProfileFallback, openBotChat, type ProfileTarget } from "@/lib/telegram";

type Result = { sent: boolean; opens: boolean | null; bot: string | null } | { error: string };

/** ТЗ 28.10 §17: a person without a username. Telegram can't open such a profile from the Mini App, so instead of
 * a dead button: the ID to copy and a card sent to the chat with the bot, with an honest answer whether it opens. */
export function ProfileFallback() {
  const [target, setTarget] = useState<ProfileTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => {
    onProfileFallback((p) => {
      setTarget(p);
      setResult(null);
      setCopied(false);
    });
    return () => onProfileFallback(null);
  }, []);

  const copy = async () => {
    if (!target) return;
    try {
      await navigator.clipboard.writeText(String(target.telegramId));
    } catch {
      /* clipboard blocked: the ID is on screen */
    }
    haptic("success");
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const sendCard = async () => {
    if (!target || busy) return;
    setBusy(true);
    try {
      const r = await api.profileLink(target.telegramId);
      setResult({ sent: r.sent, opens: r.opens, bot: r.bot_username ?? botUsername() });
      haptic(r.sent ? "success" : "warning");
    } catch (e) {
      setResult({ error: e instanceof ApiError ? e.message : "Не удалось отправить карточку" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <AlertDialog open={!!target} onOpenChange={(o) => !o && setTarget(null)}>
      <AlertDialogContent className="rounded-3xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="font-display break-words">{target?.name || "Профиль в Telegram"}</AlertDialogTitle>
          <AlertDialogDescription>
            У человека нет username, поэтому Telegram не открывает его профиль прямо из приложения.
            Профиль может быть скрыт его настройками приватности.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="flex items-center gap-2 rounded-2xl bg-muted/60 p-1.5 pl-4">
          <span className="min-w-0 flex-1 truncate text-sm">
            ID <span className="font-mono font-semibold">{target?.telegramId}</span>
          </span>
          <Button variant="secondary" size="sm" className="press h-9 shrink-0 rounded-xl bg-card" onClick={copy}>
            {copied ? <Check /> : <Copy />} {copied ? "Готово" : "Копировать ID"}
          </Button>
        </div>

        {result && "error" in result && <p className="text-sm text-cancel">{result.error}</p>}
        {result && !("error" in result) && (
          result.sent && result.opens ? (
            <div className="space-y-2">
              <p className="text-sm text-go">Карточка в чате с ботом — там кнопка «Открыть профиль».</p>
              {result.bot && (
                <Button className="press h-11 w-full rounded-2xl font-semibold" onClick={() => openBotChat(result.bot!)}>
                  <Send /> Перейти в чат с ботом
                </Button>
              )}
            </div>
          ) : result.sent ? (
            <p className="flex gap-2 text-sm text-muted-foreground">
              <Lock className="mt-0.5 size-4 shrink-0" />
              Telegram не даёт открыть этот профиль: его скрыли настройки приватности или человек ещё не писал боту.
              Связаться можно, если он напишет первым.
            </p>
          ) : (
            <p className="text-sm text-cancel">Бот не смог написать вам: откройте чат с ботом, нажмите «Старт» и попробуйте снова.</p>
          )
        )}

        <AlertDialogFooter>
          {!result && (
            <Button className="press rounded-xl" disabled={busy} onClick={sendCard}>
              {busy ? <LoaderCircle className="animate-spin" /> : <Send />} Прислать карточку в чат с ботом
            </Button>
          )}
          <AlertDialogCancel className="rounded-xl">Закрыть</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
