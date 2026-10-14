"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { FlaskConical, LoaderCircle, Send } from "lucide-react";
import { toast } from "sonner";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type Broadcast } from "@/lib/api";
import { ddmm, plural } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { RichEditor, type RichEditorHandle } from "./rich-editor";

type Group = "working" | "not_working" | "admins";
const GROUPS: { id: Group; title: string; hint: string }[] = [
  { id: "working", title: "Работают", hint: "статус «Работаю»" },
  { id: "not_working", title: "Не работают", hint: "статус «Не работаю»" },
  { id: "admins", title: "Администраторы", hint: "все админы" },
];
const LIMIT = 4000;

export function BroadcastSection() {
  const { data, reload } = useLoad(() => api.admin.broadcasts(), []);
  const editor = useRef<RichEditorHandle>(null);
  const [groups, setGroups] = useState<Record<Group, boolean>>({ working: true, not_working: false, admins: false });
  const [signed, setSigned] = useState(true);
  const [length, setLength] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const { busy, run } = useAction();

  // the delivery counter fills in while messages go out
  const sending = data?.items.some((b) => b.delivered < b.recipients && Date.now() - Date.parse(b.created_at) < 120_000);
  useEffect(() => {
    if (!sending) return;
    const t = setInterval(reload, 2500);
    return () => clearInterval(t);
  }, [sending, reload]);

  const chosen = (Object.keys(groups) as Group[]).filter((g) => groups[g]);
  const counts = useMemo(() => {
    const people = data?.people ?? [];
    const inGroup = (p: { working: boolean; admin: boolean }, g: Group) =>
      g === "working" ? p.working : g === "not_working" ? !p.working : p.admin;
    return {
      working: people.filter((p) => inGroup(p, "working")).length,
      not_working: people.filter((p) => inGroup(p, "not_working")).length,
      admins: people.filter((p) => inGroup(p, "admins")).length,
      // union: a working admin is one person, not two
      total: people.filter((p) => chosen.some((g) => inGroup(p, g))).length,
    };
  }, [data, chosen]);

  const message = () => {
    const out = editor.current?.getHtml() ?? { html: "", length: 0 };
    if (!out.html.replace(/<[^>]+>/g, "").trim()) {
      toast.error("Напишите текст сообщения");
      return null;
    }
    if (out.length > LIMIT) {
      toast.error(`Сообщение длиннее ${LIMIT} символов`);
      return null;
    }
    return out.html;
  };

  async function test() {
    const html = message();
    if (!html) return;
    try {
      await run("test", () => api.admin.broadcastTest(html, signed), "Отправлено вам в бот: проверьте, как выглядит");
    } catch {
      /* toast shown */
    }
  }

  async function send() {
    const html = message();
    if (!html) return;
    try {
      await run("send", () => api.admin.broadcast(chosen, html, signed), "Рассылка запущена");
      editor.current?.clear();
      await reload();
    } catch {
      /* toast shown */
    }
  }

  if (!data) return <Skeleton className="h-96 rounded-3xl" />;

  return (
    <div className="space-y-4">
      <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div>
          <h2 className="font-semibold">Сообщение от бота</h2>
          <p className="text-sm text-muted-foreground">
            Выделите текст и нажмите кнопку форматирования. Бот отправит сообщение с тем же оформлением.
          </p>
        </div>
        <RichEditor ref={editor} placeholder="Например: завтра на Европе новая выкладка, приходите на 10 минут раньше." onChange={setLength} />
        <p className={cn("-mt-2 text-right text-xs tabular", length > LIMIT ? "text-cancel" : "text-muted-foreground")}>
          {length} / {LIMIT}
        </p>
      </section>

      <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
        <h2 className="px-5 pt-5 pb-1 font-semibold md:px-6">Кому отправить</h2>
        <ul className="divide-y">
          {GROUPS.map((g) => (
            <li key={g.id}>
              <label className="flex cursor-pointer items-center gap-4 px-5 py-3.5 md:px-6">
                <span className="flex-1">
                  <span className="block font-medium">{g.title}</span>
                  <span className="block text-sm text-muted-foreground">
                    {g.hint}, {counts[g.id]} {plural(counts[g.id], "человек", "человека", "человек")}
                  </span>
                </span>
                <Switch checked={groups[g.id]} onCheckedChange={(on) => setGroups({ ...groups, [g.id]: on })} />
              </label>
            </li>
          ))}
          <li>
            <label className="flex cursor-pointer items-center gap-4 px-5 py-3.5 md:px-6">
              <span className="flex-1">
                <span className="block font-medium">Подпись «Сообщение от администратора»</span>
                <span className="block text-sm text-muted-foreground">Заголовок над текстом</span>
              </span>
              <Switch checked={signed} onCheckedChange={setSigned} />
            </label>
          </li>
        </ul>
      </section>

      <div className="grid gap-2.5 sm:grid-cols-[auto_1fr]">
        <Button variant="secondary" className="press h-12 rounded-2xl px-5 text-[15px] font-semibold" disabled={!!busy} onClick={test}>
          {busy === "test" ? <LoaderCircle className="animate-spin" /> : <FlaskConical />} Отправить себе
        </Button>
        <Button
          className="press h-12 rounded-2xl text-[15px] font-semibold"
          disabled={!!busy || counts.total === 0}
          onClick={() => message() && setConfirm(true)}
        >
          {busy === "send" ? <LoaderCircle className="animate-spin" /> : <Send />}
          {counts.total ? `Отправить ${counts.total} ${plural(counts.total, "человеку", "людям", "людям")}` : "Выберите, кому отправить"}
        </Button>
      </div>

      {data.items.length > 0 && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">Отправленные</h2>
          <ul className="divide-y">
            {data.items.map((b) => (
              <HistoryItem key={b.id} b={b} audiences={data.audiences} />
            ))}
          </ul>
        </section>
      )}

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Отправить рассылку?</AlertDialogTitle>
            <AlertDialogDescription>
              Сообщение получат {counts.total} {plural(counts.total, "человек", "человека", "человек")}:{" "}
              {chosen.map((g) => GROUPS.find((x) => x.id === g)!.title.toLowerCase()).join(", ")}. Отменить отправку будет нельзя.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Ещё поправлю</AlertDialogCancel>
            <AlertDialogAction className="rounded-xl" onClick={send}>
              Отправить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function HistoryItem({ b, audiences }: { b: Broadcast; audiences: Record<string, string> }) {
  // html is sanitized by the server to Telegram's tag whitelist; spoilers become spans for display
  const html = b.html?.replace(/<tg-spoiler>/g, '<span class="tg-spoiler">').replace(/<\/tg-spoiler>/g, "</span>");
  const who = b.audience
    .split(",")
    .map((a) => (a === "all" ? "все сотрудники" : (audiences[a] ?? a).toLowerCase()))
    .join(", ");
  return (
    <li className="px-5 py-3.5 md:px-6">
      {html ? (
        <div className="tg-rich line-clamp-4 text-[15px] leading-relaxed whitespace-pre-wrap" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <p className="line-clamp-4 text-[15px] whitespace-pre-wrap">{b.text}</p>
      )}
      <p className="mt-1.5 text-xs text-muted-foreground">
        {who}, доставлено {b.delivered} из {b.recipients}
        {b.first_name ? `, ${b.first_name}` : ""}, {ddmm(b.created_at.slice(0, 10))} в {b.created_at.slice(11, 16)}
      </p>
    </li>
  );
}
