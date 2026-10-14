"use client";

import { useState } from "react";
import { Check, ChevronRight, Copy, LoaderCircle, Send, X } from "lucide-react";
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
import { api, type User } from "@/lib/api";
import { dayMonth } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { haptic, webApp } from "@/lib/telegram";
import { useWide } from "@/lib/use-media";
import { cn } from "@/lib/utils";
import { Avatar } from "../avatar";

export function TeamSection({ me }: { me: User }) {
  const { data, reload } = useLoad(() => api.admin.team(), []);
  const [selected, setSelected] = useState<User | null>(null);
  const { busy, run } = useAction();

  if (!data) return <Skeleton className="h-80 rounded-3xl" />;

  const pending = data.items.filter((u) => u.status === "pending");
  const admins = data.items.filter((u) => u.status === "approved" && u.role === "admin");
  const staff = data.items.filter((u) => u.status === "approved" && u.role === "employee");
  const closed = data.items.filter((u) => u.status === "rejected");

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
      <Invite botUsername={data.bot_username} />

      {pending.length > 0 && (
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

      <Group title="Администраторы" users={admins} me={me} onPick={setSelected} />
      <Group title="Сотрудники" users={staff} me={me} onPick={setSelected} empty="Пока никого. Отправьте приглашение выше." />
      {closed.length > 0 && <Group title="Доступ закрыт" users={closed} me={me} onPick={setSelected} muted />}

      <MemberSheet
        user={selected}
        me={me}
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
                    {u.is_owner ? "Владелец" : u.username ? `@${u.username}` : `ID ${u.telegram_id}`}
                  </span>
                </span>
                {u.status === "approved" && (
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

function MemberSheet({
  user,
  me,
  onClose,
  onChanged,
}: {
  user: User | null;
  me: User;
  onClose: () => void;
  onChanged: () => void;
}) {
  const wide = useWide();
  const { busy, run } = useAction();
  const [confirmClose, setConfirmClose] = useState(false);

  const isSelf = user?.id === me.id;
  const owner = !!me.is_owner;
  const approved = user?.status === "approved";
  const canRole = !!user && approved && owner && !user.is_owner;
  const canClose = !!user && approved && !isSelf && !user.is_owner && (user.role === "employee" || owner);

  async function act(name: string, fn: (key: string) => Promise<unknown>, success: string) {
    try {
      await run(name, fn, success);
      onChanged();
    } catch {
      /* toast shown */
    }
  }

  const statusText = !user
    ? ""
    : user.status === "pending"
      ? "Ждёт решения по заявке"
      : user.status === "rejected"
        ? "Доступ закрыт"
        : user.is_owner
          ? "Владелец: всегда администратор"
          : user.role === "admin"
            ? "Администратор"
            : "Сотрудник";

  return (
    <>
      <Drawer open={!!user} onOpenChange={(o) => !o && onClose()} direction={wide ? "right" : "bottom"} repositionInputs={false}>
        <DrawerContent
          className={cn(
            "border-0 bg-card",
            wide ? "w-[26rem] max-w-[26rem] sm:max-w-[26rem]" : "mx-auto max-h-[92dvh] max-w-2xl rounded-t-[1.75rem]",
          )}
        >
          {user && (
            <div className={cn("overflow-y-auto px-5", wide ? "h-full py-8" : "pb-safe pt-2")}>
              <div className="flex items-start gap-4">
                <Avatar user={user} className="size-16 rounded-2xl text-xl" />
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
              <dl className="mt-6 grid grid-cols-2 gap-4 text-[15px]">
                <div>
                  <dt className="text-xs text-muted-foreground">Роль</dt>
                  <dd className="mt-0.5 font-semibold">{statusText}</dd>
                </div>
                {user.created_at && (
                  <div>
                    <dt className="text-xs text-muted-foreground">В системе с</dt>
                    <dd className="mt-0.5 font-semibold">{dayMonth(user.created_at.slice(0, 10))}</dd>
                  </div>
                )}
              </dl>

              <div className="mt-6 space-y-2.5">
                {user.status === "pending" && (
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
                {canRole && (
                  <Button
                    className="press h-14 w-full rounded-2xl text-base font-semibold"
                    variant={user.role === "admin" ? "secondary" : "default"}
                    disabled={!!busy}
                    onClick={() =>
                      user.role === "admin"
                        ? act("role", (k) => api.admin.setRole(user.id, "employee", k), "Права администратора сняты")
                        : act("role", (k) => api.admin.setRole(user.id, "admin", k), "Назначен администратором")
                    }
                  >
                    {busy === "role" && <LoaderCircle className="animate-spin" />}
                    {user.role === "admin" ? "Снять права администратора" : "Сделать администратором"}
                  </Button>
                )}
                {user.status === "rejected" && (
                  <Button className="press h-14 w-full rounded-2xl text-base font-semibold" disabled={!!busy}
                          onClick={() => act("reopen", (k) => api.admin.setAccess(user.id, true, k), "Доступ открыт")}>
                    {busy === "reopen" && <LoaderCircle className="animate-spin" />} Вернуть доступ
                  </Button>
                )}
                {canClose && (
                  <Button variant="ghost" className="h-12 w-full rounded-2xl text-[15px] font-semibold text-cancel hover:bg-cancel-soft hover:text-cancel"
                          disabled={!!busy} onClick={() => setConfirmClose(true)}>
                    Закрыть доступ
                  </Button>
                )}
                {approved && !owner && !user.is_owner && user.role === "employee" && (
                  <p className="pt-1 text-center text-sm text-muted-foreground">Назначать администраторов может только владелец.</p>
                )}
                {isSelf && <p className="pt-1 text-center text-sm text-muted-foreground">Это вы.</p>}
              </div>
            </div>
          )}
        </DrawerContent>
      </Drawer>

      <AlertDialog open={confirmClose} onOpenChange={setConfirmClose}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Закрыть доступ?</AlertDialogTitle>
            <AlertDialogDescription>
              {user?.first_name} больше не сможет открыть график и записываться на выходы. История смен сохранится, доступ можно вернуть.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Оставить</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              className="rounded-xl"
              onClick={() => user && act("close", (k) => api.admin.setAccess(user.id, false, k), "Доступ закрыт")}
            >
              Закрыть доступ
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
