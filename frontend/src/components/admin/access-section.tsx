"use client";

import { useState } from "react";
import { Ban, Check, Copy, LoaderCircle, Plus, QrCode, RefreshCw, ShieldAlert, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type AccessAttempt, type Invite } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { useAction, useLoad } from "@/lib/hooks";
import { haptic, openProfile } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { QrImage } from "../link-timer";

const TTL = [
  { label: "1 час", minutes: 60 },
  { label: "1 день", minutes: 24 * 60 },
  { label: "7 дней", minutes: 7 * 24 * 60 },
];
const USES: { label: string; value: number | null }[] = [
  { label: "1 человек", value: 1 },
  { label: "5", value: 5 },
  { label: "20", value: 20 },
  { label: "Без лимита", value: null },
];
const STATE: Record<Invite["state"], { label: string; tone: string }> = {
  active: { label: "Действует", tone: "bg-go-soft text-go" },
  expired: { label: "Истекла", tone: "bg-muted text-muted-foreground" },
  revoked: { label: "Отозвана", tone: "bg-cancel-soft text-cancel" },
  used_up: { label: "Использована", tone: "bg-muted text-muted-foreground" },
};
const KIND: Record<AccessAttempt["kind"], string> = {
  no_link: "/start без ссылки",
  bad_link: "Неактуальная ссылка",
  app_no_link: "Открыл приложение без ссылки",
};

const when = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)} ${iso.slice(11, 16)}`;

/** ТЗ 27.10 §5–8: owners' invitations, «Обновить все ссылки» and the log of strangers. */
export function AccessSection() {
  const canInvite = useCan("invites");
  const [tab, setTab] = useState<"invites" | "attempts">(canInvite ? "invites" : "attempts");
  return (
    <div className="space-y-4">
      <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
        <h2 className="font-display text-xl font-semibold">Доступ</h2>
        <p className="mt-1.5 text-[15px] text-hero-muted">
          В бота и приложение попадают только по ссылке: реферальной ссылке сотрудника или приглашению владельца.
          Остальным бот не отвечает — их попытки видны здесь.
        </p>
      </section>
      {canInvite && (
        <div className="grid grid-cols-2 rounded-2xl bg-muted p-1">
          {(["invites", "attempts"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
                    className={cn("press h-10 rounded-xl text-sm font-semibold", tab === t ? "bg-card shadow-lift" : "text-muted-foreground")}>
              {t === "invites" ? "Приглашения" : "Попытки доступа"}
            </button>
          ))}
        </div>
      )}
      {tab === "invites" && canInvite ? <Invites /> : <Attempts />}
    </div>
  );
}

function Invites() {
  const { data, setData } = useLoad(() => api.admin.invites(), []);
  const { busy, run } = useAction();
  const [label, setLabel] = useState("");
  const [ttl, setTtl] = useState(60);
  const [uses, setUses] = useState<number | null>(1);
  const [confirmRotate, setConfirmRotate] = useState(false);

  async function create() {
    try {
      const res = await run("create", (k) => api.admin.createInvite({ label: label.trim(), ttl_minutes: ttl, max_uses: uses }, k),
        "Приглашение создано");
      if (res) {
        setData(res);
        setLabel("");
        if (res.invite.url) void copy(res.invite.url);
      }
    } catch {
      /* toast shown */
    }
  }

  async function rotate() {
    try {
      const res = await run("rotate", (k) => api.admin.rotateInvites(k));
      if (res) {
        setData(res);
        toast.success(`Ссылки обновлены: реферальных ${res.rotated.ref}, приглашений ${res.rotated.owner}`);
      }
    } catch {
      /* toast shown */
    } finally {
      setConfirmRotate(false);
    }
  }

  return (
    <div className="space-y-4">
      <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h3 className="font-semibold">Новое приглашение</h3>
        <p className="text-sm text-muted-foreground">
          Приглашённый откроет бота, укажет фамилию и имя и отправит заявку — администраторы её одобрят. Обучение и
          стажировка для него не нужны.
        </p>
        <input className="h-12 w-full rounded-2xl border-0 bg-muted px-4 outline-none focus:ring-2 focus:ring-ring/25"
               placeholder="Для кого (по желанию), например «Курьеры, листовки»" value={label} maxLength={60}
               onChange={(e) => setLabel(e.target.value)} />
        <Chips title="Срок действия" items={TTL.map((t) => ({ label: t.label, on: ttl === t.minutes, pick: () => setTtl(t.minutes) }))} />
        <Chips title="Сколько человек" items={USES.map((u) => ({ label: u.label, on: uses === u.value, pick: () => setUses(u.value) }))} />
        <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy} onClick={create}>
          {busy === "create" ? <LoaderCircle className="animate-spin" /> : <Plus />} Создать и скопировать ссылку
        </Button>
      </section>

      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h3 className="font-semibold">Обновить все ссылки</h3>
        <p className="text-sm text-muted-foreground">
          Все действующие реферальные ссылки сотрудников, их QR-коды и приглашения перестанут работать сразу.
          Сотрудники увидят новые ссылки у себя автоматически, приглашения будут выпущены заново с теми же условиями.
          Аккаунты, рефералы и начисления не меняются. Реферальные ссылки и так меняются каждые{" "}
          {data ? `${data.ttl_minutes} мин` : "…"} («Настройки»).
        </p>
        {confirmRotate ? (
          <div className="grid grid-cols-2 gap-2">
            <Button variant="secondary" className="press h-11 rounded-xl" onClick={() => setConfirmRotate(false)}>Отмена</Button>
            <Button className="press h-11 rounded-xl bg-cancel text-white hover:bg-cancel/90" disabled={!!busy} onClick={rotate}>
              {busy === "rotate" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />} Да, обновить
            </Button>
          </div>
        ) : (
          <Button variant="secondary" className="press h-11 w-full rounded-xl" onClick={() => setConfirmRotate(true)}>
            <RefreshCw /> Обновить все ссылки
          </Button>
        )}
      </section>

      {!data ? <Skeleton className="h-40 rounded-3xl" /> : data.items.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift md:p-6">Приглашений ещё не было.</p>
      ) : (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <ul className="divide-y">
            {data.items.map((inv) => <InviteRow key={inv.id} inv={inv} onChanged={setData} />)}
          </ul>
        </section>
      )}
    </div>
  );
}

function InviteRow({ inv, onChanged }: { inv: Invite; onChanged: (d: { items: Invite[]; ttl_minutes: number }) => void }) {
  const { busy, run } = useAction();
  const [qr, setQr] = useState<string | null>(null);
  const st = STATE[inv.state];
  const live = inv.state === "active";

  async function showQr() {
    try {
      setQr((await api.admin.inviteQr(inv.id)).svg);
    } catch {
      toast.error("Не удалось сделать QR-код");
    }
  }

  async function revoke() {
    try {
      const res = await run("revoke", (k) => api.admin.revokeInvite(inv.id, k), "Ссылка отозвана");
      if (res) onChanged(res);
    } catch {
      /* toast shown */
    }
  }

  return (
    <li className="space-y-2 px-5 py-4 md:px-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold">{inv.label || "Приглашение"}</p>
          <p className="text-xs text-muted-foreground">
            {when(inv.created_at)} · до {when(inv.expires_at)} · {inv.uses}
            {inv.max_uses != null ? ` из ${inv.max_uses}` : ""} чел.
          </p>
        </div>
        <span className={cn("shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold", st.tone)}>{st.label}</span>
      </div>
      {inv.people.length > 0 && (
        <p className="text-sm">
          {inv.people.map((p, i) => (
            <span key={p.telegram_id}>
              {i > 0 && ", "}
              <button className="underline-offset-2 hover:underline" onClick={() => openProfile(null, p.telegram_id)}>
                {p.name ?? `ID ${p.telegram_id}`}
              </button>
              {p.status === "pending" ? " (заявка)" : p.status === "rejected" ? " (отказ)" : p.user_id ? "" : " (не отправил заявку)"}
            </span>
          ))}
        </p>
      )}
      {live && inv.url && (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" className="press h-9 rounded-xl" onClick={() => copy(inv.url!)}><Copy /> Копировать</Button>
          <Button variant="secondary" size="sm" className="press h-9 rounded-xl" onClick={() => (qr ? setQr(null) : showQr())}>
            {qr ? <X /> : <QrCode />} QR-код
          </Button>
          <Button variant="ghost" size="sm" className="press h-9 rounded-xl text-cancel" disabled={!!busy} onClick={revoke}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Ban />} Отозвать
          </Button>
        </div>
      )}
      {qr && live && <QrImage svg={qr} className="max-w-48" />}
    </li>
  );
}

function Attempts() {
  const { data, loading, reload } = useLoad(() => api.admin.accessAttempts(), []);
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <div className="flex items-center justify-between px-5 pt-5 pb-2 md:px-6">
        <h3 className="font-semibold">Попытки доступа</h3>
        <Button variant="ghost" size="sm" className="press h-8 rounded-lg" onClick={reload} aria-label="Обновить">
          {loading ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
        </Button>
      </div>
      {!data ? <Skeleton className="m-5 h-24 rounded-2xl" /> : data.items.length === 0 ? (
        <p className="px-5 pb-5 text-sm text-muted-foreground md:px-6">Посторонних не было.</p>
      ) : (
        <ul className="divide-y">
          {data.items.map((a) => (
            <li key={a.id} className="flex items-start gap-3 px-5 py-3 md:px-6">
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <button className="block max-w-full truncate text-left font-medium underline-offset-2 hover:underline"
                        onClick={() => openProfile(a.username, a.telegram_id)}>
                  {[a.first_name, a.last_name].filter(Boolean).join(" ") || "Без имени"}
                  {a.username ? ` @${a.username}` : ""}
                </button>
                <span className="block text-xs text-muted-foreground">
                  ID {a.telegram_id} · {KIND[a.kind]}{a.param ? ` (${a.param})` : ""}{a.known ? " · уже есть в базе" : ""}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {a.count > 1 ? `${a.count} раз, первый ${when(a.first_at)}, последний ${when(a.last_at)}` : when(a.last_at)}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Chips({ title, items }: { title: string; items: { label: string; on: boolean; pick: () => void }[] }) {
  return (
    <div>
      <p className="mb-2 text-sm text-muted-foreground">{title}</p>
      <div className="flex flex-wrap gap-2">
        {items.map((i) => (
          <button key={i.label} type="button" onClick={i.pick}
                  className={cn("press flex h-9 items-center gap-1 rounded-full px-4 text-sm font-semibold",
                    i.on ? "bg-primary text-primary-foreground" : "bg-muted")}>
            {i.on && <Check className="size-3.5" />} {i.label}
          </button>
        ))}
      </div>
    </div>
  );
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    haptic("success");
    toast.success("Ссылка скопирована");
  } catch {
    toast.message(text);
  }
}
