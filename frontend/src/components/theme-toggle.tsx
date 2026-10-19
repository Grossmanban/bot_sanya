"use client";

import { Moon, Send, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";
import type { ThemePref } from "@/lib/telegram";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";

const CHOICES: { id: ThemePref; label: string; short: string; icon: typeof Sun }[] = [
  { id: "auto", label: "Как в Telegram", short: "Авто", icon: Send },
  { id: "light", label: "Светлая", short: "Светлая", icon: Sun },
  { id: "dark", label: "Тёмная", short: "Тёмная", icon: Moon },
];

/** One tap: light ⇄ dark. The icon shows what you get. */
export function ThemeButton({ className }: { className?: string }) {
  const { scheme, setPref } = useTheme();
  const next = scheme === "dark" ? "light" : "dark";
  const Icon = next === "dark" ? Moon : Sun;
  return (
    <button
      onClick={() => {
        tapHaptic();
        setPref(next);
      }}
      aria-label={next === "dark" ? "Включить тёмную тему" : "Включить светлую тему"}
      title={next === "dark" ? "Тёмная тема" : "Светлая тема"}
      className={cn("press grid size-9 place-items-center rounded-full bg-muted text-muted-foreground hover:text-foreground", className)}
    >
      <Icon className="size-[18px]" strokeWidth={1.9} />
    </button>
  );
}

/** «Как в Telegram» / «Светлая» / «Тёмная». */
export function ThemeChoice({ compact, className }: { compact?: boolean; className?: string }) {
  const { pref, setPref } = useTheme();
  return (
    <div role="radiogroup" aria-label="Тема оформления" className={cn("flex gap-1 rounded-2xl bg-muted p-1", className)}>
      {CHOICES.map(({ id, label, short, icon: Icon }) => (
        <button
          key={id}
          role="radio"
          aria-checked={pref === id}
          aria-label={label}
          title={label}
          onClick={() => {
            tapHaptic();
            setPref(id);
          }}
          className={cn(
            "press flex h-9 flex-1 items-center justify-center gap-1.5 rounded-xl text-[13px] font-semibold whitespace-nowrap transition-colors",
            pref === id ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon className="size-4 shrink-0" strokeWidth={1.9} />
          {!compact && <span className="truncate">{short === "Авто" ? label : short}</span>}
        </button>
      ))}
    </div>
  );
}
