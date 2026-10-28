"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, CircleHelp, CircleSlash, LoaderCircle, RefreshCw, Send, UserMinus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, newKey, type GroupAction, type GroupKind, type GroupMember, type GroupRow, type GroupsBoard } from "@/lib/api";
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
  const shown = data.items.filter((r) => !onlyIssues || r.issues.length);
  const problems = data.items.filter((r) => r.issues.length).length;

  return (
    <div className="space-y-4">
      <section className="rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6">
        <h2 className="font-display text-xl font-semibold">Группы</h2>
        <p className="mt-1.5 text-[15px] text-hero-muted">
          Кандидат получает «Предзакреп», когда обучение согласовано, «Основную команду» — после «Принять в команду».
          Пришедшие без анкеты — сразу в «Основную команду». Каждому можно задать свои группы: только одна или обе.
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

const WHO: Record<GroupRow["who"], string> = { employee: "сотрудник", candidate: "кандидат", trainee: "стажёр", blocked: "заблокирован" };
const MODES: { value: GroupRow["mode"]; label: string }[] = [
  { value: null, label: "Авто" }, { value: "pre", label: "Предзакреп" }, { value: "main", label: "Основная" }, { value: "both", label: "Обе" },
];

function Row({ r, busy, act }: {
  r: GroupRow; busy: string | null;
  act: (name: string, fn: () => Promise<GroupsBoard>, ok: string) => Promise<void>;
}) {
  const [settings, setSettings] = useState(false);
  const run = (a: GroupAction) => {
    if (a === "invite_pre" || a === "invite_main") {
      const k: GroupKind = a === "invite_pre" ? "pre" : "main";
      return act(`inv-${r.user_id}-${k}`, () => api.admin.groupInvite(r.user_id, k), `Приглашение в «${TITLE[k]}» отправлено`);
    }
    if (a === "remove_pre" || a === "remove_main") {
      const k: GroupKind = a === "remove_pre" ? "pre" : "main";
      return act(`rm-${r.user_id}-${k}`, () => api.admin.groupRemove(r.user_id, k), `Удалён из «${TITLE[k]}»`);
    }
    if (a === "allow_both") return act(`mode-${r.user_id}`, () => api.admin.groupMode(r.user_id, "both", newKey()), "Можно в обеих группах");
    if (a === "not_newbie") return act(`nn-${r.user_id}`, () => api.admin.notNewbie(r.user_id, true, newKey()), "Отмечен: не новичок");
    return act("check", () => api.admin.checkGroups(), "Членство сверено с Telegram");
  };
  const LABEL: Record<GroupAction, string> = {
    invite_pre: "Пригласить в «Предзакреп»", invite_main: "Пригласить в «Основную»", remove_pre: "Убрать из «Предзакрепа»",
    remove_main: "Убрать из «Основной»", allow_both: "Можно в обеих", not_newbie: "Не новичок — подтвердить", check: "Сверить",
  };
  return (
    <li className="space-y-2 px-5 py-4 md:px-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <button className="block max-w-full truncate text-left font-semibold underline-offset-2 hover:underline"
                  onClick={() => openProfile(r.username, r.telegram_id, r.name)}>{r.name}</button>
          <p className="text-xs text-muted-foreground">
            {WHO[r.who]} · {r.days} {plural(r.days, "подтв. день", "подтв. дня", "подтв. дней")}
            {r.first_day ? ` · первый ${ddmm(r.first_day)}` : ""} · нужно: {r.required.length ? r.required.map((k) => `«${TITLE[k]}»`).join(" и ") : "ни одной"}
            {r.mode ? ` (${r.mode_title.toLowerCase()})` : ""}{r.not_newbie ? " · не новичок" : ""}
          </p>
        </div>
        <button className="shrink-0 text-xs font-semibold text-muted-foreground underline-offset-2 hover:underline" onClick={() => setSettings((v) => !v)}>
          Группы человека
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {(["pre", "main"] as const).map((k) => <Membership key={k} kind={k} state={r.member[k]} invite={r.invites[k]} need={r.required.includes(k)} />)}
      </div>
      {r.problems.length === 0 ? (
        <p className="flex items-center gap-1.5 text-sm text-go"><CheckCircle2 className="size-4 shrink-0" /> Все нужные группы на месте</p>
      ) : r.problems.map((p) => (
        <div key={p.text} className="space-y-1.5">
          <p className="flex items-center gap-1.5 text-sm text-wait"><AlertTriangle className="size-4 shrink-0" /> {p.text}</p>
          <div className="flex flex-wrap gap-2">
            {p.actions.map((a, i) => (
              <Button key={a} size="sm" variant={i === 0 ? "default" : "secondary"} className="press h-9 rounded-xl" disabled={!!busy}
                      onClick={() => run(a)}>
                {busy?.includes(String(r.user_id)) && i === 0 ? <LoaderCircle className="animate-spin" />
                  : a.startsWith("invite") ? <Send /> : a.startsWith("remove") ? <UserMinus /> : null}
                {LABEL[a]}
              </Button>
            ))}
          </div>
        </div>
      ))}
      {settings && (
        <div className="row-in space-y-2 rounded-2xl bg-muted/50 p-3">
          <p className="text-xs text-muted-foreground">В каких группах должен быть человек</p>
          <div className="flex flex-wrap gap-1.5">
            {MODES.map((m) => (
              <button key={m.label} type="button" disabled={!!busy}
                      onClick={() => act(`mode-${r.user_id}`, () => api.admin.groupMode(r.user_id, m.value, newKey()), `Группы: ${m.label}`)}
                      className={cn("press h-9 rounded-full px-3.5 text-sm font-medium", r.mode === m.value ? "bg-primary text-primary-foreground" : "bg-card")}>
                {m.label}
              </button>
            ))}
          </div>
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>Не новичок — подтвердить <span className="block text-xs text-muted-foreground">Считать опытным: нужна «Основная команда». Роли не меняются.</span></span>
            <input type="checkbox" className="size-5" checked={r.not_newbie} disabled={!!busy}
                   onChange={(e) => act(`nn-${r.user_id}`, () => api.admin.notNewbie(r.user_id, e.target.checked, newKey()),
                     e.target.checked ? "Отмечен: не новичок" : "Отметка снята")} />
          </label>
        </div>
      )}
    </li>
  );
}

function Membership({ kind, state, invite, need }: { kind: GroupKind; state: GroupMember; invite: GroupRow["invites"][GroupKind]; need: boolean }) {
  if (state === null) return null;
  const meta = state === "member" ? { icon: CheckCircle2, text: "состоит", tone: "bg-go-soft text-go" }
    : state === "out" ? { icon: CircleSlash, text: "не состоит", tone: "bg-muted text-muted-foreground" }
      : { icon: CircleHelp, text: "не проверено", tone: "bg-muted text-muted-foreground" };
  const Icon = meta.icon;
  const sent = invite && !invite.used_at && !invite.revoked && state !== "member";
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold", meta.tone)}>
      <Icon className="size-3.5" /> {TITLE[kind]}: {meta.text}{need ? "" : state === "member" ? " (не обязательно)" : ""}
      {sent && <span className="font-normal">· приглашение {ddmm(invite.created_at.slice(0, 10))}</span>}
    </span>
  );
}
