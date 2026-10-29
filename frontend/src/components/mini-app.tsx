"use client";

import { useCallback, useEffect, useState } from "react";
import { Briefcase, CalendarDays, ExternalLink, GraduationCap, ShieldCheck, Trophy, UserRound } from "lucide-react";
import { Toaster } from "@/components/ui/sonner";
import { api, isStaff, ROLE_TITLES, type Me, type User } from "@/lib/api";
import { getThemePref, initData, openTelegramLinkSafe, setBotUsername, setThemePref, setupWebApp, tapHaptic, type ThemePref } from "@/lib/telegram";
import { ThemeContext } from "@/lib/theme";
import { usePending } from "@/lib/pending";
import { rememberHeader } from "@/lib/header";
import { Wordmark } from "./wordmark";
import { cn } from "@/lib/utils";
import { AccessScreen, LoadingScreen } from "./access-screens";
import { ApplicationStatus, Registration } from "./register/register";
import { readSession, rememberScroll, useScrollMemory, writeSession } from "@/lib/scroll";
import { ScheduleScreen } from "./schedule/schedule-screen";
import { ProfileScreen } from "./profile/profile-screen";
import { AdminScreen } from "./admin/admin-screen";
import { EventsScreen } from "./events/live";
import { WorkScreen } from "./work/work-screen";
import { TrainingScreen } from "./training/training-screen";
import { Avatar } from "./avatar";
import { ThemeButton, ThemeChoice } from "./theme-toggle";
import { ProfileFallback } from "./profile-fallback";

type Tab = "schedule" | "work" | "training" | "events" | "profile" | "admin";

// ТЗ 28.10 §8 (В24): Работа, График, (Обучение), События, Профиль, Админ — unavailable ones are hidden
const TABS: { id: Tab; label: string; icon: typeof CalendarDays }[] = [
  { id: "work", label: "Работа", icon: Briefcase },
  { id: "schedule", label: "График", icon: CalendarDays },
  { id: "training", label: "Обучение", icon: GraduationCap },
  { id: "events", label: "События", icon: Trophy },
  { id: "profile", label: "Профиль", icon: UserRound },
  { id: "admin", label: "Админ", icon: ShieldCheck },
];

// ТЗ 28.10 §8 (В25): the staff tab is named by the role — a trainer is never shown as «Админ»
const STAFF_TAB: Record<User["role"], string> = { owner: "Админ", admin: "Админ", trainer: "Тренер", instructor: "Инструктор",
  employee: "Админ" };

// role colours in the header (ТЗ №1): owner, admin, trainer, employee
const ROLE_DOT: Record<User["role"], string> = { owner: "bg-final", admin: "bg-primary", trainer: "bg-wait",
  instructor: "bg-wait", employee: "bg-go" };

/** ТЗ 28.10 §10: the one button to another bot or Mini App, next to the role; a long title is cut, never wraps. */
function GlobalLink({ link, className }: { link: { title: string; url: string }; className?: string }) {
  return (
    <button type="button" onClick={(e) => { e.stopPropagation(); tapHaptic(); openTelegramLinkSafe(link.url); }}
            className={cn("press inline-flex max-w-[42vw] shrink-0 items-center gap-1 rounded-full bg-primary px-2.5 py-0.5 text-[11px] font-semibold text-primary-foreground", className)}>
      <span className="truncate">{link.title}</span><ExternalLink className="size-3 shrink-0" />
    </button>
  );
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
  // the open tab survives a reload of the app (Telegram «Обновить страницу»)
  const [shown, setTabState] = useState<Tab>(() => (readSession("tab") as Tab | null) ?? "schedule");
  const setTab = useCallback((t: Tab) => {
    rememberScroll();
    setTabState(t);
    writeSession("tab", t);
  }, []);

  const loadMe = useCallback(async () => {
    try {
      const loaded = await api.me();
      setBotUsername(loaded.bot_username);
      rememberHeader(loaded.header);
      setMe(loaded);
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

  // iOS ignores user-scalable=no: block pinch zoom so the app never ends up zoomed in and cut off
  useEffect(() => {
    const stop = (e: Event) => e.preventDefault();
    document.addEventListener("gesturestart", stop);
    return () => document.removeEventListener("gesturestart", stop);
  }, []);

  // each tab keeps its scroll position (the admin tab — per section, in AdminScreen)
  useScrollMemory(`tab:${shown}`, !!me?.user && shown !== "admin");

  const updateUser = useCallback((user: User) => setMe((m) => (m ? { ...m, user } : m)), []);
  // ТЗ 28.10 §14: the sum of open requests on the staff tab (only what this person may decide)
  const pending = usePending(!!me?.user && isStaff(me.user) && me.user.status === "approved");
  const pendingTotal = pending?.total ?? 0;

  const go = (id: Tab) => {
    tapHaptic();
    setTab(id);
  };

  let body: React.ReactNode;
  if (fatal === "outside") {
    body = <AccessScreen kind="outside" />;
  } else if (fatal) {
    body = <AccessScreen kind="error" message={fatal} onRetry={loadMe} />;
  } else if (!me) {
    body = <LoadingScreen />;
  } else if (!me.registered && me.entry !== "ref") {
    // ТЗ 27.10 §5–7: no live invitation — nothing here; an owner's invitation — the request is in the chat
    body = <AccessScreen kind={me.entry === "owner" ? "invited" : me.stale_link ? "stale" : "unregistered"} botUsername={me.bot_username} />;
  } else if (!me.registered || !me.user || (me.user.status === "pending" && !me.application)) {
    // the questionnaire (the customer's module 3): a new person, or one returned to the reserve
    body = <Registration onSent={loadMe} botUsername={me.bot_username} />;
  } else if (me.user.status === "pending" && me.application) {
    body = <ApplicationStatus application={me.application} />;
  } else if (me.user.status !== "approved") {
    body = <AccessScreen kind={me.user.status} botUsername={me.bot_username} />;
  } else if (me.user.account_state === "blocked") {
    body = <AccessScreen kind="blocked" message={me.user.state_reason ? `Причина: ${me.user.state_reason}. Обратитесь к администратору.` : undefined} />;
  } else {
    const user = me.user;
    const staff = isStaff(user);
    // the events tab appears while there is an active (or just finished) event for this person
    // «Работа» appears with access to at least one product (ТЗ 27.10 §28)
    const tabs = TABS.filter((t) => (t.id !== "admin" || staff) && (t.id !== "events" || (me.events ?? 0) > 0)
      && (t.id !== "work" || (me.products?.length ?? 0) > 0) && (t.id !== "training" || !!me.training || (me.manuals ?? 0) > 0))
      .map((t) => (t.id === "admin" ? { ...t, label: STAFF_TAB[user.role] } : t));
    const working = user.work_status === "working";
    const badge = (id: Tab) => (id === "admin" ? pendingTotal : 0);
    // a remembered tab that is gone now (no events any more, no admin rights) falls back to the schedule
    const tab = tabs.some((t) => t.id === shown) ? shown : "schedule";
    const activeIndex = tabs.findIndex((t) => t.id === tab);

    body = (
      <div className="lg:grid lg:min-h-dvh lg:grid-cols-[15.5rem_minmax(0,1fr)]">
        {/* iPad landscape / desktop: side rail */}
        <aside className="sticky top-0 hidden h-dvh flex-col border-r bg-card/50 px-5 pt-8 pb-6 lg:flex">
          <Wordmark className="block px-3 text-2xl" text={me.header} />
          {me.global_link && <GlobalLink link={me.global_link} className="mx-3 mt-3 max-w-full self-start py-1 text-xs" />}
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
                {badge(id) > 0 && (
                  <span className={cn("ml-auto inline-grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] leading-5 tabular",
                    tab === id ? "bg-white/20" : "bg-cancel text-white")}>{badge(id)}</span>
                )}
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
              <Wordmark className="block text-xl leading-tight" text={me.header} />
              <span className="flex min-w-0 items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
                <span className={cn("size-1.5 shrink-0 rounded-full", ROLE_DOT[user.role])} />
                <span className="truncate">{ROLE_TITLES[user.role]}{me.network ? ` · ${me.network}` : ""}</span>
                {me.global_link && <GlobalLink link={me.global_link} />}
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
              <ScheduleScreen user={user} pricing={me.pricing!} onOpenProfile={() => go("profile")} onUserChange={updateUser}
                              internship={me.internship} />
            )}
            {tab === "events" && <EventsScreen />}
            {tab === "training" && <TrainingScreen trainer={!!me.training} manuals={me.manuals ?? 0} />}
            {tab === "work" && <WorkScreen products={me.products ?? []} working={working} onOpenProfile={() => go("profile")} />}
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
                aria-label={label}
                className={cn(
                  "relative z-10 flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[11px] font-semibold transition-colors duration-300",
                  tab === id ? "text-primary-foreground" : "text-muted-foreground",
                )}
              >
                <span className="relative">
                  <Icon className="size-[22px]" strokeWidth={tab === id ? 2.1 : 1.8} />
                  {badge(id) > 0 && (
                    <span className="absolute -top-1.5 left-3.5 inline-grid h-4 min-w-4 place-items-center rounded-full bg-cancel px-1 text-[10px] leading-4 font-bold text-white tabular">
                      {badge(id) > 99 ? "99+" : badge(id)}
                    </span>
                  )}
                </span>
                {/* §27: narrower than 360 px (iPhone SE with 6 tabs) — icons only, the label stays on the active tab */}
                <span className={cn("max-w-full truncate px-0.5", tab !== id && "max-[359.98px]:sr-only")}>{label}</span>
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
        <ProfileFallback />
        {/* phones (≤600px) use mobileOffset, not offset: both must clear the notch and Telegram's buttons */}
        <Toaster position="top-center" theme={scheme} offset={{ top: "calc(var(--safe-top) + 8px)" }}
                 mobileOffset={{ top: "calc(var(--safe-top) + 8px)", left: "16px", right: "16px" }} />
      </div>
    </ThemeContext.Provider>
  );
}
