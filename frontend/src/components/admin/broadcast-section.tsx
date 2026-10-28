"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BellRing, Check, CheckCheck, ChevronDown, ChevronRight, Film, FlaskConical, ImageIcon, LoaderCircle, MessageSquareReply, Pencil, Plus, Search, Send, Trash2 } from "lucide-react";
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
import { Drawer, DrawerContent, DrawerTitle } from "@/components/ui/drawer";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type Broadcast, type BroadcastPerson, type BroadcastTemplate, type User } from "@/lib/api";
import { ddmm, plural } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { openProfile, tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { RichEditor, type RichEditorHandle } from "./rich-editor";
import { PhotoPicker, VideoPicker } from "../report/photo";

const LIMIT = 4000;

export function BroadcastSection({ me }: { me: User }) {
  const { data, reload } = useLoad(() => api.admin.broadcasts(), []);
  const editor = useRef<RichEditorHandle>(null);
  // exactly these people get the message (a checklist); groups and networks only tick boxes
  const [picked, setPicked] = useState<number[]>([]);
  const [unmarked, setUnmarked] = useState(false);
  // one attachment: a photo or a video
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [video, setVideo] = useState<File | null>(null);
  const [mediaId, setMediaId] = useState<{ blob: Blob; id: string } | null>(null);
  const [ack, setAck] = useState(false);
  const [reply, setReply] = useState(false);
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
  const people = useMemo(() => (data?.people ?? []).filter((p) => scopeAll || p.id !== me.id), [data, scopeAll, me.id]);
  const recipients = useMemo(
    () => people.filter((p) => picked.includes(p.id) && (!unmarked || !p.marked_tomorrow)),
    [people, picked, unmarked],
  );

  const kind: "photo" | "video" = video ? "video" : "photo";
  async function media(): Promise<string | null> {
    const file = video ?? photo;
    if (!file) return null;
    if (mediaId?.blob === file) return mediaId.id;
    const { file_id } = video ? await api.admin.uploadVideo(video) : await api.admin.uploadMedia(file);
    setMediaId({ blob: file, id: file_id });
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
      await run("test", async () => api.admin.broadcastTest({ html, signed, require_ack: ack, media_file_id: await media(),
        media_kind: kind, allow_reply: reply }), "Отправлено вам в бот: проверьте, как выглядит");
    } catch {
      /* toast shown */
    }
  }

  async function send() {
    const html = message();
    if (!html) return;
    try {
      await run("send", async () => api.admin.broadcast({
        audiences: [], html, signed, network_id: null, user_ids: picked, require_ack: ack, media_file_id: await media(),
        only_unmarked_tomorrow: unmarked, media_kind: kind, allow_reply: reply,
      }), "Рассылка запущена");
      editor.current?.clear();
      setPhoto(null);
      setVideo(null);
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
        <Templates editor={editor} />
        <RichEditor ref={editor} placeholder="Например: завтра на Европе новая выкладка, приходите на 10 минут раньше." onChange={setLength} />
        <p className={cn("-mt-2 text-right text-xs tabular", length > LIMIT ? "text-cancel" : "text-muted-foreground")}>
          {length} / {LIMIT}
        </p>
        {!video && <PhotoPicker value={photo} onChange={setPhoto} label="Добавить фото" optional />}
        {!photo && <VideoPicker value={video} onChange={setVideo} />}
      </section>

      <Recipients people={people} networks={scopeAll ? data.networks : []} trainerNetwork={scopeAll ? null : data.networks[0]?.name ?? null}
                  value={picked} onChange={setPicked} unmarked={unmarked} onUnmarked={setUnmarked} willGet={recipients.length} />

      <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
        <ul className="divide-y">
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
                <span className="block font-medium">Возможность ответить</span>
                <span className="block text-sm text-muted-foreground">
                  Кнопка «✍️ Ответить»: текст, фото и видео придут вам в бот, а здесь видно, кто ответил
                </span>
              </span>
              <Switch checked={reply} onCheckedChange={setReply} />
            </label>
          </li>
          <li>
            {data.signer.optional ? (
              <label className="flex cursor-pointer items-center gap-4 px-5 py-3.5 md:px-6">
                <span className="flex-1">
                  <span className="block font-medium">Подпись «Сообщение от администратора»</span>
                  <span className="block text-sm text-muted-foreground">Заголовок над текстом</span>
                </span>
                <Switch checked={signed} onCheckedChange={setSigned} />
              </label>
            ) : (
              <div className="px-5 py-3.5 md:px-6">
                <span className="block font-medium">Подпись</span>
                <span className="block text-sm break-words whitespace-pre-line text-muted-foreground">{data.signer.text}</span>
              </div>
            )}
          </li>
        </ul>
      </section>

      <div className="grid gap-2.5 sm:grid-cols-[auto_1fr]">
        <Button variant="secondary" className="press h-12 rounded-2xl px-5 text-[15px] font-semibold" disabled={!!busy} onClick={test}>
          {busy === "test" ? <LoaderCircle className="animate-spin" /> : <FlaskConical />} Отправить себе
        </Button>
        <Button
          className="press h-12 rounded-2xl text-[15px] font-semibold"
          disabled={!!busy || recipients.length === 0}
          onClick={() => message() && setConfirm(true)}
        >
          {busy === "send" ? <LoaderCircle className="animate-spin" /> : <Send />}
          {recipients.length ? `Отправить ${recipients.length} ${plural(recipients.length, "человеку", "людям", "людям")}` : "Выберите, кому отправить"}
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
              Сообщение получат {recipients.length} {plural(recipients.length, "человек", "человека", "человек")}
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

/** ТЗ: a checkbox next to every person, «Выбрать всех / Снять выбор», «Выбрано: N». Group and network buttons only
 *  tick or untick their people; what is ticked is exactly who gets the message. */
function Recipients({ people, networks, trainerNetwork, value, onChange, unmarked, onUnmarked, willGet }: {
  people: BroadcastPerson[];
  networks: { id: number; name: string }[];
  trainerNetwork: string | null;
  value: number[];
  onChange: (v: number[]) => void;
  unmarked: boolean;
  onUnmarked: (v: boolean) => void;
  willGet: number;
}) {
  const [q, setQ] = useState("");
  const chosen = new Set(value);
  const shown = people.filter((p) => p.name.toLowerCase().includes(q.trim().toLowerCase()));
  const groups: { key: string; label: string; ids: number[] }[] = [
    { key: "all", label: trainerNetwork ? `Вся сеть «${trainerNetwork}»` : "Все", ids: people.map((p) => p.id) },
    { key: "working", label: "Работают", ids: people.filter((p) => p.working).map((p) => p.id) },
    { key: "not_working", label: "Не работают", ids: people.filter((p) => !p.working).map((p) => p.id) },
    ...(trainerNetwork ? [] : [{ key: "admins", label: "Админы", ids: people.filter((p) => p.admin).map((p) => p.id) }]),
    ...networks.map((n) => ({ key: `n${n.id}`, label: `Сеть «${n.name}»`, ids: people.filter((p) => p.network_id === n.id).map((p) => p.id) })),
  ].filter((g) => g.ids.length > 0);
  const full = (ids: number[]) => ids.length > 0 && ids.every((id) => chosen.has(id));
  const toggle = (ids: number[]) => {
    tapHaptic();
    onChange(full(ids) ? value.filter((id) => !ids.includes(id)) : [...new Set([...value, ...ids])]);
  };
  const unmarkedCount = people.filter((p) => !p.marked_tomorrow).length;
  const picked = value.length;

  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <div className="space-y-3 px-5 pt-5 md:px-6">
        <h2 className="font-semibold">Кому отправить</h2>
        <div className="flex flex-wrap gap-1.5">
          {groups.map((g) => (
            <button key={g.key} type="button" onClick={() => toggle(g.ids)}
                    className={cn("press h-9 rounded-full px-3.5 text-sm font-medium",
                      full(g.ids) ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
              {g.label} <span className="tabular opacity-70">{g.ids.length}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <label className="relative min-w-0 flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <input className="h-10 w-full rounded-xl border-0 bg-muted pr-3 pl-9 text-sm outline-none focus:ring-2 focus:ring-ring/25"
                   placeholder="Поиск по имени" value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
          <Button variant="ghost" size="sm" className="h-10 shrink-0 rounded-xl" onClick={() => toggle(shown.map((p) => p.id))}>
            {full(shown.map((p) => p.id)) ? "Снять выбор" : "Выбрать всех"}
          </Button>
        </div>
      </div>
      <ul className="mt-2 max-h-80 overflow-y-auto px-3 md:px-4" aria-label="Сотрудники">
        {shown.map((p) => {
          const on = chosen.has(p.id);
          return (
            <li key={p.id}>
              <label className="flex cursor-pointer items-center gap-3 rounded-xl px-2 py-2.5 hover:bg-muted/60">
                <input type="checkbox" className="peer sr-only" checked={on}
                       onChange={() => onChange(on ? value.filter((x) => x !== p.id) : [...value, p.id])} />
                <span aria-hidden className={cn("grid size-5 shrink-0 place-items-center rounded-md border-2 peer-focus-visible:ring-2 peer-focus-visible:ring-ring",
                  on ? "border-primary bg-primary text-primary-foreground" : "border-border")}>
                  {on && <Check className="size-3" strokeWidth={3} />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{p.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {[p.working ? "работает" : "не работает", p.network, p.marked_tomorrow ? null : "не отметил завтра"].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </label>
            </li>
          );
        })}
        {shown.length === 0 && <li className="px-2 py-4 text-sm text-muted-foreground">Никого не нашли</li>}
      </ul>
      <label className="flex cursor-pointer items-center gap-4 border-t px-5 py-3.5 md:px-6">
        <span className="min-w-0 flex-1">
          <span className="block font-medium">Только тем, кто не отметил график на завтра</span>
          <span className="block text-sm text-muted-foreground">
            Нет ни смены, ни выходного на завтра — сейчас {unmarkedCount} {plural(unmarkedCount, "человек", "человека", "человек")}
          </span>
        </span>
        <Switch checked={unmarked} onCheckedChange={onUnmarked} />
      </label>
      <p className="border-t bg-muted/40 px-5 py-3 text-sm md:px-6" aria-live="polite">
        Выбрано: <b className="tabular">{picked}</b>
        {unmarked && picked > 0 && <> · получат <b className="tabular">{willGet}</b>: остальные уже отметили завтра</>}
      </p>
    </section>
  );
}

function HistoryItem({ b, audiences, networks }: { b: Broadcast; audiences: Record<string, string>; networks: { id: number; name: string }[] }) {
  // html is sanitized by the server to Telegram's tag whitelist; spoilers become spans for display
  const html = b.html?.replace(/<tg-spoiler>/g, '<span class="tg-spoiler">').replace(/<\/tg-spoiler>/g, "</span>");
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(false);
  const who = [
    ...b.audience.split(",").filter(Boolean).map((a) => (a === "all" ? "все сотрудники" : (audiences[a] ?? a).toLowerCase())),
    ...(b.network_id ? [`сеть «${networks.find((n) => n.id === b.network_id)?.name ?? b.network_id}»`] : []),
  ].join(", ") || "выбранные люди";
  return (
    <li className="px-5 py-3.5 md:px-6">
      {/* the whole message — text, photo, video — opens on tap */}
      <button type="button" className="press group block w-full text-left" onClick={() => { tapHaptic(); setFull(true); }}>
        <span className="flex gap-3">
          {b.media_url && (
            <span className="grid size-14 shrink-0 place-items-center overflow-hidden rounded-xl bg-muted text-muted-foreground">
              {b.media_kind === "video" ? <Film className="size-5" /> : (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={b.media_url} alt="" className="size-full object-cover" onError={(e) => { e.currentTarget.style.display = "none"; }} />
              )}
            </span>
          )}
          {html ? (
            <span className="tg-rich line-clamp-4 min-w-0 flex-1 text-[15px] leading-relaxed whitespace-pre-wrap" dangerouslySetInnerHTML={{ __html: html }} />
          ) : (
            <span className="line-clamp-4 min-w-0 flex-1 text-[15px] whitespace-pre-wrap">{b.text}</span>
          )}
        </span>
        <span className="mt-1 inline-flex items-center gap-0.5 text-xs font-semibold text-primary">
          Открыть целиком <ChevronRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
        </span>
      </button>
      <p className="mt-1.5 text-xs text-muted-foreground">
        {b.media_file_id ? (b.media_kind === "video" ? "🎬 " : "📷 ") : ""}{who}, доставлено {b.delivered} из {b.recipients}
        {b.first_name ? `, ${b.first_name}` : ""}, {ddmm(b.created_at.slice(0, 10))} в {b.created_at.slice(11, 16)}
      </p>
      <SentMessage b={b} html={html} meta={`${who}, ${ddmm(b.created_at.slice(0, 10))} в ${b.created_at.slice(11, 16)}`}
                   open={full} onOpenChange={setFull} />
      {(!!b.require_ack || !!b.allow_reply) && (
        <button onClick={() => setOpen((v) => !v)} className="press mt-2 inline-flex flex-wrap items-center gap-1.5 text-xs font-semibold">
          {!!b.require_ack && (
            <span className={cn("inline-flex h-8 items-center gap-1.5 rounded-full px-3",
              b.read >= b.delivered && b.delivered > 0 ? "bg-go-soft text-go" : "bg-wait-soft text-wait")}>
              <CheckCheck className="size-3.5" /> прочитали {b.read} из {b.delivered}
            </span>
          )}
          {!!b.allow_reply && (
            <span className={cn("inline-flex h-8 items-center gap-1.5 rounded-full px-3",
              (b.replied ?? 0) > 0 ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground")}>
              <MessageSquareReply className="size-3.5" /> ответили {b.replied ?? 0} из {b.delivered}
            </span>
          )}
          <ChevronDown className={cn("size-3.5 text-muted-foreground transition-transform", open && "rotate-180")} />
        </button>
      )}
      {open && <Receipts id={b.id} ack={!!b.require_ack} replies={!!b.allow_reply} />}
    </li>
  );
}

/** A sent broadcast in full: the header, the photo or video and all the text — as people got it in the bot. */
function SentMessage({ b, html, meta, open, onOpenChange }: {
  b: Broadcast; html: string | undefined; meta: string; open: boolean; onOpenChange: (v: boolean) => void;
}) {
  const [broken, setBroken] = useState(false);
  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent className="mx-auto max-h-[90dvh] max-w-2xl rounded-t-[1.75rem] border-0 bg-card">
        <div className="pb-safe space-y-3 overflow-y-auto px-5 pt-2 pb-4">
          <DrawerTitle className="font-display text-xl font-semibold">Отправленное сообщение</DrawerTitle>
          <p className="text-xs text-muted-foreground">{meta}</p>
          {b.media_url && (broken ? (
            <p className="flex items-center gap-2 rounded-2xl bg-muted px-4 py-3 text-sm text-muted-foreground">
              {b.media_kind === "video" ? <Film className="size-4 shrink-0" /> : <ImageIcon className="size-4 shrink-0" />}
              {b.media_kind === "video" ? "Видео не открылось в приложении — оно есть в чате с ботом." : "Фото не открылось — оно есть в чате с ботом."}
            </p>
          ) : b.media_kind === "video" ? (
            <video src={b.media_url} controls playsInline preload="metadata" onError={() => setBroken(true)}
                   className="max-h-[60dvh] w-full rounded-2xl bg-black" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={b.media_url} alt="Фото рассылки" onError={() => setBroken(true)} className="max-h-[60dvh] w-full rounded-2xl bg-muted object-contain" />
          ))}
          <div className="rounded-2xl bg-muted/50 p-4">
            {b.signature_text && <p className="mb-3 font-semibold break-words whitespace-pre-line">{b.signature_text}</p>}
            {html ? (
              <div className="tg-rich text-[15px] leading-relaxed break-words whitespace-pre-wrap" dangerouslySetInnerHTML={{ __html: html }} />
            ) : (
              <p className="text-[15px] break-words whitespace-pre-wrap">{b.text}</p>
            )}
          </div>
          <Button variant="secondary" className="press h-12 w-full rounded-2xl" onClick={() => onOpenChange(false)}>Закрыть</Button>
        </div>
      </DrawerContent>
    </Drawer>
  );
}

/** Who hasn't confirmed, with «Напомнить» per person and for everyone (ТЗ 7.3). */
function Receipts({ id, ack, replies }: { id: number; ack: boolean; replies: boolean }) {
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
  const answered = data.items.filter((r) => r.replies.length > 0);
  const silent = data.items.filter((r) => r.replies.length === 0);
  return (
    <div className="row-in mt-3 space-y-3 rounded-2xl bg-muted/50 p-3">
      {replies && (
        <div className="space-y-2">
          <p className="text-sm font-semibold">Ответили: {answered.length}</p>
          {answered.map((r) => (
            <div key={r.user_id} className="rounded-xl bg-card p-3 text-sm">
              <p className="font-medium">
                <a href={r.username ? `https://t.me/${r.username}` : "#"} className="underline-offset-2 hover:underline"
                   onClick={(e) => { e.preventDefault(); openProfile(r.username, r.telegram_id, r.name); }}>
                  {r.name}
                </a>
                {r.username && <span className="font-normal text-muted-foreground"> @{r.username}</span>}
              </p>
              {r.replies.map((x, i) => (
                <p key={i} className="mt-1 whitespace-pre-wrap">
                  {x.text ?? <span className="text-muted-foreground">без текста</span>}
                  {(x.photos > 0 || x.videos > 0) && (
                    <span className="text-muted-foreground"> · {[x.photos ? `📷 ${x.photos}` : "", x.videos ? `🎬 ${x.videos}` : ""].filter(Boolean).join(" ")} — в боте</span>
                  )}
                </p>
              ))}
            </div>
          ))}
          {silent.length > 0 && <p className="text-xs text-muted-foreground">Не ответили: {silent.map((r) => r.name).join(", ")}</p>}
        </div>
      )}
      {!ack ? null : unread.length > 0 ? (
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
      {ack && read.length > 0 && (
        <p className="text-xs text-muted-foreground">Прочитали: {read.map((r) => r.name).join(", ")}</p>
      )}
    </div>
  );
}

/** Saved texts: pick one to fill the editor; save the current text as a new one or over the picked one. */
function Templates({ editor }: { editor: React.RefObject<RichEditorHandle | null> }) {
  const { data, reload } = useLoad(() => api.admin.templates(), []);
  const [current, setCurrent] = useState<BroadcastTemplate | null>(null);
  const [naming, setNaming] = useState(false);
  const [title, setTitle] = useState("");
  const [manage, setManage] = useState(false);
  const { busy, run } = useAction();
  const items = data?.items ?? [];

  const save = async (over: BroadcastTemplate | null) => {
    const html = editor.current?.getHtml().html ?? "";
    if (!html.replace(/<[^>]+>/g, "").trim()) {
      toast.error("Сначала напишите текст шаблона");
      return;
    }
    try {
      const res = await run("save", () => api.admin.saveTemplate(over ? over.title : title.trim(), html, over?.id),
        over ? "Шаблон обновлён" : "Шаблон сохранён");
      if (res) {
        setCurrent(res.template.id ? { ...res.template, author: null, can_edit: true } : null);
        setNaming(false);
        setTitle("");
        await reload();
      }
    } catch {
      /* toast shown */
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-sm text-muted-foreground">Шаблоны:</span>
        {items.length === 0 && <span className="text-sm text-muted-foreground">пока нет</span>}
        {items.map((t) => (
          <span key={t.id} className={cn("inline-flex h-8 items-center rounded-full text-sm font-medium",
            current?.id === t.id ? "bg-primary text-primary-foreground" : "bg-muted")}>
            <button type="button" className="press h-full px-3" onClick={() => { editor.current?.setHtml(t.html); setCurrent(t); }}>
              {t.title}
            </button>
            {manage && t.can_edit && (
              <button type="button" aria-label={`Удалить шаблон «${t.title}»`} className="grid h-full place-items-center pr-2.5 opacity-70 hover:opacity-100"
                      onClick={async () => {
                        try {
                          await run(`del${t.id}`, () => api.admin.deleteTemplate(t.id), "Шаблон удалён");
                          if (current?.id === t.id) setCurrent(null);
                          await reload();
                        } catch { /* toast */ }
                      }}>
                <Trash2 className="size-3.5" />
              </button>
            )}
          </span>
        ))}
        <button type="button" className="press inline-flex h-8 items-center gap-1 rounded-full px-3 text-sm font-medium text-muted-foreground hover:bg-muted"
                onClick={() => setNaming((v) => !v)}>
          <Plus className="size-3.5" /> Сохранить текст
        </button>
        {items.some((t) => t.can_edit) && (
          <button type="button" className="press inline-flex h-8 items-center gap-1 rounded-full px-3 text-sm font-medium text-muted-foreground hover:bg-muted"
                  onClick={() => setManage((v) => !v)}>
            <Pencil className="size-3.5" /> {manage ? "Готово" : "Изменить"}
          </button>
        )}
      </div>
      {current?.can_edit && (
        <button type="button" className="press text-sm font-medium text-primary underline-offset-2 hover:underline" disabled={!!busy}
                onClick={() => save(current)}>
          {busy === "save" ? "Сохраняю…" : `Записать текст редактора в шаблон «${current.title}»`}
        </button>
      )}
      {naming && (
        <div className="row-in flex gap-2">
          <input autoFocus className="h-10 min-w-0 flex-1 rounded-xl border-0 bg-muted px-3 outline-none focus:ring-2 focus:ring-ring/25"
                 placeholder="Название, например «Сбор утром»" value={title} onChange={(e) => setTitle(e.target.value.slice(0, 60))}
                 onKeyDown={(e) => e.key === "Enter" && title.trim() && save(null)} />
          <Button className="press h-10 rounded-xl" disabled={!!busy || !title.trim()} onClick={() => save(null)}>
            {busy === "save" && <LoaderCircle className="animate-spin" />} Сохранить
          </Button>
        </div>
      )}
    </div>
  );
}
