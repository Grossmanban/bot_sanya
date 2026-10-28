"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, CircleHelp, CircleSlash, LoaderCircle, RefreshCw, Send, UserMinus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type GroupKind, type GroupMember, type GroupRow, type GroupsBoard } from "@/lib/api";
import { ddmm, plural } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { openProfile } from "@/lib/telegram";
import { cn } from "@/lib/utils";

const TITLE: Record<GroupKind, string> = { pre: "Предзакреп", main: "Основная команда" };

/** ТЗ 27.10 §26–27: where everyone should be by confirmed work days, where Telegram says they are, what to do. */
export function GroupsSection() {
  const { data, setData } = useLoad(() => api.admin.groups(), []);
  const { busy, run } = useAction();
  const [onlyIssues, setOnlyIssues] = useState(true);

  async function act(name: string, fn: () => Promise<GroupsBoard>, ok: string) {
    try {
      const res = await run(name, () => fn(), ok);
      if (res) setData(res);
    } catch {
      /* toast shown */
    }
  }

  if (!data) return <Skeleton className="h-64 rounded-3xl" />;
  const shown = data.items.filter((r) => !onlyIssues || r.issues.length || r.action);
  const problems = data.items.filter((r) => r.issues.length).length;

  return (
    <div className="space-y-4">
      <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
        <h2 className="font-display text-xl font-semibold">Группы</h2>
        <p className="mt-1.5 text-[15px] text-hero-muted">
          0–{data.threshold - 1} подтверждённых рабочих дня — «Предзакреп», с {data.threshold}-го — «Основная команда».
          Рабочий день — дата с принятым отчётом (и автоприёмом «команда выполнила план», и учебные смены).
          Приглашения личные: по ссылке войдёт только тот, кому её прислал бот.
        </p>
      </section>

      <section className="grid gap-3 md:grid-cols-2">
        {(["pre", "main"] as const).map((k) => {
          const g = data.groups[k];
          const r = data.rights[k];
          return (
            <div key={k} className="rounded-3xl bg-card p-5 shadow-lift">
              <p className="text-xs text-muted-foreground">{TITLE[k]}</p>
              {g ? (
                <>
                  <p className="font-semibold">{g.title}</p>
                  {r && (
                    <p className={cn("mt-1 text-xs", r.invite && r.remove ? "text-go" : "text-wait")}>
                      {!r.ok ? "Бот не видит группу — добавьте его снова"
                        : !r.admin ? "Бот не администратор — приглашать и сверять нельзя"
                          : r.invite && r.remove ? "Бот — администратор: приглашает и удаляет"
                            : `Не хватает прав: ${[!r.invite && "приглашать", !r.remove && "банить"].filter(Boolean).join(", ")}`}
                    </p>
                  )}
                </>
              ) : (
                <p className="mt-1 text-sm text-muted-foreground">
                  Не подключена. Добавьте бота в группу администратором и отправьте там <code>/group_{k}</code> от владельца.
                </p>
              )}
            </div>
          );
        })}
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="secondary" className="press h-10 rounded-xl" disabled={!!busy || !data.bot}
                onClick={() => act("check", () => api.admin.checkGroups(), "Членство сверено с Telegram")}>
          {busy === "check" ? <LoaderCircle className="animate-spin" /> : <RefreshCw />} Сверить сейчас
        </Button>
        <button onClick={() => setOnlyIssues((v) => !v)}
                className={cn("press h-10 rounded-xl px-4 text-sm font-semibold", onlyIssues ? "bg-primary text-primary-foreground" : "bg-muted")}>
          Только требующие действий
        </button>
        <span className="text-sm text-muted-foreground">
          {problems ? `${problems} ${plural(problems, "проблема", "проблемы", "проблем")}` : "Проблем нет"}
        </span>
      </div>

      {shown.length === 0 ? (
        <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift">Все на своих местах.</p>
      ) : (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <ul className="divide-y">
            {shown.map((r) => <Row key={r.user_id} r={r} busy={busy} act={act} />)}
          </ul>
        </section>
      )}
      <p className="px-1 text-xs text-muted-foreground">
        Членство — то, что ответил Telegram при последней сверке (раз в день и по кнопке). «Не проверено» — Telegram не дал
        ответа, догадок нет. Отправленное приглашение ещё не значит, что человек вступил.
      </p>
    </div>
  );
}

function Row({ r, busy, act }: {
  r: GroupRow; busy: string | null;
  act: (name: string, fn: () => Promise<GroupsBoard>, ok: string) => Promise<void>;
}) {
  const invite = (k: GroupKind) => act(`inv-${r.user_id}-${k}`, () => api.admin.groupInvite(r.user_id, k), `Приглашение в «${TITLE[k]}» отправлено`);
  const remove = (k: GroupKind) => act(`rm-${r.user_id}-${k}`, () => api.admin.groupRemove(r.user_id, k), `Удалён из «${TITLE[k]}»`);
  return (
    <li className="space-y-2 px-5 py-4 md:px-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <button className="block max-w-full truncate text-left font-semibold underline-offset-2 hover:underline"
                  onClick={() => openProfile(r.username, r.telegram_id, r.name)}>{r.name}</button>
          <p className="text-xs text-muted-foreground">
            {r.days} {plural(r.days, "подтверждённый день", "подтверждённых дня", "подтверждённых дней")}
            {r.first_day ? ` · первый ${ddmm(r.first_day)}` : ""} · ожидается «{TITLE[r.expected]}»
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {(["pre", "main"] as const).map((k) => <Membership key={k} kind={k} state={r.member[k]} invite={r.invites[k]} />)}
      </div>
      {r.issues.map((i) => (
        <p key={i} className="flex items-center gap-1.5 text-sm text-wait"><AlertTriangle className="size-4 shrink-0" /> {i}</p>
      ))}
      <div className="flex flex-wrap gap-2">
        {(r.action === "invite_pre" || r.action === "invite_main" || r.action === "move") && (
          <Button size="sm" className="press h-9 rounded-xl" disabled={!!busy} onClick={() => invite(r.action === "invite_pre" ? "pre" : "main")}>
            {busy?.startsWith(`inv-${r.user_id}`) ? <LoaderCircle className="animate-spin" /> : <Send />}
            {r.action === "invite_pre" ? "Пригласить в предзакреп" : "Пригласить в основную команду"}
          </Button>
        )}
        {(r.action === "remove_pre" || r.action === "move") && r.member.pre === "member" && (
          <Button size="sm" variant="secondary" className="press h-9 rounded-xl" disabled={!!busy} onClick={() => remove("pre")}>
            <UserMinus /> Убрать из предзакрепа
          </Button>
        )}
        {r.action === "remove_main" && (
          <Button size="sm" variant="secondary" className="press h-9 rounded-xl" disabled={!!busy} onClick={() => remove("main")}>
            <UserMinus /> Убрать из основной команды
          </Button>
        )}
        {r.action !== "invite_pre" && r.member.pre !== "member" && r.expected === "pre" && r.action !== "check" && (
          <Button size="sm" variant="ghost" className="press h-9 rounded-xl" disabled={!!busy} onClick={() => invite("pre")}>
            <Send /> Ещё раз в предзакреп
          </Button>
        )}
      </div>
    </li>
  );
}

function Membership({ kind, state, invite }: { kind: GroupKind; state: GroupMember; invite: GroupRow["invites"][GroupKind] }) {
  if (state === null) return null;
  const meta = state === "member" ? { icon: CheckCircle2, text: "состоит", tone: "bg-go-soft text-go" }
    : state === "out" ? { icon: CircleSlash, text: "не состоит", tone: "bg-muted text-muted-foreground" }
      : { icon: CircleHelp, text: "не проверено", tone: "bg-muted text-muted-foreground" };
  const Icon = meta.icon;
  const sent = invite && !invite.used_at && !invite.revoked && state !== "member";
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold", meta.tone)}>
      <Icon className="size-3.5" /> {TITLE[kind]}: {meta.text}
      {sent && <span className="font-normal">· приглашение {ddmm(invite.created_at.slice(0, 10))}</span>}
    </span>
  );
}
