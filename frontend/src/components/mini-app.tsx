"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarDays, ShieldCheck, Trophy, UserRound } from "lucide-react";
import { Toaster } from "@/components/ui/sonner";
import { api, isStaff, ROLE_TITLES, type Me, type User } from "@/lib/api";
import { getThemePref, initData, setThemePref, setupWebApp, tapHaptic, type ThemePref } from "@/lib/telegram";
import { ThemeContext } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { AccessScreen, LoadingScreen } from "./access-screens";
import { ScheduleScreen } from "./schedule/schedule-screen";
import { ProfileScreen } from "./profile/profile-screen";
import { AdminScreen } from "./admin/admin-screen";
import { EventsScreen } from "./events/live";
import { Avatar } from "./avatar";
import { ThemeButton, ThemeChoice } from "./theme-toggle";

type Tab = "schedule" | "events" | "profile" | "admin";

const TABS: { id: Tab; label: string; icon: typeof CalendarDays }[] = [
  { id: "schedule", label: "График", icon: CalendarDays },
  { id: "events", label: "События", icon: Trophy },
  { id: "profile", label: "Профиль", icon: UserRound },
  { id: "admin", label: "Админ", icon: ShieldCheck },
];

// role colours in the header (ТЗ №1): owner, admin, trainer, employee
const ROLE_DOT: Record<User["role"], string> = { owner: "bg-final", admin: "bg-primary", trainer: "bg-wait", employee: "bg-go" };

export function Wordmark({ className }: { className?: string }) {
  return <span className={cn("font-display font-semibold tracking-tight lowercase", className)}>выход</span>;
}

export function MiniApp() {
  const [scheme, setScheme] = useState<"light" | "dark">("light");
  const [pref, setPrefState] = useState<ThemePref>("auto");
  const setPref = useCallback((p: ThemePref) => {
    setThemePref(p);
    setPrefState(p);
  }, []);
  const [me, setMe] = useState<Me | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("schedule");
  // пасхалка: при каждом открытии фото на 0,2 секунды поверх экрана загрузки
  const [egg, setEgg] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setEgg(false), 200);
    return () => clearTimeout(t);
  }, []);

  const loadMe = useCallback(async () => {
    try {
      setMe(await api.me());
      setFatal(null);
    } catch (e) {
      setFatal(e instanceof Error ? e.message : "Не удалось загрузить данные");
    }
  }, []);

  useEffect(() => {
    // the SDK script is loaded synchronously in <head>, so WebApp is ready here
    setPrefState(getThemePref());
    const cleanup = setupWebApp(setScheme);
    if (!initData()) {
      setFatal("outside");
      return cleanup;
    }
    void loadMe();
    return cleanup;
  }, [loadMe]);

  const updateUser = useCallback((user: User) => setMe((m) => (m ? { ...m, user } : m)), []);

  const go = (id: Tab) => {
    tapHaptic();
    setTab(id);
    window.scrollTo({ top: 0 });
  };

  let body: React.ReactNode;
  if (fatal === "outside") {
    body = <AccessScreen kind="outside" />;
  } else if (fatal) {
    body = <AccessScreen kind="error" message={fatal} onRetry={loadMe} />;
  } else if (!me) {
    body = <LoadingScreen />;
  } else if (!me.registered || !me.user) {
    body = <AccessScreen kind="unregistered" botUsername={me.bot_username} />;
  } else if (me.user.status !== "approved") {
    body = <AccessScreen kind={me.user.status} botUsername={me.bot_username} />;
  } else if (me.user.account_state === "blocked") {
    body = <AccessScreen kind="blocked" message={me.user.state_reason ? `Причина: ${me.user.state_reason}. Если это ошибка — свяжитесь с руководителем.` : undefined} />;
  } else {
    const user = me.user;
    const staff = isStaff(user);
    // the events tab appears while there is an active (or just finished) event for this person
    const tabs = TABS.filter((t) => (t.id !== "admin" || staff) && (t.id !== "events" || (me.events ?? 0) > 0));
    const working = user.work_status === "working";
    const activeIndex = tabs.findIndex((t) => t.id === tab);

    body = (
      <div className="lg:grid lg:min-h-dvh lg:grid-cols-[15.5rem_minmax(0,1fr)]">
        {/* iPad landscape / desktop: side rail */}
        <aside className="sticky top-0 hidden h-dvh flex-col border-r bg-card/50 px-5 pt-8 pb-6 lg:flex">
          <Wordmark className="px-3 text-2xl" />
          <nav className="mt-10 space-y-1">
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => go(id)}
                className={cn(
                  "press flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-[15px] font-medium",
                  tab === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="size-5" strokeWidth={1.9} />
                {label}
              </button>
            ))}
          </nav>
          <p className="mt-auto mb-1.5 px-3 text-xs font-medium text-muted-foreground">Тема: как в Telegram, светлая, тёмная</p>
          <ThemeChoice compact className="mb-3" />
          <button
            onClick={() => go("profile")}
            className="press flex items-center gap-3 rounded-2xl p-3 text-left hover:bg-muted"
          >
            <Avatar user={user} className="size-10 rounded-xl text-sm" />
            <span className="min-w-0">
              <span className="block truncate font-semibold">{user.first_name} {user.last_name}</span>
              <span className={cn("flex items-center gap-1.5 text-xs", working ? "text-go" : "text-muted-foreground")}>
                <span className={cn("size-1.5 rounded-full", working ? "bg-go" : "bg-idle")} />
                {user.account_state === "paused" ? "На паузе" : working ? "Работаю" : "Не работаю"}
              </span>
              <span className="block text-xs text-muted-foreground">
                {ROLE_TITLES[user.role]}{me.network ? ` · ${me.network}` : ""}
              </span>
            </span>
          </button>
        </aside>

        <div className="flex min-h-dvh min-w-0 flex-col">
          {/* phone / iPad portrait: compact top bar under Telegram's controls */}
          <header className="pt-safe flex items-center justify-between gap-3 px-5 pb-3 lg:hidden">
            <div className="min-w-0">
              <Wordmark className="block text-xl leading-tight" />
              <span className="flex items-center gap-1.5 truncate text-[11px] font-semibold text-muted-foreground">
                <span className={cn("size-1.5 shrink-0 rounded-full", ROLE_DOT[user.role])} />
                {ROLE_TITLES[user.role]}{me.network ? ` · ${me.network}` : ""}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <ThemeButton />
              <button
                onClick={() => go("profile")}
                className={cn(
                  "press flex items-center gap-2 rounded-full py-1 pr-1 pl-3 text-xs font-semibold",
                  working ? "bg-go-soft text-go" : "bg-muted text-muted-foreground",
                )}
              >
                {user.account_state === "paused" ? "На паузе" : working ? "Работаю" : "Не работаю"}
                <Avatar user={user} className="size-7 rounded-full text-[11px]" />
              </button>
            </div>
          </header>

          <main key={tab} className="screen-in mx-auto w-full max-w-2xl flex-1 px-4 pb-36 md:px-6 lg:max-w-6xl lg:px-10 lg:pt-10 lg:pb-12">
            {tab === "schedule" && (
              <ScheduleScreen user={user} pricing={me.pricing!} onOpenProfile={() => go("profile")} onUserChange={updateUser} />
            )}
            {tab === "events" && <EventsScreen />}
            {tab === "profile" && <ProfileScreen user={user} pricing={me.pricing!} onUserChange={updateUser} botUsername={me.bot_username} />}
            {tab === "admin" && staff && <AdminScreen me={user} caps={me.capabilities ?? []} network={me.network} />}
          </main>
        </div>

        {/* bottom bar with a sliding selection */}
        <nav className="pb-safe fixed inset-x-0 bottom-0 z-40 px-4 lg:hidden">
          <div className="relative mx-auto flex max-w-md rounded-[1.6rem] border bg-card/80 p-1.5 shadow-lift-strong backdrop-blur-xl backdrop-saturate-150">
            <span
              aria-hidden
              className="absolute inset-y-1.5 left-1.5 rounded-[1.2rem] bg-primary transition-transform duration-300 ease-[cubic-bezier(.2,.8,.2,1)]"
              style={{ width: `calc((100% - 0.75rem) / ${tabs.length})`, transform: `translateX(${Math.max(activeIndex, 0) * 100}%)` }}
            />
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => go(id)}
                className={cn(
                  "relative z-10 flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] font-semibold transition-colors duration-300",
                  tab === id ? "text-primary-foreground" : "text-muted-foreground",
                )}
              >
                <Icon className="size-[22px]" strokeWidth={tab === id ? 2.1 : 1.8} />
                {label}
              </button>
            ))}
          </div>
        </nav>
      </div>
    );
  }

  return (
    <ThemeContext.Provider value={{ pref, scheme, setPref }}>
      <div className="min-h-dvh">
        {body}
        {egg && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src="/hello.webp" alt="" aria-hidden className="fixed inset-0 z-[100] size-full bg-background object-cover" />
        )}
        <Toaster position="top-center" theme={scheme} offset={{ top: "calc(var(--safe-top) + 8px)" }} />
      </div>
    </ThemeContext.Provider>
  );
}
