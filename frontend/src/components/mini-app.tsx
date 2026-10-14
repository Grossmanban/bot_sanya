"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarClock, CircleUser, ShieldCheck } from "lucide-react";
import { Toaster } from "@/components/ui/sonner";
import { api, type Me, type User } from "@/lib/api";
import { initData, setupWebApp, tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { AccessScreen, LoadingScreen } from "./access-screens";
import { ScheduleScreen } from "./schedule/schedule-screen";
import { ProfileScreen } from "./profile/profile-screen";
import { AdminScreen } from "./admin/admin-screen";

type Tab = "schedule" | "profile" | "admin";

export function MiniApp() {
  const [scheme, setScheme] = useState<"light" | "dark">("light");
  const [me, setMe] = useState<Me | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("schedule");

  useEffect(() => {
    document.documentElement.classList.toggle("dark", scheme === "dark");
  }, [scheme]);

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
    const cleanup = setupWebApp(setScheme);
    if (!initData()) {
      setFatal("outside");
      return cleanup;
    }
    void loadMe();
    return cleanup;
  }, [loadMe]);

  const updateUser = useCallback((user: User) => setMe((m) => (m ? { ...m, user } : m)), []);

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
  } else {
    const user = me.user;
    const isAdmin = user.role === "admin";
    const tabs: { id: Tab; label: string; icon: typeof CalendarClock }[] = [
      { id: "schedule", label: "График", icon: CalendarClock },
      { id: "profile", label: "Профиль", icon: CircleUser },
      ...(isAdmin ? [{ id: "admin" as const, label: "Админ", icon: ShieldCheck }] : []),
    ];
    body = (
      <>
        <header className="sticky top-0 z-30 flex items-center justify-between border-b bg-card/90 px-4 py-3 backdrop-blur">
          <div className="flex items-center gap-2.5">
            <span className="text-xl font-extrabold tracking-tight">
              Сме<span className="text-primary">ны</span>
            </span>
            <button
              onClick={() => setTab("profile")}
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold",
                user.work_status === "working"
                  ? "border-go/30 bg-go-soft text-go"
                  : "border-border bg-muted text-muted-foreground",
              )}
            >
              <span className={cn("size-1.5 rounded-full", user.work_status === "working" ? "bg-go" : "bg-muted-foreground")} />
              {user.work_status === "working" ? "Работаю" : "Не работаю"}
            </button>
          </div>
          {isAdmin && (
            <span className="rounded-full bg-off/10 px-2.5 py-0.5 text-xs font-semibold text-off">Админ</span>
          )}
        </header>

        <main className="mx-auto w-full max-w-xl flex-1 px-4 pt-4 pb-32">
          {tab === "schedule" && <ScheduleScreen user={user} pricing={me.pricing!} onOpenProfile={() => setTab("profile")} />}
          {tab === "profile" && <ProfileScreen user={user} pricing={me.pricing!} onUserChange={updateUser} />}
          {tab === "admin" && isAdmin && <AdminScreen />}
        </main>

        <nav className="pb-safe fixed inset-x-0 bottom-0 z-40 px-4">
          <div className="mx-auto flex max-w-xl gap-1 rounded-2xl border bg-card/95 p-1.5 shadow-lg shadow-black/5 backdrop-blur">
            {tabs.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => {
                  tapHaptic();
                  setTab(id);
                }}
                className={cn(
                  "flex flex-1 flex-col items-center gap-0.5 rounded-xl border border-transparent py-2 text-[11px] font-medium transition-colors",
                  tab === id ? "border-primary/25 bg-accent text-accent-foreground" : "text-muted-foreground",
                )}
              >
                <Icon className="size-5" strokeWidth={tab === id ? 2.2 : 1.8} />
                {label}
              </button>
            ))}
          </div>
        </nav>
      </>
    );
  }

  return (
    <div className="grid-paper flex min-h-dvh flex-col">
      {body}
      <Toaster position="top-center" theme={scheme} richColors />
    </div>
  );
}
