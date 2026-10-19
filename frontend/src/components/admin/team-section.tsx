"use client";

import { useState } from "react";
import { Ban, Check, ChevronRight, Copy, LoaderCircle, MessageCircle, Pause, Play, Send, Wallet, X } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerClose, DrawerContent, DrawerDescription, DrawerTitle } from "@/components/ui/drawer";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ROLE_TITLES, type Capability, type Role, type User } from "@/lib/api";
import { dayMonth } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { haptic, openChat, webApp } from "@/lib/telegram";
import { useWide } from "@/lib/use-media";
import { cn } from "@/lib/utils";
import { Avatar } from "../avatar";
import { TxForm } from "../finance/money";

export function TeamSection({ me, caps, network }: { me: User; caps: Capability[]; network?: string | null }) {
  const { data, reload } = useLoad(() => api.admin.team(), []);
  const [selected, setSelected] = useState<User | null>(null);
  const { busy, run } = useAction();

  if (!data) return <Skeleton className="h-80 rounded-3xl" />;

  const live = (u: User) => u.status === "approved" && u.account_state !== "blocked";
  const pending = data.items.filter((u) => u.status === "pending");
  const leads = data.items.filter((u) => live(u) && u.role !== "employee");
  const staff = data.items.filter((u) => live(u) && u.role === "employee");
  const blocked = data.items.filter((u) => u.status === "approved" && u.account_state === "blocked");
  const closed = data.items.filter((u) => u.status === "rejected");
  const canDecide = caps.includes("registrations");

  async function decide(user: User, allow: boolean) {
    try {
      await run(`${allow ? "ok" : "no"}-${user.id}`, (k) => api.admin.setAccess(user.id, allow, k), allow ? "Заявка принята" : "Заявка отклонена");
      await reload();
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="space-y-4">
      {canDecide && <Invite botUsername={data.bot_username} />}
      {me.role === "trainer" && (
        <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
          <p className="text-sm text-hero-muted">Ваша сеть</p>
          <h2 className="font-display text-xl font-semibold">{network ?? "не назначена"}</h2>
          <p className="mt-1 text-sm text-hero-muted">
            {network ? "Вы видите и ведёте только людей своей сети." : "Попросите владельца добавить вас в сеть — тогда здесь появится команда."}
          </p>
        </section>
      )}

      {canDecide && pending.length > 0 && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">
            Заявки <span className="text-muted-foreground">{pending.length}</span>
          </h2>
          <ul className="divide-y">
            {pending.map((u) => (
              <li key={u.id} className="row-in flex flex-wrap items-center gap-3 px-5 py-3.5 md:px-6">
                <Avatar user={u} className="size-10 rounded-xl bg-wait text-sm text-white" />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{u.first_name} {u.last_name}</p>
                  <p className="text-sm text-muted-foreground">
                    {u.username ? `@${u.username}` : `ID ${u.telegram_id}`}
                    {u.created_at ? `, ${dayMonth(u.created_at.slice(0, 10))}` : ""}
                  </p>
                </div>
                <div className="flex basis-full gap-2 pl-[3.25rem] sm:basis-auto sm:pl-0">
                  <Button size="sm" className="press h-10 flex-1 rounded-xl px-4 sm:flex-none" disabled={!!busy} onClick={() => decide(u, true)}>
                    {busy === `ok-${u.id}` ? <LoaderCircle className="animate-spin" /> : <Check />} Принять
                  </Button>
                  <Button size="sm" variant="secondary" className="press h-10 rounded-xl px-3" disabled={!!busy}
                          aria-label="Отклонить" onClick={() => decide(u, false)}>
                    {busy === `no-${u.id}` ? <LoaderCircle className="animate-spin" /> : <X />}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {leads.length > 0 && <Group title="Руководство" users={leads} me={me} onPick={setSelected} />}
      <Group title="Сотрудники" users={staff} me={me} onPick={setSelected}
             empty={canDecide ? "Пока никого. Отправьте приглашение выше." : "В вашей сети пока никого."} />
      {blocked.length > 0 && <Group title="Заблокированы" users={blocked} me={me} onPick={setSelected} muted />}
      {closed.length > 0 && <Group title="Заявка отклонена" users={closed} me={me} onPick={setSelected} muted />}

      <MemberSheet
        user={selected}
        me={me}
        caps={caps}
        onClose={() => setSelected(null)}
        onChanged={async () => {
          setSelected(null);
          await reload();
        }}
      />
    </div>
  );
}

function Invite({ botUsername }: { botUsername: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!botUsername) {
    return (
      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h2 className="font-semibold">Как добавить сотрудника</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Человек открывает бота, нажимает /start и отправляет заявку. Она появится здесь и придёт в бот.
        </p>
      </section>
    );
  }
  const link = `https://t.me/${botUsername}`;
  const text = "Регистрация в графике: открой бота, нажми «Старт» и отправь заявку.";

  async function copy() {
    try {
      await navigator.clipboard.writeText(link);
    } catch {
      /* clipboard blocked: the link is visible anyway */
    }
    haptic("success");
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  return (
    <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
      <h2 className="font-display text-xl font-semibold">Новый сотрудник</h2>
      <p className="mt-1.5 text-[15px] text-hero-muted">
        Отправьте ссылку на бота. Человек нажмёт «Старт», введёт фамилию и имя, а заявка появится здесь.
      </p>
      <p className="mt-4 truncate rounded-2xl bg-white/[0.08] px-4 py-3 font-medium">{link.replace("https://", "")}</p>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <Button
          className="press h-12 rounded-2xl bg-white text-[15px] font-semibold text-hero hover:bg-white/90"
          onClick={() => {
            const share = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(text)}`;
            const tg = webApp();
            if (tg) tg.openTelegramLink(share);
            else window.open(share, "_blank");
          }}
        >
          <Send /> Отправить
        </Button>
        <Button className="press h-12 rounded-2xl bg-white/10 text-[15px] font-semibold text-white hover:bg-white/15" onClick={copy}>
          {copied ? <Check className="pop" /> : <Copy />} {copied ? "Скопировано" : "Скопировать"}
        </Button>
      </div>
    </section>
  );
}

function Group({
  title,
  users,
  me,
  onPick,
  empty,
  muted,
}: {
  title: string;
  users: User[];
  me: User;
  onPick: (u: User) => void;
  empty?: string;
  muted?: boolean;
}) {
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">
        {title} <span className="text-muted-foreground">{users.length}</span>
      </h2>
      {users.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted-foreground md:px-6">{empty}</p>
      ) : (
        <ul className="divide-y">
          {users.map((u) => (
            <li key={u.id}>
              <button onClick={() => onPick(u)} className="press flex w-full items-center gap-3 px-5 py-3 text-left hover:bg-muted/60 md:px-6">
                <Avatar user={u} className={cn("size-10 rounded-xl text-sm", muted && "bg-muted text-muted-foreground")} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">
                    {u.first_name} {u.last_name}
                    {u.id === me.id && <span className="font-normal text-muted-foreground"> (вы)</span>}
                  </span>
                  <span className="block text-sm text-muted-foreground">
                    {u.role !== "employee" ? ROLE_TITLES[u.role] : u.username ? `@${u.username}` : `ID ${u.telegram_id}`}
                    {u.network && <span> · {u.network}</span>}
                  </span>
                </span>
                {u.account_state === "paused" ? (
                  <span className="flex items-center gap-1 rounded-full bg-wait-soft px-2 py-0.5 text-xs font-semibold text-wait">
                    <Pause className="size-3" /> пауза
                  </span>
                ) : u.status === "approved" && u.account_state === "active" && (
                  <span className={cn("flex items-center gap-1.5 text-xs", u.work_status === "working" ? "text-go" : "text-muted-foreground")}>
                    <span className={cn("size-1.5 rounded-full", u.work_status === "working" ? "bg-go" : "bg-idle")} />
                    {u.work_status === "working" ? "работает" : "не работает"}
                  </span>
                )}
                <ChevronRight className="size-4 text-muted-foreground" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const ROLE_OPTIONS: Exclude<Role, "owner">[] = ["employee", "trainer", "admin"];

function MemberSheet({
  user,
  me,
  caps,
  onClose,
  onChanged,
}: {
  user: User | null;
  me: User;
  caps: Capability[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const wide = useWide();
  const { busy, run } = useAction();
  const [pausing, setPausing] = useState(false);
  const [money, setMoney] = useState(false);
  const [blocking, setBlocking] = useState(false);
  const [reason, setReason] = useState("");

  const isSelf = user?.id === me.id;
  const approved = user?.status === "approved";
  const state = user?.account_state ?? "active";
  const touchable = !!user && approved && !isSelf && !user.is_owner;
  const canRole = touchable && caps.includes("roles") && state !== "blocked";
  const canPause = touchable && caps.includes("pause") && state !== "blocked";
  const canBlock = touchable && caps.includes("block");
  const canDecide = caps.includes("registrations");

  async function act(name: string, fn: (key: string) => Promise<unknown>, success: string) {
    try {
      await run(name, fn, success);
      setPausing(false);
      setBlocking(false);
      setReason("");
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  const statusText = !user
    ? ""
    : user.status === "pending"
      ? "Ждёт решения"
      : user.status === "rejected"
        ? "Заявка отклонена"
        : ROLE_TITLES[user.role];

  return (
    <>
      <Drawer
        open={!!user}
        onOpenChange={(o) => {
          if (!o) {
            setPausing(false);
            setMoney(false);
            setReason("");
            onClose();
          }
        }}
        direction={wide ? "right" : "bottom"}
        repositionInputs={false}
      >
        <DrawerContent
          className={cn(
            "border-0 bg-card",
            wide ? "w-[26rem] max-w-[26rem] sm:max-w-[26rem]" : "mx-auto max-h-[92dvh] max-w-2xl rounded-t-[1.75rem]",
          )}
        >
          {user && (
            <div className={cn("overflow-y-auto px-5", wide ? "h-full py-8" : "pb-safe pt-2")}>
              <div className="flex items-start gap-4">
                <Avatar user={user} className={cn("size-16 rounded-2xl text-xl", state === "blocked" && "bg-muted text-muted-foreground")} />
                <div className="min-w-0 flex-1">
                  <DrawerTitle className="font-display text-xl font-semibold">{user.first_name} {user.last_name}</DrawerTitle>
                  <DrawerDescription>
                    {user.username ? `@${user.username}, ` : ""}ID {user.telegram_id}
                  </DrawerDescription>
                </div>
                {wide && (
                  <DrawerClose className="grid size-9 place-items-center rounded-full bg-muted" aria-label="Закрыть">
                    <X className="size-4" />
                  </DrawerClose>
                )}
              </div>

              {state !== "active" && (
                <div className={cn("mt-5 flex items-start gap-3 rounded-2xl p-4",
                  state === "paused" ? "bg-wait-soft text-wait" : "bg-cancel-soft text-cancel")}>
                  {state === "paused" ? <Pause className="mt-0.5 size-4 shrink-0" /> : <Ban className="mt-0.5 size-4 shrink-0" />}
                  <div className="text-sm">
                    <p className="font-semibold">{state === "paused" ? "На паузе" : "Заблокирован"}</p>
                    {user.state_reason && <p className="mt-0.5 text-foreground/80">{user.state_reason}</p>}
                  </div>
                </div>
              )}

              <dl className="mt-6 grid grid-cols-2 gap-4 text-[15px]">
                <div>
                  <dt className="text-xs text-muted-foreground">Роль</dt>
                  <dd className="mt-0.5 font-semibold">{statusText}</dd>
                </div>
                {user.network && (
                  <div>
                    <dt className="text-xs text-muted-foreground">Сеть</dt>
                    <dd className="mt-0.5 font-semibold">{user.network}</dd>
                  </div>
                )}
                {user.created_at && (
                  <div>
                    <dt className="text-xs text-muted-foreground">В системе с</dt>
                    <dd className="mt-0.5 font-semibold">{dayMonth(user.created_at.slice(0, 10))}</dd>
                  </div>
                )}
              </dl>

              {!isSelf && (
                <Button variant="secondary" className="press mt-5 h-12 w-full rounded-2xl text-[15px] font-semibold"
                        onClick={() => openChat(user)}>
                  <MessageCircle /> Открыть в Telegram
                </Button>
              )}

              {approved && !isSelf && state !== "blocked" && (caps.includes("bonus") || caps.includes("fine_direct")) && (
                <div className="mt-4">
                  <Button variant="secondary" className="press h-12 w-full rounded-2xl text-[15px] font-semibold" onClick={() => setMoney((v) => !v)}>
                    <Wallet /> {money ? "Скрыть" : "Штраф, бонус, вычет"}
                  </Button>
                  {money && (
                    <div className="row-in mt-3 rounded-2xl bg-muted/50 p-4">
                      <TxForm userId={user.id} onDone={() => setMoney(false)} />
                    </div>
                  )}
                </div>
              )}

              {canRole && (
                <div className="mt-6">
                  <p className="mb-2 text-xs text-muted-foreground">Права</p>
                  <div className="grid grid-cols-3 gap-1 rounded-2xl bg-muted p-1">
                    {ROLE_OPTIONS.map((r) => (
                      <button
                        key={r}
                        disabled={!!busy}
                        onClick={() => r !== user.role && act(`role-${r}`, (k) => api.admin.setRole(user.id, r, k), `Роль: ${ROLE_TITLES[r]}`)}
                        className={cn(
                          "press flex h-11 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold transition-colors",
                          user.role === r ? "bg-card text-foreground shadow-lift" : "text-muted-foreground",
                        )}
                      >
                        {busy === `role-${r}` && <LoaderCircle className="size-4 animate-spin" />}
                        {ROLE_TITLES[r]}
                      </button>
                    ))}
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                    {user.role === "trainer"
                      ? "Тренер видит только свою сеть: смены, отчёты, паузы. Штрафы — через администратора."
                      : user.role === "admin"
                        ? "Администратор работает со всеми: смены, отчёты, штрафы, рассылки. Без денег и блокировок."
                        : "Сотрудник видит свой график, отчёты и штрафы."}
                  </p>
                </div>
              )}

              <div className="mt-6 space-y-2.5">
                {user.status === "pending" && canDecide && (
                  <>
                    <Button className="press h-14 w-full rounded-2xl text-base font-semibold" disabled={!!busy}
                            onClick={() => act("allow", (k) => api.admin.setAccess(user.id, true, k), "Заявка принята")}>
                      {busy === "allow" && <LoaderCircle className="animate-spin" />} Принять в команду
                    </Button>
                    <Button variant="secondary" className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                            onClick={() => act("deny", (k) => api.admin.setAccess(user.id, false, k), "Заявка отклонена")}>
                      Отклонить
                    </Button>
                  </>
                )}
                {user.status === "rejected" && canDecide && (
                  <Button className="press h-14 w-full rounded-2xl text-base font-semibold" disabled={!!busy}
                          onClick={() => act("reopen", (k) => api.admin.setAccess(user.id, true, k), "Доступ открыт")}>
                    {busy === "reopen" && <LoaderCircle className="animate-spin" />} Вернуть доступ
                  </Button>
                )}

                {canPause && state === "paused" && (
                  <Button className="press h-14 w-full rounded-2xl text-base font-semibold" disabled={!!busy}
                          onClick={() => act("resume", (k) => api.admin.setState(user.id, "active", null, k), "Пауза снята")}>
                    {busy === "resume" ? <LoaderCircle className="animate-spin" /> : <Play />} Снять паузу
                  </Button>
                )}
                {canPause && state === "active" && !pausing && (
                  <Button variant="secondary" className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy}
                          onClick={() => setPausing(true)}>
                    <Pause /> Поставить на паузу
                  </Button>
                )}
                {pausing && (
                  <div className="row-in space-y-2.5 rounded-2xl bg-wait-soft p-4">
                    <p className="text-sm font-semibold text-wait">Пауза: новые смены недоступны, статус — «Не работаю»</p>
                    <textarea rows={2} autoFocus value={reason} onChange={(e) => setReason(e.target.value.slice(0, 300))}
                              placeholder="Причина, например «больничный до 25.10»"
                              className="w-full resize-none rounded-xl border-0 bg-card px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25" />
                    <div className="flex gap-2">
                      <Button className="press h-11 flex-1 rounded-xl font-semibold" disabled={!!busy || !reason.trim()}
                              onClick={() => act("pause", (k) => api.admin.setState(user.id, "paused", reason.trim(), k), "На паузе")}>
                        {busy === "pause" && <LoaderCircle className="animate-spin" />} Поставить на паузу
                      </Button>
                      <Button variant="ghost" className="h-11 rounded-xl" onClick={() => { setPausing(false); setReason(""); }}>
                        Отмена
                      </Button>
                    </div>
                  </div>
                )}

                {canBlock && state === "blocked" && (
                  <Button className="press h-14 w-full rounded-2xl text-base font-semibold" disabled={!!busy}
                          onClick={() => act("unblock", (k) => api.admin.setState(user.id, "active", null, k), "Разблокирован")}>
                    {busy === "unblock" && <LoaderCircle className="animate-spin" />} Разблокировать
                  </Button>
                )}
                {canBlock && state !== "blocked" && (
                  <Button variant="ghost" className="h-12 w-full rounded-2xl text-[15px] font-semibold text-cancel hover:bg-cancel-soft hover:text-cancel"
                          disabled={!!busy} onClick={() => { setPausing(false); setReason(""); setBlocking(true); }}>
                    <Ban /> Заблокировать
                  </Button>
                )}

                {approved && touchable && !caps.includes("roles") && user.role === "employee" && (
                  <p className="pt-1 text-center text-sm text-muted-foreground">Роли и блокировки — у владельца.</p>
                )}
                {isSelf && <p className="pt-1 text-center text-sm text-muted-foreground">Это вы.</p>}
              </div>
            </div>
          )}
        </DrawerContent>
      </Drawer>

      <AlertDialog open={blocking} onOpenChange={(o) => { setBlocking(o); if (!o) setReason(""); }}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Заблокировать {user?.first_name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Доступ к графику и отчётам закроется, права будут сняты. История и деньги сохранятся, разблокировать можно в любой момент.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value.slice(0, 300))} placeholder="Причина (видна в архиве)"
                    className="w-full resize-none rounded-xl border-0 bg-muted px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25" />
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="rounded-xl"
              onClick={() => user && act("block", (k) => api.admin.setState(user.id, "blocked", reason.trim() || null, k), "Заблокирован")}
            >
              Заблокировать
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
