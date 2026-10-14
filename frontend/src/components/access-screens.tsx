"use client";

import { Clock, LoaderCircle, Lock, Send, TriangleAlert, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { webApp } from "@/lib/telegram";

type Kind = "outside" | "unregistered" | "pending" | "rejected" | "error";

const COPY: Record<Kind, { icon: typeof Clock; title: string; text: string }> = {
  outside: {
    icon: Send,
    title: "Откройте из Telegram",
    text: "Приложение работает только внутри Telegram — откройте его кнопкой «График» в боте.",
  },
  unregistered: {
    icon: UserPlus,
    title: "Обратитесь к администратору",
    text: "Вы ещё не зарегистрированы. Откройте бота, нажмите /start и отправьте заявку.",
  },
  pending: {
    icon: Clock,
    title: "Заявка на рассмотрении",
    text: "Администратор ещё не принял решение. Бот пришлёт сообщение, как только заявку рассмотрят.",
  },
  rejected: { icon: Lock, title: "Доступ отклонён", text: "Администратор отклонил заявку на доступ." },
  error: { icon: TriangleAlert, title: "Не удалось загрузить", text: "" },
};

export function AccessScreen({
  kind,
  message,
  botUsername,
  onRetry,
}: {
  kind: Kind;
  message?: string;
  botUsername?: string | null;
  onRetry?: () => void;
}) {
  const { icon: Icon, title, text } = COPY[kind];
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div className="w-full max-w-sm rounded-2xl border bg-card p-6 text-center shadow-sm">
        <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-2xl bg-accent text-accent-foreground">
          <Icon className="size-7" />
        </div>
        <h1 className="text-lg font-bold">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{message ?? text}</p>
        {kind === "unregistered" && botUsername && (
          <Button className="mt-5 w-full" onClick={() => webApp()?.openTelegramLink(`https://t.me/${botUsername}?start=reg`)}>
            Открыть бота
          </Button>
        )}
        {kind === "error" && onRetry && (
          <Button className="mt-5 w-full" variant="outline" onClick={onRetry}>
            Попробовать снова
          </Button>
        )}
      </div>
    </div>
  );
}

export function LoadingScreen() {
  return (
    <div className="flex flex-1 items-center justify-center">
      <LoaderCircle className="size-7 animate-spin text-primary" />
    </div>
  );
}
