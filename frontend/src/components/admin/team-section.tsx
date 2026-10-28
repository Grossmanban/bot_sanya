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
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerClose, DrawerContent, DrawerDescription, DrawerTitle } from "@/components/ui/drawer";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, ROLE_TITLES, type Capability, type Role, type User } from "@/lib/api";
import { dayMonth, offFor } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { haptic, openProfile, webApp } from "@/lib/telegram";
import { useWide } from "@/lib/use-media";
import { cn } from "@/lib/utils";
import { Avatar } from "../avatar";
import { ReasonChips } from "../reason-chips";
import { PersonForm, StatusPill } from "../profile/person-form";
import { ReasonForm } from "../training/shared";
import { TxForm } from "../finance/money";

export function TeamSection({ me, caps, network }: { me: User; caps: Capability[]; network?: string | null }) {
  const { data, reload } = useLoad(() => api.admin.team(), []);
  const [selected, setSelected] = useState<User | null>(null);
  const { busy, run } = useAction();

  if (!data) return <Skeleton className="h-80 rounded-3xl" />;

  const live = (u: User) => u.status === "approved" && u.account_state !== "blocked";
  // ТЗ 28.10 §14: a candidate with a questionnaire is decided in «Онбординг» — not counted or shown here twice
  const pending = data.items.filter((u) => u.status === "pending" && !u.candidate);
  const candidates = data.items.filter((u) => u.status === "pending" && u.candidate).length;
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
      {(me.role === "trainer" || me.role === "instructor") && (
        <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
          <p className="text-sm text-hero-muted">Ваша сеть</p>
          <h2 className="font-display text-xl font-semibold">{network ?? "не назначена"}</h2>
          <p className="mt-1 text-sm text-hero-muted">
            {!network ? "Попросите владельца добавить вас в сеть — тогда здесь появится команда."
              : me.role === "instructor" ? "Вы видите и ведёте только закреплённых за вами сотрудников."
                : "Вы видите и ведёте только людей своей сети, включая людей инструкторов."}
          </p>
        </section>
      )}

      {(caps.includes("profiles") || caps.includes("money")) && <ProfileRequests onDone={reload} />}
      {caps.includes("remind_profiles") && <Reminder />}

      {canDecide && candidates > 0 && (
        <p className="px-1 text-sm text-muted-foreground">
          Кандидатов с анкетой: {candidates} — их заявки решаются в «Онбординге».
        </p>
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
        onUpdated={(u) => {
          setSelected((cur) => (cur ? { ...cur, ...u } : cur));
          void reload();
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
                  {u.profile_state && u.status === "approved" && (u.profile_state.profile.status !== "complete" || u.profile_state.payment.status !== "complete") && (
                    <span className="mt-1 flex flex-wrap gap-1">
                      {u.profile_state.profile.status !== "complete" && <StatusPill status={u.profile_state.profile.status} prefix="Профиль: " />}
                      {u.profile_state.payment.status !== "complete" && <StatusPill status={u.profile_state.payment.status} prefix="Реквизиты: " />}
                    </span>
                  )}
                </span>
                {u.account_state === "paused" ? (
                  <span className="flex items-center gap-1 rounded-full bg-wait-soft px-2 py-0.5 text-xs font-semibold text-wait">
                    <Pause className="size-3" /> пауза
                  </span>
                ) : u.status === "approved" && u.account_state === "active" && (
                  <span className={cn("flex items-center gap-1.5 text-xs", u.work_status === "working" ? "text-go" : "text-muted-foreground")}>
                    <span className={cn("size-1.5 rounded-full", u.work_status === "working" ? "bg-go" : "bg-idle")} />
                    {u.work_status === "working" ? "работает" : `не работает${offFor(u.status_days) ? ` · ${offFor(u.status_days)}` : ""}`}
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

const ROLE_OPTIONS: Exclude<Role, "owner">[] = ["employee", "instructor", "trainer", "admin"];

function MemberSheet({
  user,
  me,
  caps,
  onClose,
  onChanged,
  onUpdated,
}: {
  user: User | null;
  me: User;
  caps: Capability[];
  onClose: () => void;
  onChanged: () => void;
  /** the card stays open, the person's data changed (e.g. the weekly norm) */
  onUpdated: (u: User) => void;
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
                  <button className="mt-1 text-sm font-semibold text-primary underline-offset-2 hover:underline"
                          onClick={() => openProfile(user.username, user.telegram_id, `${user.first_name} ${user.last_name}`)}>
                    Открыть профиль в Telegram
                  </button>
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

              {approved && <PersonData key={`d-${user.id}`} userId={user.id} />}
              {approved && <Requisites userId={user.id} />}

              {!isSelf && user.username && (
                <Button variant="secondary" className="press mt-5 h-12 w-full rounded-2xl text-[15px] font-semibold"
                        onClick={() => openProfile(user.username, user.telegram_id)}>
                  <MessageCircle /> Написать в Telegram
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

              {approved && !isSelf && !user.is_owner && ["employee", "trainer", "instructor"].includes(user.role) && caps.includes("review") && (
                <NormPicker user={user} onUpdated={onUpdated} />
              )}
              {/* anyone who works shifts may get a personal rate — owners too, including themselves */}
              {approved && caps.includes("money") && (
                <PersonalRates key={user.id} user={user} onUpdated={onUpdated} />
              )}
              {approved && caps.includes("products") && <ProductAccess key={`p-${user.id}`} user={user} />}
              {approved && caps.includes("manuals") && <ManualAccess key={`m-${user.id}`} userId={user.id} />}
              {approved && caps.includes("registrations") && <TrainingAccess key={`t-${user.id}`} user={user} onChanged={() => onUpdated(user)} />}

              {canRole && (
                <div className="mt-6">
                  <p className="mb-2 text-xs text-muted-foreground">Права</p>
                  <div className="grid grid-cols-2 gap-1 rounded-2xl bg-muted p-1 sm:grid-cols-4">
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
                      : user.role === "instructor"
                        ? "Инструктор видит только закреплённых за ним сотрудников (назначаются в «Сетях»): смены, отчёты, паузы. Штрафы — через администратора."
                      : user.role === "admin"
                        ? "Администратор работает со всеми: смены, отчёты, штрафы, рассылки. Без денег и блокировок."
                        : "Сотрудник видит свой график, отчёты и штрафы."}
                  </p>
                </div>
              )}

              <div className="mt-6 space-y-2.5">
                {user.status === "pending" && user.candidate && (
                  <p className="text-center text-sm text-muted-foreground">Кандидат с анкетой — решение в «Онбординге».</p>
                )}
                {user.status === "pending" && canDecide && !user.candidate && (
                  <>
                    <Button className="press h-14 w-full rounded-2xl text-base font-semibold" disabled={!!busy}
                            onClick={() => act("allow", (k) => api.admin.setAccess(user.id, true, k), "Заявка принята")}>
                      {busy === "allow" && <LoaderCircle className="animate-spin" />} Открыть доступ
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
                    <ReasonChips kind="pause" value={reason} onPick={setReason} />
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
              Бот и приложение закроются, рабочие сообщения и напоминания приходить не будут, права будут сняты.
              История и деньги сохранятся, разблокировать можно в любой момент.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ReasonChips kind="block" value={reason} onPick={setReason} />
          <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value.slice(0, 300))} placeholder="Причина (обязательно, видна в архиве)"
                    className="w-full resize-none rounded-xl border-0 bg-muted px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25" />
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="rounded-xl"
              disabled={!reason.trim()}
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

/** SBP requisites in the member card: full for owners, masked for everyone else (ТЗ 10.2). */
function Requisites({ userId }: { userId: number }) {
  const { data } = useLoad(() => api.admin.userPaymentProfile(userId), [userId]);
  if (!data) return null;
  const p = data.profile;
  return (
    <div className="mt-5 rounded-2xl bg-muted/60 px-4 py-3 text-sm">
      <p className="text-xs text-muted-foreground">Реквизиты СБП</p>
      {p ? (
        <p className="mt-0.5 font-semibold">
          {p.bank}, <span className="tabular">{p.phone}</span>
          {p.recipient && <span className="block font-normal text-muted-foreground">{p.recipient}</span>}
        </p>
      ) : (
        <p className="mt-0.5 font-semibold text-wait">не заполнены</p>
      )}
    </div>
  );
}

/** ТЗ 12.4: a personal norm of work days a week — over the common one, or none at all. */
function NormPicker({ user, onUpdated }: { user: User; onUpdated: (u: User) => void }) {
  const { busy, run } = useAction();
  const value = user.min_weekly_shifts == null ? "common" : String(user.min_weekly_shifts);
  return (
    <label className="mt-4 flex items-center gap-3 rounded-2xl bg-muted/50 px-4 py-3">
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-semibold">Норма выходов в неделю</span>
        <span className="block text-xs text-muted-foreground">Меньше — заявка в «Дисциплину» по понедельникам</span>
      </span>
      {busy && <LoaderCircle className="size-4 animate-spin text-muted-foreground" />}
      <select
        aria-label="Норма выходов в неделю"
        disabled={!!busy}
        value={value}
        onChange={async (e) => {
          const v = e.target.value;
          const norm = v === "common" ? null : Number(v);
          try {
            const res = await run("norm", (k) => api.admin.setNorm(user.id, norm, k),
              norm == null ? "Действует общая норма" : norm === 0 ? "Норма выходов отключена" : `Личная норма: ${norm} в неделю`);
            if (res) onUpdated(res.user);
          } catch {
            /* toast shown */
          }
        }}
        className="h-10 shrink-0 rounded-xl border-0 bg-card px-3 text-sm font-semibold outline-none"
      >
        <option value="common">Общая</option>
        <option value="0">Без нормы</option>
        {[1, 2, 3, 4, 5, 6, 7].map((n) => (
          <option key={n} value={n}>{n} {n === 1 ? "день" : n < 5 ? "дня" : "дней"}</option>
        ))}
      </select>
    </label>
  );
}

/** Owner only (customer round B): a personal rate over the network's/common one and «не применять налог на лень». */
const PERSON_LABEL: Record<string, string> = { last_name: "Фамилия", first_name: "Имя", middle_name: "Отчество", age: "Возраст",
  metro: "Метро", min_days: "Мин. дней", work_days: "Удобные дни", phone: "Телефон", bank: "Банк", bank_name: "Название банка",
  recipient: "Получатель" };
const WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
const shown = (k: string, v: unknown) => v == null || v === "" ? "—" : k === "work_days" && Array.isArray(v) ? v.map((i) => WD[i as number]).join(", ") : String(v);

/** ТЗ 28.10 §7: «Изменения профилей» — what the employee proposes against what is current; approve or reject with a reason. */
function ProfileRequests({ onDone }: { onDone: () => void }) {
  const { data, setData } = useLoad(() => api.admin.profileRequests(), []);
  const [rejecting, setRejecting] = useState<number | null>(null);
  const { busy, run } = useAction();
  if (!data || data.items.length === 0) return null;
  const decide = async (id: number, approve: boolean, reason: string | null) => {
    try {
      const res = await run(`d${id}`, (k) => api.admin.decideProfile(id, approve, reason, k), approve ? "Одобрено" : "Отклонено");
      if (res) {
        setData(res);
        setRejecting(null);
        onDone();
      }
    } catch {
      /* toast shown */
    }
  };
  return (
    <section className="space-y-3 rounded-3xl bg-wait-soft p-4 md:p-5">
      <h2 className="font-semibold text-wait">Изменения профилей: {data.items.length}</h2>
      <ul className="space-y-2">
        {data.items.map((r) => (
          <li key={r.id} className="space-y-2 rounded-2xl bg-card p-3">
            <p className="font-semibold">
              <button className="underline-offset-2 hover:underline" onClick={() => openProfile(r.username, r.telegram_id, r.name)}>{r.name}</button>
              <span className="font-normal text-muted-foreground"> · {r.kind === "profile" ? "профиль" : "реквизиты"} · {r.created_at.slice(8, 10)}.{r.created_at.slice(5, 7)}</span>
            </p>
            <dl className="grid gap-1 text-sm">
              {Object.entries(r.data).map(([k, v]) => {
                const old = r.old?.[k];
                const changed = JSON.stringify(old) !== JSON.stringify(v);
                return (
                  <div key={k} className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted-foreground">{PERSON_LABEL[k] ?? k}</dt>
                    <dd className={cn("min-w-0 break-words", changed && "font-semibold")}>
                      {changed && r.old ? <><span className="text-muted-foreground line-through">{shown(k, old)}</span> → </> : null}{shown(k, v)}
                    </dd>
                  </div>
                );
              })}
            </dl>
            <div className="flex gap-2">
              <Button size="sm" className="h-9 rounded-xl" disabled={!!busy} onClick={() => decide(r.id, true, null)}>Одобрить</Button>
              <Button size="sm" variant="secondary" className="h-9 rounded-xl" onClick={() => setRejecting(rejecting === r.id ? null : r.id)}>Отклонить</Button>
            </div>
            {rejecting === r.id && (
              <ReasonForm kind="reject_request" busy={!!busy} confirm="Отклонить — сотрудник увидит причину"
                          onSave={(reason) => decide(r.id, false, reason)} />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** «📢 Напомнить о заполнении»: shows how many get it and asks before sending (ТЗ 28.10 §7). */
function Reminder() {
  const [preview, setPreview] = useState<{ count: number; names: string[]; text: string } | null>(null);
  const { busy, run } = useAction();
  return (
    <>
      <Button variant="secondary" className="press h-11 w-full rounded-2xl font-semibold" disabled={!!busy}
              onClick={async () => setPreview(await api.admin.reminderPreview())}>
        📢 Напомнить о заполнении
      </Button>
      <AlertDialog open={!!preview} onOpenChange={(o) => !o && setPreview(null)}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">
              {preview?.count ? `Отправить ${preview.count} ${preview.count % 10 === 1 && preview.count % 100 !== 11 ? "сотруднику" : "сотрудникам"}?` : "Некому напоминать"}
            </AlertDialogTitle>
            <AlertDialogDescription className="break-words">
              {preview?.count ? `Только тем, у кого профиль или реквизиты не заполнены или отклонены: ${preview.names.join(", ")}${preview.count > preview.names.length ? "…" : ""}.`
                : "У всех профили и реквизиты заполнены и одобрены."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {!!preview?.count && <p className="max-h-40 overflow-y-auto rounded-xl bg-muted p-3 text-sm whitespace-pre-wrap">{preview.text}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
            {!!preview?.count && (
              <AlertDialogAction className="rounded-xl" onClick={async () => {
                try {
                  const res = await run("remind", (k) => api.admin.sendReminder(k));
                  if (res) toast.success(`Отправлено: ${res.sent} из ${res.count}`);
                } catch {
                  /* toast shown */
                }
              }}>Отправить</AlertDialogAction>
            )}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/** The employee's data in their card: state, the change log; owners and admins may edit at once (§7). */
function PersonData({ userId }: { userId: number }) {
  const { data, setData } = useLoad(() => api.admin.userProfile(userId), [userId]);
  const [editing, setEditing] = useState(false);
  const [log, setLog] = useState(false);
  const { busy, run } = useAction();
  if (!data) return null;
  const p = data.profile;
  return (
    <div className="mt-4 space-y-2 rounded-2xl bg-muted/50 px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[15px] font-semibold">Данные сотрудника</span>
        <StatusPill status={data.state.status} />
      </div>
      <p className="text-sm break-words">
        {[p.last_name, p.first_name, p.middle_name].filter(Boolean).join(" ")}{p.age ? ` · ${p.age} лет` : ""}{p.metro ? ` · м. ${p.metro}` : ""}
        {p.min_days ? ` · от ${p.min_days} дн. (${p.work_days.map((i) => WD[i]).join(", ")})` : ""}
      </p>
      {data.missing.length > 0 && <p className="text-xs text-muted-foreground">Не заполнено: {data.missing.join(", ").toLowerCase()}</p>}
      {data.can_edit && !editing && (
        <button className="text-xs font-semibold text-primary" onClick={() => setEditing(true)}>Изменить сразу</button>
      )}
      {editing && (
        <PersonForm initial={data.draft} busy={!!busy} submitLabel="Сохранить без согласования"
                    onSubmit={async (d) => {
                      try {
                        const res = await run("edit", (k) => api.admin.editUserProfile(userId, d, k), "Данные сохранены");
                        if (res) { setData(res); setEditing(false); }
                      } catch {
                        /* toast shown */
                      }
                    }} />
      )}
      {(data.history?.length ?? 0) > 0 && (
        <button className="block text-xs font-semibold text-primary" onClick={() => setLog((x) => !x)}>{log ? "Скрыть журнал" : "Журнал изменений"}</button>
      )}
      {log && (
        <ul className="space-y-1.5 text-xs text-muted-foreground">
          {data.history!.map((h, i) => (
            <li key={i}>
              {h.at.slice(8, 10)}.{h.at.slice(5, 7)} {h.at.slice(11, 16)} · {h.by} · {h.how}
              {h.new && Object.keys(h.new).filter((k) => JSON.stringify(h.old?.[k as keyof typeof h.old]) !== JSON.stringify(h.new![k as keyof typeof h.new]))
                .map((k) => <span key={k} className="block">{PERSON_LABEL[k]}: {shown(k, h.old?.[k as keyof typeof h.old])} → {shown(k, h.new![k as keyof typeof h.new])}</span>)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** ТЗ 28.10 §12: which manuals this person reads in «Обучение»; one by one or all back at once. */
function ManualAccess({ userId }: { userId: number }) {
  const { data, setData } = useLoad(() => api.admin.userManuals(userId), [userId]);
  const { busy, run } = useAction();
  if (!data || data.items.length === 0) return null;
  const set = async (manualId: number | null, allow: boolean, ok: string) => {
    try {
      const res = await run(`m${manualId}`, (k) => api.admin.setUserManual(userId, manualId, allow, k), ok);
      if (res) setData(res);
    } catch {
      /* toast shown */
    }
  };
  const any = data.items.some((m) => m.assigned);
  return (
    <div className="mt-4 space-y-2 rounded-2xl bg-muted/50 px-4 py-3">
      <span className="block text-[15px] font-semibold">Методички</span>
      {data.items.map((m) => (
        <label key={m.id} className="flex cursor-pointer items-center gap-3">
          <span className="min-w-0 flex-1 text-[15px] break-words">{m.title}</span>
          <Switch checked={m.assigned} disabled={!!busy} onCheckedChange={(v) => set(m.id, v, v ? "Методичка выдана" : "Доступ к методичке закрыт")} />
        </label>
      ))}
      {any && (
        <button className="text-xs font-semibold text-cancel" disabled={!!busy} onClick={() => set(null, false, "Все методички закрыты")}>
          Закрыть все
        </button>
      )}
    </div>
  );
}

/** ТЗ 28.10 §3: «Обучение» — opened automatically for a candidate's trainer; an admin may give or take it,
 * but not while candidates are still assigned (the server says so). */
function TrainingAccess({ user, onChanged }: { user: User; onChanged: () => void }) {
  const [state, setState] = useState(user.training ?? { granted: false, assigned: 0 });
  const { busy, run } = useAction();
  const on = state.granted || state.assigned > 0;
  return (
    <label className="mt-4 flex cursor-pointer items-center gap-3 rounded-2xl bg-muted/50 px-4 py-3">
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-semibold">Раздел «Обучение»</span>
        <span className="block text-xs text-muted-foreground">
          {state.assigned ? `Кандидатов: ${state.assigned} — отключить можно после передачи другому тренеру` : on ? "Доступ открыт" : "Нет доступа"}
        </span>
      </span>
      <Switch checked={on} disabled={!!busy} onCheckedChange={async (v) => {
        try {
          const res = await run("training", (k) => api.admin.trainingAccess(user.id, v, k), v ? "«Обучение» открыто" : "«Обучение» закрыто");
          if (res) {
            setState(res.training);
            onChanged();
          }
        } catch {
          /* toast shown */
        }
      }} />
    </label>
  );
}

/** ТЗ 27.10 §28: access to «Работа» products — give, take back, the log of changes. */
function ProductAccess({ user }: { user: User }) {
  const { data, setData } = useLoad(() => api.admin.userProducts(user.id), [user.id]);
  const { busy, run } = useAction();
  const [log, setLog] = useState(false);
  if (!data) return null;
  return (
    <div className="mt-4 space-y-3 rounded-2xl bg-muted/50 px-4 py-3">
      <span className="block text-[15px] font-semibold">Работа: продукты</span>
      {data.items.map((p) => (
        <label key={p.product} className="flex cursor-pointer items-center gap-3">
          <span className="min-w-0 flex-1">
            <span className="block text-[15px]">{p.title}</span>
            <span className="block text-xs text-muted-foreground">{p.active ? "Доступ открыт" : "Нет доступа"}</span>
          </span>
          <Switch checked={p.active} disabled={!!busy} onCheckedChange={async (v) => {
            try {
              const res = await run("product", (k) => api.admin.setUserProduct(user.id, p.product, v, k),
                v ? `Доступ к «${p.title}» выдан` : `Доступ к «${p.title}» закрыт`);
              if (res) setData(res);
            } catch {
              /* toast shown */
            }
          }} />
        </label>
      ))}
      {data.history.length > 0 && (
        <button className="text-xs font-semibold text-primary" onClick={() => setLog((x) => !x)}>{log ? "Скрыть журнал" : "Журнал изменений"}</button>
      )}
      {log && (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {data.history.map((h, i) => (
            <li key={i}>
              {h.title}: выдан {h.granted_at.slice(8, 10)}.{h.granted_at.slice(5, 7)}{h.granted_by ? ` (${h.granted_by})` : ""}
              {h.revoked_at ? `, закрыт ${h.revoked_at.slice(8, 10)}.${h.revoked_at.slice(5, 7)}${h.revoked_by ? ` (${h.revoked_by})` : ""}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** ТЗ 27.10 §24: a trainer's or an instructor's own rate per piece of their people (over the ladder and the common one). */
function OwnCommission({ user, save, busy }: {
  user: User; busy: boolean;
  save: (body: Parameters<typeof api.admin.setUserRates>[1], ok: string) => Promise<void>;
}) {
  const saved = user.personal_commission != null ? String(user.personal_commission) : "";
  const [value, setValue] = useState(saved);
  const whose = user.role === "trainer" ? "сети" : "своих людей";
  return (
    <>
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold">{user.role === "trainer" ? "Ставка тренера" : "Ставка инструктора"}</span>
          <span className="block text-xs text-muted-foreground">
            ₽ за штуку {whose}. Пусто — по лестнице сети или общая. Вместе тренер и инструктор получают не больше дельты (150 ₽ − общая ставка).
          </span>
        </span>
        <div className="relative w-24 shrink-0">
          <input inputMode="numeric" aria-label="Ставка с объёма, ₽ за штуку" placeholder="лестница" value={value}
                 onChange={(e) => setValue(e.target.value.replace(/\D/g, "").slice(0, 6))}
                 className="h-10 w-full rounded-xl border-0 bg-card pr-7 pl-3 text-sm font-semibold tabular outline-none focus:ring-2 focus:ring-ring/25" />
          <span className="absolute top-1/2 right-2.5 -translate-y-1/2 text-xs text-muted-foreground">₽</span>
        </div>
      </div>
      {value !== saved && (
        <Button size="sm" className="press h-9 w-full rounded-xl" disabled={busy}
                onClick={() => save(value ? { personal_commission: Number(value) } : { clear_personal_commission: true },
                  value ? `Ставка с объёма: ${value} ₽ за штуку` : "Ставка с объёма — по лестнице")}>
          Сохранить ставку с объёма
        </Button>
      )}
    </>
  );
}

function PersonalRates({ user, onUpdated }: { user: User; onUpdated: (u: User) => void }) {
  const { busy, run } = useAction();
  const [rate, setRate] = useState(user.personal_rate != null ? String(user.personal_rate) : "");
  const saved = user.personal_rate != null ? String(user.personal_rate) : "";
  async function save(body: Parameters<typeof api.admin.setUserRates>[1], ok: string) {
    try {
      const res = await run("rates", (k) => api.admin.setUserRates(user.id, body, k), ok);
      if (res) onUpdated(res.user);
    } catch {
      /* toast shown */
    }
  }
  return (
    <div className="mt-4 space-y-3 rounded-2xl bg-muted/50 px-4 py-3">
      <div className="flex items-center gap-3">
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold">Личная ставка</span>
          <span className="block text-xs text-muted-foreground">Пусто — общая ставка. Видит и меняет только владелец</span>
        </span>
        <div className="relative w-24 shrink-0">
          <input inputMode="numeric" aria-label="Личная ставка, ₽ за штуку" placeholder="общая" value={rate}
                 onChange={(e) => setRate(e.target.value.replace(/\D/g, "").slice(0, 6))}
                 className="h-10 w-full rounded-xl border-0 bg-card pr-7 pl-3 text-sm font-semibold tabular outline-none focus:ring-2 focus:ring-ring/25" />
          <span className="absolute top-1/2 right-2.5 -translate-y-1/2 text-xs text-muted-foreground">₽</span>
        </div>
      </div>
      {rate !== saved && (
        <Button size="sm" className="press h-9 w-full rounded-xl" disabled={!!busy}
                onClick={() => save(rate ? { personal_rate: Number(rate) } : { clear_personal_rate: true },
                  rate ? `Личная ставка: ${rate} ₽ за штуку` : "Личная ставка снята")}>
          {busy && <LoaderCircle className="animate-spin" />} Сохранить ставку
        </Button>
      )}
      {(user.role === "trainer" || user.role === "instructor") && <OwnCommission user={user} save={save} busy={!!busy} />}
      <label className="flex cursor-pointer items-center gap-3">
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold">Не применять налог на лень</span>
          <span className="block text-xs text-muted-foreground">Полная ставка, даже если за день сделано меньше порога</span>
        </span>
        <Switch checked={!!user.lazy_exempt} disabled={!!busy}
                onCheckedChange={(v) => save({ lazy_exempt: v }, v ? "Налог на лень не применяется" : "Налог на лень применяется")} />
      </label>
    </div>
  );
}
