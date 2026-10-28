"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowDown, ArrowUp, BookOpen, Bold, Check, ChevronLeft, ChevronRight, Eye, FileUp, Heading, Italic, List, ListOrdered,
  LoaderCircle, Plus, Trash2,
} from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, newKey, type ManualAdmin, type ManualRead } from "@/lib/api";
import { useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";
// the server has already cleaned the HTML (whitelist); this only styles it
const prose = "manual-prose space-y-3 text-[15px] leading-relaxed break-words [&_a]:font-semibold [&_a]:underline [&_blockquote]:border-l-4 [&_blockquote]:pl-3 [&_h1]:font-display [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:font-display [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:font-semibold [&_img]:max-w-full [&_img]:rounded-xl [&_li]:ml-5 [&_ol]:list-decimal [&_table]:w-full [&_table]:text-sm [&_td]:border [&_td]:p-1.5 [&_th]:border [&_th]:p-1.5 [&_ul]:list-disc";

/** ТЗ 28.10 §12: the manuals given to this person — sections, «Назад» / «Далее», progress, read again any time. */
export function ManualsReader() {
  const { data } = useLoad(() => api.manuals(), []);
  const [open, setOpen] = useState<number | null>(null);
  if (open !== null) return <Reader id={open} onBack={() => setOpen(null)} />;
  if (!data) return <Skeleton className="h-40 rounded-3xl" />;
  if (data.items.length === 0) return <p className="rounded-3xl bg-card p-5 text-sm text-muted-foreground shadow-lift">Методичек пока нет.</p>;
  return (
    <ul className="space-y-2">
      {data.items.map((m) => (
        <li key={m.id}>
          <button onClick={() => setOpen(m.id)} className="press flex w-full items-center gap-3 rounded-3xl bg-card p-4 text-left shadow-lift">
            <span className="grid size-10 shrink-0 place-items-center rounded-2xl bg-primary text-primary-foreground"><BookOpen className="size-5" /></span>
            <span className="min-w-0 flex-1">
              <span className="block font-semibold break-words">{m.title}</span>
              <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-muted">
                <span className="block h-full rounded-full bg-go" style={{ width: `${m.total ? (m.read / m.total) * 100 : 0}%` }} />
              </span>
              <span className="text-xs text-muted-foreground">Прочитано {m.read} из {m.total}</span>
            </span>
            <ChevronRight className="size-5 shrink-0 text-muted-foreground" />
          </button>
        </li>
      ))}
    </ul>
  );
}

function Reader({ id, onBack }: { id: number; onBack: () => void }) {
  const { data, setData, error } = useLoad(() => api.manual(id), [id]);
  const [at, setAt] = useState<number | null>(null);
  const top = useRef<HTMLDivElement>(null);
  const m = data?.manual;
  const section = m && at !== null ? m.sections[at] : null;

  useEffect(() => {
    if (!m || !section || section.read) return;
    void api.manualRead(m.id, section.id).then((r) => setData({ manual: r.manual })).catch(() => undefined);
  }, [m, section, setData]);
  useEffect(() => top.current?.scrollIntoView({ block: "start" }), [at]);

  if (error) return <p className="rounded-3xl bg-card p-5 text-sm text-cancel shadow-lift">{error.message}</p>;
  if (!m) return <Skeleton className="h-64 rounded-3xl" />;
  return (
    <div ref={top} className="space-y-3">
      <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={at === null ? onBack : () => setAt(null)}>
        <ChevronLeft /> {at === null ? "Все методички" : "Оглавление"}
      </Button>
      {section ? (
        <article className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
          <p className="text-xs text-muted-foreground">Раздел {at! + 1} из {m.sections.length} · {m.title}</p>
          <h2 className="font-display text-xl font-semibold break-words">{section.title}</h2>
          <div className={prose} dangerouslySetInnerHTML={{ __html: section.html }} />
          <div className="flex gap-2 pt-2">
            <Button variant="secondary" className="press h-12 flex-1 rounded-2xl" disabled={at === 0} onClick={() => setAt(at! - 1)}>
              <ChevronLeft /> Назад
            </Button>
            {at! < m.sections.length - 1 ? (
              <Button className="press h-12 flex-1 rounded-2xl" onClick={() => setAt(at! + 1)}>Далее <ChevronRight /></Button>
            ) : (
              <Button className="press h-12 flex-1 rounded-2xl" onClick={() => setAt(null)}><Check /> К оглавлению</Button>
            )}
          </div>
        </article>
      ) : (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <div className="p-5 md:p-6">
            <h2 className="font-display text-xl font-semibold break-words">{m.title}</h2>
            <p className="text-sm text-muted-foreground">Прочитано {m.read} из {m.total}</p>
          </div>
          <ol className="divide-y border-t">
            {m.sections.map((s, i) => (
              <li key={s.id}>
                <button onClick={() => setAt(i)} className="press flex w-full items-center gap-3 px-5 py-3.5 text-left md:px-6">
                  <span className={cn("grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold",
                    s.read ? "bg-go text-white" : "bg-muted text-muted-foreground")}>{s.read ? <Check className="size-4" /> : i + 1}</span>
                  <span className="min-w-0 flex-1 break-words">{s.title}</span>
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

/** «Админ → Методички»: create, upload HTML, edit sections, reorder, preview, delete — every save is a version. */
export function ManualsAdmin() {
  const { data, setData } = useLoad(() => api.admin.manuals(), []);
  const [open, setOpen] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const { busy, run } = useAction();
  if (open !== null) return <Editor id={open} onBack={() => setOpen(null)} />;
  if (!data) return <Skeleton className="h-40 rounded-3xl" />;
  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h2 className="font-semibold">Методички</h2>
        <p className="text-sm text-muted-foreground">
          Видны в разделе «Обучение» только тем, кому выданы (выдаёт «Команда» → человек). Загрузите HTML — разделы получатся по заголовкам,
          или наберите разделы здесь. Скрипты, стили и внешние картинки удаляются; содержимое никуда не передаётся.
        </p>
        <div className="flex gap-2">
          <input className={field} placeholder="Название новой методички" value={title} onChange={(e) => setTitle(e.target.value.slice(0, 120))} />
          <Button className="press h-11 shrink-0 rounded-xl" disabled={!!busy || !title.trim()}
                  onClick={async () => {
                    try {
                      const res = await run("create", (k) => api.admin.createManual(title.trim(), k), "Методичка создана");
                      if (res) { setData({ items: res.items }); setTitle(""); setOpen(res.manual.id); }
                    } catch { /* toast shown */ }
                  }}>
            <Plus /> Создать
          </Button>
        </div>
      </section>
      {data.items.length > 0 && (
        <ul className="overflow-hidden rounded-3xl bg-card shadow-lift">
          {data.items.map((m) => (
            <li key={m.id} className="border-b last:border-0">
              <button onClick={() => setOpen(m.id)} className="press flex w-full items-center gap-3 px-5 py-3.5 text-left md:px-6">
                <span className="min-w-0 flex-1">
                  <span className="block font-medium break-words">{m.title}</span>
                  <span className="text-xs text-muted-foreground">разделов {m.sections} · выдано {m.people} · версия {m.version}</span>
                </span>
                <ChevronRight className="size-5 text-muted-foreground" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type Draft = { id?: number; title: string; html: string; key: string };

function Editor({ id, onBack }: { id: number; onBack: () => void }) {
  const { data, setData } = useLoad(() => api.admin.manual(id), [id]);
  const [draft, setDraft] = useState<Draft[] | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const { busy, run } = useAction();
  const m = data?.manual;
  useEffect(() => {
    if (m) setDraft(m.sections.map((s) => ({ id: s.id, title: s.title, html: s.html, key: `s${s.id}` })));
  }, [m]);
  if (!m || !draft) return <Skeleton className="h-64 rounded-3xl" />;
  const dirty = JSON.stringify(draft.map(({ id, title, html }) => ({ id, title, html })))
    !== JSON.stringify(m.sections.map(({ id, title, html }) => ({ id, title, html })));
  const apply = (res?: { manual: ManualAdmin }) => res && setData({ manual: res.manual });
  const move = (i: number, d: number) => setDraft((x) => {
    const next = [...x!];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    return next;
  });

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" className="h-9 rounded-xl" onClick={onBack}><ChevronLeft /> Все методички</Button>
      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div className="flex gap-2">
          <input className={field} value={title ?? m.title} onChange={(e) => setTitle(e.target.value.slice(0, 120))} aria-label="Название" />
          {title !== null && title.trim() && title !== m.title && (
            <Button className="press h-11 shrink-0 rounded-xl" disabled={!!busy}
                    onClick={async () => { try { apply(await run("rename", (k) => api.admin.renameManual(id, title.trim(), k), "Переименовано")); setTitle(null); } catch { /* */ } }}>
              Сохранить
            </Button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">Версия {m.version}{m.versions[0] ? ` · ${m.versions[0].created_at.slice(8, 10)}.${m.versions[0].created_at.slice(5, 7)}${m.versions[0].note ? ` · ${m.versions[0].note}` : ""}` : ""}</p>
        <div className="flex flex-wrap gap-2">
          <label className="press inline-flex h-10 cursor-pointer items-center gap-2 rounded-xl bg-muted px-4 text-sm font-semibold">
            <FileUp className="size-4" /> {m.sections.length ? "Заменить из HTML" : "Загрузить HTML"}
            <input type="file" accept=".html,.htm,text/html" className="hidden" onChange={async (e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              try { apply(await run("upload", async (k) => api.admin.uploadManual(id, await file.text(), k), "Загружено — новая версия")); } catch { /* */ }
            }} />
          </label>
          <Button variant="secondary" className="press h-10 rounded-xl" onClick={() => setPreview((v) => !v)}><Eye /> {preview ? "Редактировать" : "Предпросмотр"}</Button>
          <Button variant="ghost" className="press h-10 rounded-xl text-cancel" onClick={() => setConfirm(true)}><Trash2 /> Удалить</Button>
        </div>
      </section>

      {preview ? (
        <PreviewManual manual={{ id: m.id, title: m.title, read: 0, total: draft.length, sections: draft.map((s, i) => ({ id: s.id ?? -i, title: s.title, read: false, html: s.html })) }} />
      ) : (
        <ol className="space-y-3">
          {draft.map((s, i) => (
            <li key={s.key} className="space-y-2 rounded-3xl bg-card p-4 shadow-lift">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-muted-foreground">{i + 1}</span>
                <input className={field} value={s.title} placeholder="Название раздела"
                       onChange={(e) => setDraft(draft.map((x, j) => j === i ? { ...x, title: e.target.value.slice(0, 120) } : x))} />
                <Button size="icon" variant="ghost" className="size-9 shrink-0" aria-label="Выше" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp /></Button>
                <Button size="icon" variant="ghost" className="size-9 shrink-0" aria-label="Ниже" disabled={i === draft.length - 1} onClick={() => move(i, 1)}><ArrowDown /></Button>
                <Button size="icon" variant="ghost" className="size-9 shrink-0 text-cancel" aria-label="Удалить раздел"
                        onClick={() => setDraft(draft.filter((_, j) => j !== i))}><Trash2 /></Button>
              </div>
              <SectionEditor html={s.html} onChange={(html) => setDraft((d) => d!.map((x, j) => j === i ? { ...x, html } : x))} />
            </li>
          ))}
        </ol>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" className="press h-11 rounded-xl" onClick={() => setDraft([...draft, { title: "", html: "", key: newKey() }])}>
          <Plus /> Раздел
        </Button>
        <Button className="press h-11 flex-1 rounded-xl" disabled={!!busy || !dirty}
                onClick={async () => { try { apply(await run("save", (k) => api.admin.saveManual(id, draft.map(({ id, title, html }) => ({ id, title, html })), k), "Сохранено — новая версия")); } catch { /* */ } }}>
          {busy === "save" && <LoaderCircle className="animate-spin" />} {dirty ? "Сохранить изменения" : "Всё сохранено"}
        </Button>
      </div>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent className="rounded-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-display">Удалить «{m.title}»?</AlertDialogTitle>
            <AlertDialogDescription>Методичка пропадёт у всех, кому выдана. Версии останутся в базе.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="rounded-xl">Отмена</AlertDialogCancel>
            <AlertDialogAction variant="destructive" className="rounded-xl"
                               onClick={async () => { try { if (await run("delete", (k) => api.admin.deleteManual(id, k), "Удалено")) onBack(); } catch { /* */ } }}>
              Удалить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function PreviewManual({ manual }: { manual: ManualRead }) {
  return (
    <div className="space-y-3">
      {manual.sections.map((s, i) => (
        <article key={s.id} className="space-y-3 rounded-3xl bg-card p-5 shadow-lift">
          <p className="text-xs text-muted-foreground">Раздел {i + 1} из {manual.sections.length}</p>
          <h2 className="font-display text-xl font-semibold break-words">{s.title || "Без названия"}</h2>
          <div className={prose} dangerouslySetInnerHTML={{ __html: s.html }} />
        </article>
      ))}
      <p className="text-xs text-muted-foreground">Так увидит сотрудник. Перед сохранением сервер ещё раз очистит HTML.</p>
    </div>
  );
}

/** Headings, paragraphs, lists, bold and italic — the server keeps only what's allowed. */
function SectionEditor({ html, onChange }: { html: string; onChange: (html: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current && ref.current.innerHTML !== html) ref.current.innerHTML = html;
  }, [html]);
  const cmd = (name: string, value?: string) => {
    ref.current?.focus();
    document.execCommand(name, false, value);
    onChange(ref.current?.innerHTML ?? "");
  };
  const tools: [typeof Bold, string, () => void][] = [
    [Heading, "Подзаголовок", () => cmd("formatBlock", "h3")],
    [Bold, "Жирный", () => cmd("bold")],
    [Italic, "Курсив", () => cmd("italic")],
    [List, "Список", () => cmd("insertUnorderedList")],
    [ListOrdered, "Нумерованный список", () => cmd("insertOrderedList")],
  ];
  return (
    <div className="rounded-xl bg-muted/60">
      <div className="flex flex-wrap gap-1 border-b border-border/60 p-1">
        {tools.map(([Icon, label, fn]) => (
          <button key={label} type="button" aria-label={label} title={label} onMouseDown={(e) => { e.preventDefault(); fn(); }}
                  className="grid size-9 place-items-center rounded-lg hover:bg-card"><Icon className="size-4" /></button>
        ))}
        <button type="button" onMouseDown={(e) => { e.preventDefault(); cmd("formatBlock", "p"); }} className="h-9 rounded-lg px-2 text-xs font-semibold hover:bg-card">Обычный текст</button>
      </div>
      <div ref={ref} contentEditable suppressContentEditableWarning className={cn(prose, "min-h-28 p-3 outline-none")}
           onInput={() => onChange(ref.current?.innerHTML ?? "")} />
    </div>
  );
}
