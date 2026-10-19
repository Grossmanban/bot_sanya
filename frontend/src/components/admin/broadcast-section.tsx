"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BellRing, CheckCheck, ChevronDown, FlaskConical, LoaderCircle, Send, X } from "lucide-react";
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
import { api, type Broadcast, type BroadcastPerson, type User } from "@/lib/api";
import { ddmm, plural } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { RichEditor, type RichEditorHandle } from "./rich-editor";
import { PhotoPicker } from "../report/photo";

type Group = "working" | "not_working" | "admins";
const GROUPS: { id: Group; title: string; hint: string }[] = [
  { id: "working", title: "Работают", hint: "статус «Работаю»" },
  { id: "not_working", title: "Не работают", hint: "статус «Не работаю»" },
  { id: "admins", title: "Администраторы", hint: "все админы" },
];
const LIMIT = 4000;

export function BroadcastSection({ me }: { me: User }) {
  const { data, reload } = useLoad(() => api.admin.broadcasts(), []);
  const editor = useRef<RichEditorHandle>(null);
  const [groups, setGroups] = useState<Record<Group, boolean>>({ working: true, not_working: false, admins: false });
  const [network, setNetwork] = useState<number | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [mediaId, setMediaId] = useState<{ blob: Blob; id: string } | null>(null);
  const [ack, setAck] = useState(false);
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

  const scopeAll = data?.scope !== "network";
  // a trainer writes only to their network (ТЗ 7.1)
  const ownNetwork = !scopeAll ? (data?.networks[0]?.id ?? null) : null;
  const chosen = useMemo(() => (scopeAll ? (Object.keys(groups) as Group[]).filter((g) => groups[g]) : []), [scopeAll, groups]);
  const targetNetwork = scopeAll ? network : ownNetwork;
  const counts = useMemo(() => {
    const people = (data?.people ?? []).filter((p) => scopeAll || p.id !== me.id);
    const inGroup = (p: BroadcastPerson, g: Group) => (g === "working" ? p.working : g === "not_working" ? !p.working : p.admin);
    return {
      working: people.filter((p) => inGroup(p, "working")).length,
      not_working: people.filter((p) => inGroup(p, "not_working")).length,
      admins: people.filter((p) => inGroup(p, "admins")).length,
      // union: a working admin who is also picked by name is one person
      total: people.filter((p) => chosen.some((g) => inGroup(p, g)) || (targetNetwork != null && p.network_id === targetNetwork)
        || picked.includes(p.id)).length,
    };
  }, [data, chosen, targetNetwork, picked, scopeAll, me.id]);

  async function media(): Promise<string | null> {
    if (!photo) return null;
    if (mediaId?.blob === photo) return mediaId.id;
    const { file_id } = await api.admin.uploadMedia(photo);
    setMediaId({ blob: photo, id: file_id });
    return file_id;
  }

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
      await run("test", async () => api.admin.broadcastTest(html, signed, ack, await media()), "Отправлено вам в бот: проверьте, как выглядит");
    } catch {
      /* toast shown */
    }
  }

  async function send() {
    const html = message();
    if (!html) return;
    try {
      await run("send", async () => api.admin.broadcast({
        audiences: chosen, html, signed, network_id: targetNetwork, user_ids: picked.length ? picked : null,
        require_ack: ack, media_file_id: await media(),
      }), "Рассылка запущена");
      editor.current?.clear();
      setPhoto(null);
      setPicked([]);
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
        <PhotoPicker value={photo} onChange={setPhoto} label="Добавить фото" optional />
      </section>

      <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
        <h2 className="px-5 pt-5 pb-1 font-semibold md:px-6">Кому отправить</h2>
        <ul className="divide-y">
          {!scopeAll && (
            <li className="px-5 py-3.5 md:px-6">
              <span className="block font-medium">Ваша сеть{data.networks[0] ? ` «${data.networks[0].name}»` : ""}</span>
              <span className="block text-sm text-muted-foreground">Тренер пишет только своей сети</span>
            </li>
          )}
          {scopeAll && GROUPS.map((g) => (
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
          {scopeAll && data.networks.length > 0 && (
            <li className="flex items-center gap-4 px-5 py-3.5 md:px-6">
              <span className="flex-1">
                <span className="block font-medium">Сеть</span>
                <span className="block text-sm text-muted-foreground">все люди сети вместе с тренером</span>
              </span>
              <select className="h-10 rounded-xl border-0 bg-muted px-3 text-sm outline-none" value={network ?? ""}
                      onChange={(e) => setNetwork(e.target.value ? Number(e.target.value) : null)}>
                <option value="">не выбрана</option>
                {data.networks.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
              </select>
            </li>
          )}
          <li className="px-5 py-3.5 md:px-6">
            <PeoplePicker people={data.people.filter((p) => scopeAll || p.id !== me.id)} value={picked} onChange={setPicked} />
          </li>
          <li>
            <label className="flex cursor-pointer items-center gap-4 px-5 py-3.5 md:px-6">
              <span className="flex-1">
                <span className="block font-medium">Требовать подтверждения прочтения</span>
                <span className="block text-sm text-muted-foreground">Кнопка «✅ Я ознакомился» и список, кто не прочитал</span>
              </span>
              <Switch checked={ack} onCheckedChange={setAck} />
            </label>
          </li>
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
              <HistoryItem key={b.id} b={b} audiences={data.audiences} networks={data.networks} />
            ))}
          </ul>
        </section>
      )}

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Отправить рассылку?</AlertDialogTitle>
            <AlertDialogDescription>
              Сообщение получат {counts.total} {plural(counts.total, "человек", "человека", "человек")}
              {ack ? ", с кнопкой «Я ознакомился»" : ""}. Отменить отправку будет нельзя.
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

function PeoplePicker({ people, value, onChange }: { people: BroadcastPerson[]; value: number[]; onChange: (v: number[]) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const list = people.filter((p) => p.name.toLowerCase().includes(q.toLowerCase())).slice(0, 40);
  return (
    <div>
      <button className="flex w-full items-center gap-4 text-left" onClick={() => setOpen((v) => !v)}>
        <span className="flex-1">
          <span className="block font-medium">Конкретные люди</span>
          <span className="block text-sm text-muted-foreground">{value.length ? `выбрано: ${value.length}` : "добавить к выбранным группам"}</span>
        </span>
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {value.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {value.map((id) => {
            const p = people.find((x) => x.id === id);
            return (
              <span key={id} className="flex items-center gap-1 rounded-full bg-muted py-1 pr-1 pl-3 text-sm">
                {p?.name ?? id}
                <button aria-label="Убрать" className="grid size-6 place-items-center rounded-full hover:bg-black/5" onClick={() => onChange(value.filter((x) => x !== id))}>
                  <X className="size-3.5" />
                </button>
              </span>
            );
          })}
        </div>
      )}
      {open && (
        <div className="row-in mt-3 space-y-2">
          <input className="h-10 w-full rounded-xl border-0 bg-muted px-3 outline-none focus:ring-2 focus:ring-ring/25" placeholder="Поиск"
                 value={q} onChange={(e) => setQ(e.target.value)} />
          <ul className="max-h-56 overflow-y-auto">
            {list.map((p) => {
              const on = value.includes(p.id);
              return (
                <li key={p.id}>
                  <button onClick={() => onChange(on ? value.filter((x) => x !== p.id) : [...value, p.id])}
                          className="press flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-muted">
                    <span className={cn("grid size-5 place-items-center rounded-md border-2", on ? "border-primary bg-primary text-primary-foreground" : "border-border")}>
                      {on && <CheckCheck className="size-3" />}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{p.name}</span>
                    {p.network && <span className="text-xs text-muted-foreground">{p.network}</span>}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

function HistoryItem({ b, audiences, networks }: { b: Broadcast; audiences: Record<string, string>; networks: { id: number; name: string }[] }) {
  // html is sanitized by the server to Telegram's tag whitelist; spoilers become spans for display
  const html = b.html?.replace(/<tg-spoiler>/g, '<span class="tg-spoiler">').replace(/<\/tg-spoiler>/g, "</span>");
  const [open, setOpen] = useState(false);
  const who = [
    ...b.audience.split(",").filter(Boolean).map((a) => (a === "all" ? "все сотрудники" : (audiences[a] ?? a).toLowerCase())),
    ...(b.network_id ? [`сеть «${networks.find((n) => n.id === b.network_id)?.name ?? b.network_id}»`] : []),
  ].join(", ") || "выбранные люди";
  return (
    <li className="px-5 py-3.5 md:px-6">
      {html ? (
        <div className="tg-rich line-clamp-4 text-[15px] leading-relaxed whitespace-pre-wrap" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <p className="line-clamp-4 text-[15px] whitespace-pre-wrap">{b.text}</p>
      )}
      <p className="mt-1.5 text-xs text-muted-foreground">
        {b.media_file_id ? "📷 " : ""}{who}, доставлено {b.delivered} из {b.recipients}
        {b.first_name ? `, ${b.first_name}` : ""}, {ddmm(b.created_at.slice(0, 10))} в {b.created_at.slice(11, 16)}
      </p>
      {!!b.require_ack && (
        <button onClick={() => setOpen((v) => !v)}
                className={cn("press mt-2 inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-xs font-semibold",
                  b.read >= b.delivered && b.delivered > 0 ? "bg-go-soft text-go" : "bg-wait-soft text-wait")}>
          <CheckCheck className="size-3.5" /> прочитали {b.read} из {b.delivered}
          <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
        </button>
      )}
      {open && <Receipts id={b.id} />}
    </li>
  );
}

/** Who hasn't confirmed, with «Напомнить» per person and for everyone (ТЗ 7.3). */
function Receipts({ id }: { id: number }) {
  const { data, reload } = useLoad(() => api.admin.receipts(id), [id]);
  const { busy, run } = useAction();
  if (!data) return <Skeleton className="mt-3 h-20 rounded-2xl" />;
  const unread = data.items.filter((r) => !r.read_at);
  const read = data.items.filter((r) => r.read_at);
  const remind = async (ids: number[] | null, name: string) => {
    try {
      const res = await run(name, () => api.admin.remind(id, ids));
      if (res) toast.success(`Напоминание отправлено: ${res.sent}`);
      await reload();
    } catch {
      /* toast shown */
    }
  };
  return (
    <div className="row-in mt-3 space-y-3 rounded-2xl bg-muted/50 p-3">
      {unread.length > 0 ? (
        <>
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-semibold">Не прочитали: {unread.length}</p>
            <Button size="sm" className="press h-8 rounded-lg" disabled={!!busy} onClick={() => remind(null, "all")}>
              {busy === "all" ? <LoaderCircle className="animate-spin" /> : <BellRing />} Напомнить всем
            </Button>
          </div>
          <ul className="space-y-1">
            {unread.map((r) => (
              <li key={r.user_id} className="flex items-center justify-between gap-2 text-sm">
                <span className="min-w-0 truncate">
                  {r.name}
                  {!r.delivered && <span className="text-cancel"> · не доставлено</span>}
                  {r.reminders > 0 && <span className="text-muted-foreground"> · напоминали {r.reminders}×</span>}
                </span>
                <Button size="sm" variant="ghost" className="h-8 shrink-0 rounded-lg" disabled={!!busy} onClick={() => remind([r.user_id], `one-${r.user_id}`)}>
                  {busy === `one-${r.user_id}` ? <LoaderCircle className="animate-spin" /> : <BellRing />} Напомнить
                </Button>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="text-sm font-semibold text-go">Все прочитали</p>
      )}
      {read.length > 0 && (
        <p className="text-xs text-muted-foreground">Прочитали: {read.map((r) => r.name).join(", ")}</p>
      )}
    </div>
  );
}
