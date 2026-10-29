"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlignCenter, AlignLeft, AlignRight, Bold, ChevronLeft, Code2, Eye, FileUp, Italic, Link2, List, ListOrdered,
  LoaderCircle, PenLine, Redo2, RemoveFormatting, Trash2, Underline, Undo2, Unlink,
} from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type ManualAdmin } from "@/lib/api";
import { useAction, useLoad } from "@/lib/hooks";
import { setClosingConfirmation } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { ManualFrame } from "./manual-frame";

const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";
type Mode = "visual" | "code" | "preview";

/** The page as one HTML text for the «Код» tab: the style sheet, then the content. */
const toCode = (css: string, html: string) => (css.trim() ? `<style>\n${css.trim()}\n</style>\n\n` : "") + html;

function fromCode(code: string): { css: string; html: string } {
  const sheets: string[] = [];
  let html = code.replace(/<style[^>]*>([\s\S]*?)<\/style>/gi, (_, css: string) => {
    sheets.push(css.trim());
    return "";
  });
  // a whole page pasted in: only the body's content stays (the server reads the <head> on save anyway)
  const body = /<body[^>]*>([\s\S]*?)<\/body>/i.exec(html);
  if (body) html = body[1];
  return { css: sheets.join("\n"), html: html.trim() };
}

const sectionsIn = (html: string) => {
  const h2 = (html.match(/<h2[\s>]/gi) ?? []).length;
  return h2 || (html.match(/<h1[\s>]/gi) ?? []).length || 1;
};

/**
 * ТЗ 29.10 §4: the manual is edited as the page it is — the way an employee sees it — with a toolbar (headings,
 * bold/italic/underline, lists, links, alignment, size, colour, undo), a «Код» tab for complex changes and a
 * preview. Saving sends the whole page: the server cleans it and splits it into sections by the headings again —
 * the same manual (its id, people and the reading progress of unchanged sections stay), a new version.
 */
export function ManualEditor({ id, onBack }: { id: number; onBack: () => void }) {
  const { data, setData } = useLoad(() => api.admin.manual(id), [id]);
  const m = data?.manual;
  const [mode, setMode] = useState<Mode>("visual");
  const [html, setHtml] = useState<string | null>(null);   // the content being edited
  const [css, setCss] = useState<string>("");
  const [code, setCode] = useState("");
  const [start, setStart] = useState(0);                    // remounts the editable frame with the current content
  const [dirty, setDirty] = useState(false);
  const [title, setTitle] = useState<string | null>(null);
  const [ask, setAsk] = useState<null | { text: string; go: () => void }>(null);
  const [remove, setRemove] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const { busy, run } = useAction();

  const load = (manual: ManualAdmin) => {
    setData({ manual });
    setHtml(manual.document ?? manual.sections.map((s) => s.html).join("\n"));
    setCss(manual.css ?? "");
    setDirty(false);
    setStart((n) => n + 1);
  };
  useEffect(() => {
    if (m && html === null) {
      setHtml(m.document ?? m.sections.map((s) => s.html).join("\n"));
      setCss(m.css ?? "");
    }
  }, [m, html]);
  // unsaved changes: Telegram asks before the app is closed
  useEffect(() => {
    setClosingConfirmation(dirty);
    return () => setClosingConfirmation(false);
  }, [dirty]);

  if (!m || html === null) return <Skeleton className="h-64 rounded-3xl" />;

  const guard = (text: string, go: () => void) => (dirty ? setAsk({ text, go }) : go());
  const switchTo = (next: Mode) => {
    if (next === mode) return;
    if (mode === "visual") {
      const body = frame.current?.contentDocument?.body;
      if (body) setHtml(body.innerHTML);
    }
    if (mode === "code") {
      const parsed = fromCode(code);
      setHtml(parsed.html);
      setCss(parsed.css);
    }
    if (next === "code") setCode(toCode(css, mode === "visual" ? frame.current?.contentDocument?.body?.innerHTML ?? html : html));
    if (next === "visual") setStart((n) => n + 1);
    setMode(next);
  };
  const current = () => {
    if (mode === "visual") return { css, html: frame.current?.contentDocument?.body?.innerHTML ?? html };
    if (mode === "code") return fromCode(code);
    return { css, html };
  };
  const save = async () => {
    const page = current();
    const doc = `<html><head><style>${page.css}</style></head><body class="${m.body_class ?? ""}">${page.html}</body></html>`;
    try {
      const res = await run("save", (k) => api.admin.saveManualDocument(id, doc, k), "Сохранено — новая версия");
      if (res) {
        load(res.manual);
        if (mode === "code") setCode(toCode(res.manual.css ?? "", res.manual.document ?? ""));
      }
    } catch {
      /* toast shown; the text stays in the editor */
    }
  };
  const upload = async (file: File) => {
    try {
      const res = await run("upload", (k) => api.admin.uploadManual(id, file, k), "Загружено — новая версия");
      if (res) {
        load(res.manual);
        setMode("visual");
      }
    } catch {
      /* toast shown with the concrete reason */
    }
  };
  const last = m.versions[0];

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" className="h-9 rounded-xl"
              onClick={() => guard("Есть несохранённые изменения. Выйти без сохранения?", onBack)}>
        <ChevronLeft /> Все методички
      </Button>

      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div className="flex gap-2">
          <input className={field} value={title ?? m.title} onChange={(e) => setTitle(e.target.value.slice(0, 120))} aria-label="Название" />
          {title !== null && title.trim() && title !== m.title && (
            <Button className="press h-11 shrink-0 rounded-xl" disabled={!!busy}
                    onClick={async () => {
                      try {
                        const res = await run("rename", (k) => api.admin.renameManual(id, title.trim(), k), "Переименовано");
                        if (res) { setData({ manual: { ...m, title: res.manual.title } }); setTitle(null); }
                      } catch { /* */ }
                    }}>
              Сохранить
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          Версия {m.version}{last ? ` · ${last.created_at.slice(8, 10)}.${last.created_at.slice(5, 7)}${last.note ? ` · ${last.note}` : ""}` : ""}
          {" · "}разделов {m.sections.length}
        </p>
        <div className="flex flex-wrap gap-2">
          <label className="press inline-flex h-10 cursor-pointer items-center gap-2 rounded-xl bg-muted px-4 text-sm font-semibold">
            {busy === "upload" ? <LoaderCircle className="size-4 animate-spin" /> : <FileUp className="size-4" />}
            {m.sections.length ? "Заменить из HTML-файла" : "Загрузить HTML-файл"}
            <input type="file" accept=".html,.htm,text/html" className="hidden" onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) guard("Файл заменит содержимое, а несохранённые правки пропадут. Продолжить?", () => void upload(file));
            }} />
          </label>
          <Button variant="ghost" className="press h-10 rounded-xl text-cancel" onClick={() => setRemove(true)}><Trash2 /> Удалить</Button>
        </div>
      </section>

      <div className="flex gap-1 rounded-2xl bg-card p-1 shadow-lift">
        {([["visual", "Визуально", PenLine], ["code", "Код", Code2], ["preview", "Предпросмотр", Eye]] as const).map(([k, label, Icon]) => (
          <button key={k} onClick={() => switchTo(k)}
                  className={cn("press inline-flex h-10 flex-1 items-center justify-center gap-1.5 rounded-xl text-sm font-semibold",
                    mode === k ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>
            <Icon className="size-4" /> {label}
          </button>
        ))}
      </div>

      {mode === "visual" && (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <Toolbar frame={frame} onChange={() => setDirty(true)} />
          <div className="border-t">
            <ManualFrame key={start} frameRef={frame} editable html={html} css={css} bodyClass={m.body_class}
                         onInput={() => setDirty(true)} title="Редактор методички" />
          </div>
          <p className="border-t px-5 py-3 text-xs text-muted-foreground">
            Новый раздел — с «Заголовка 2» (у методички без таких заголовков — с «Заголовка 1»). Картинки и сложная вёрстка —
            через HTML-файл или вкладку «Код».
          </p>
        </section>
      )}

      {mode === "code" && (
        <section className="space-y-2 rounded-3xl bg-card p-4 shadow-lift">
          <textarea value={code} spellCheck={false} aria-label="HTML-код методички"
                    onChange={(e) => { setCode(e.target.value); setDirty(true); }}
                    className="min-h-[60dvh] w-full resize-y rounded-2xl bg-muted p-3 font-mono text-[13px] leading-relaxed outline-none focus:ring-2 focus:ring-ring/25" />
          <p className="text-xs text-muted-foreground">
            Стили — в блоке &lt;style&gt;. При сохранении сервер удалит скрипты, обработчики событий, формы и всё, что грузится из интернета.
          </p>
        </section>
      )}

      {mode === "preview" && (
        <section className="space-y-3">
          <p className="rounded-2xl bg-muted/60 px-4 py-2.5 text-sm text-muted-foreground">
            Так страница выглядит у сотрудника (он видит её по разделам: {sectionsIn(html)}). Окончательную очистку сделает сервер при сохранении.
          </p>
          <div className="overflow-hidden rounded-3xl shadow-lift">
            <ManualFrame html={html} css={css} bodyClass={m.body_class} title="Предпросмотр" />
          </div>
        </section>
      )}

      <div className="sticky bottom-28 z-10 flex gap-2 lg:bottom-4">
        <Button className="press h-12 flex-1 rounded-2xl text-[15px] font-semibold shadow-lift-strong" disabled={!!busy || !dirty} onClick={save}>
          {busy === "save" && <LoaderCircle className="animate-spin" />} {dirty ? "Сохранить изменения" : "Всё сохранено"}
        </Button>
        {dirty && (
          <Button variant="secondary" className="press h-12 rounded-2xl shadow-lift-strong"
                  onClick={() => setAsk({ text: "Отменить все несохранённые правки?", go: () => { load(m); setMode("visual"); } })}>
            Отменить
          </Button>
        )}
      </div>

      <AlertDialog open={!!ask} onOpenChange={(o) => !o && setAsk(null)}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Несохранённые изменения</AlertDialogTitle>
            <AlertDialogDescription>{ask?.text}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Остаться</AlertDialogCancel>
            <AlertDialogAction variant="destructive" className="rounded-xl" onClick={() => { const go = ask?.go; setAsk(null); setDirty(false); go?.(); }}>
              Да
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={remove} onOpenChange={setRemove}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Удалить «{m.title}»?</AlertDialogTitle>
            <AlertDialogDescription>Методичка пропадёт у всех, кому выдана. Версии останутся в базе.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
            <AlertDialogAction variant="destructive" className="rounded-xl"
                               onClick={async () => { try { if (await run("delete", (k) => api.admin.deleteManual(id, k), "Удалено")) { setDirty(false); onBack(); } } catch { /* */ } }}>
              Удалить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

const SIZES: [string, string][] = [["2", "Мелкий"], ["3", "Обычный"], ["4", "Крупнее"], ["5", "Крупный"], ["6", "Очень крупный"]];

/** The visual editor's tools — browser editing commands on the page inside the frame (inline styles, so the look
 * survives the server's cleaning). */
function Toolbar({ frame, onChange }: { frame: React.RefObject<HTMLIFrameElement | null>; onChange: () => void }) {
  const cmd = (name: string, value?: string) => {
    const win = frame.current?.contentWindow;
    const doc = frame.current?.contentDocument;
    if (!win || !doc) return;
    win.focus();
    try {
      doc.execCommand("styleWithCSS", false, "true");
    } catch {
      /* older engines */
    }
    doc.execCommand(name, false, value);
    onChange();
  };
  const link = () => {
    const url = window.prompt("Ссылка (https://…)", "https://");
    if (url && /^(https?:\/\/|mailto:|#)/i.test(url.trim())) cmd("createLink", url.trim());
  };
  const btn = "grid size-9 shrink-0 place-items-center rounded-lg hover:bg-muted";
  const tool = (label: string, fn: () => void, child: React.ReactNode, cls = btn) => (
    <button key={label} type="button" aria-label={label} title={label} className={cls}
            onMouseDown={(e) => { e.preventDefault(); fn(); }}>{child}</button>
  );
  return (
    <div className="no-scrollbar sticky top-0 z-10 flex gap-1 overflow-x-auto bg-card p-1.5">
      <select aria-label="Вид абзаца" className="h-9 shrink-0 rounded-lg bg-muted px-2 text-sm outline-none" defaultValue=""
              onChange={(e) => { if (e.target.value) cmd("formatBlock", e.target.value); e.target.value = ""; }}>
        <option value="" disabled>Абзац…</option>
        <option value="h1">Заголовок 1</option>
        <option value="h2">Заголовок 2 — новый раздел</option>
        <option value="h3">Заголовок 3</option>
        <option value="p">Обычный текст</option>
        <option value="blockquote">Цитата</option>
      </select>
      {tool("Жирный", () => cmd("bold"), <Bold className="size-4" />)}
      {tool("Курсив", () => cmd("italic"), <Italic className="size-4" />)}
      {tool("Подчёркнутый", () => cmd("underline"), <Underline className="size-4" />)}
      {tool("Список", () => cmd("insertUnorderedList"), <List className="size-4" />)}
      {tool("Нумерованный список", () => cmd("insertOrderedList"), <ListOrdered className="size-4" />)}
      {tool("По левому краю", () => cmd("justifyLeft"), <AlignLeft className="size-4" />)}
      {tool("По центру", () => cmd("justifyCenter"), <AlignCenter className="size-4" />)}
      {tool("По правому краю", () => cmd("justifyRight"), <AlignRight className="size-4" />)}
      {tool("Ссылка", link, <Link2 className="size-4" />)}
      {tool("Убрать ссылку", () => cmd("unlink"), <Unlink className="size-4" />)}
      <select aria-label="Размер текста" className="h-9 shrink-0 rounded-lg bg-muted px-2 text-sm outline-none" defaultValue=""
              onChange={(e) => { if (e.target.value) cmd("fontSize", e.target.value); e.target.value = ""; }}>
        <option value="" disabled>Размер…</option>
        {SIZES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <label className={cn(btn, "relative cursor-pointer")} title="Цвет текста" aria-label="Цвет текста">
        <span className="size-4 rounded-full border bg-[conic-gradient(red,orange,yellow,green,blue,violet,red)]" />
        <input type="color" className="absolute inset-0 cursor-pointer opacity-0" onChange={(e) => cmd("foreColor", e.target.value)} />
      </label>
      {tool("Очистить оформление", () => cmd("removeFormat"), <RemoveFormatting className="size-4" />)}
      {tool("Отменить", () => cmd("undo"), <Undo2 className="size-4" />)}
      {tool("Повторить", () => cmd("redo"), <Redo2 className="size-4" />)}
    </div>
  );
}
