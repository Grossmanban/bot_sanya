"use client";

import { Clock3, LoaderCircle, Lock, Send, TriangleAlert, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { webApp } from "@/lib/telegram";

type Kind = "outside" | "unregistered" | "pending" | "rejected" | "error";

const COPY: Record<Kind, { icon: typeof Clock3; title: string; text: string }> = {
  outside: {
    icon: Send,
    title: "Откройте из Telegram",
    text: "Приложение работает внутри Telegram. Откройте бота и нажмите кнопку «График».",
  },
  unregistered: {
    icon: UserPlus,
    title: "Сначала регистрация",
    text: "Откройте бота, нажмите /start и отправьте заявку. Администратор её рассмотрит.",
  },
  pending: {
    icon: Clock3,
    title: "Заявка на рассмотрении",
    text: "Как только администратор примет решение, бот пришлёт сообщение.",
  },
  rejected: { icon: Lock, title: "Доступ закрыт", text: "Администратор отклонил заявку." },
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
    <div className="pt-safe flex min-h-dvh flex-col items-center justify-center px-8 pb-12 text-center">
      <span className="font-display text-lg font-semibold lowercase">выход</span>
      <div className="mt-10 grid size-16 place-items-center rounded-3xl bg-card shadow-lift">
        <Icon className="size-7" strokeWidth={1.8} />
      </div>
      <h1 className="mt-6 font-display text-2xl font-semibold">{title}</h1>
      <p className="mt-3 max-w-xs text-[15px] leading-relaxed text-muted-foreground">{message ?? text}</p>
      {kind === "unregistered" && botUsername && (
        <Button
          className="mt-8 h-12 w-full max-w-xs rounded-2xl text-base"
          onClick={() => webApp()?.openTelegramLink(`https://t.me/${botUsername}?start=reg`)}
        >
          Открыть бота
        </Button>
      )}
      {kind === "error" && onRetry && (
        <Button className="mt-8 h-12 w-full max-w-xs rounded-2xl text-base" variant="secondary" onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  );
}

export function LoadingScreen() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4">
      <span className="font-display text-2xl font-semibold lowercase">выход</span>
      <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
    </div>
  );
}
