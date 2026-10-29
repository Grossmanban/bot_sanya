"use client";

import { useEffect, useRef, useState } from "react";
import { BookOpen, Check, ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { ManualFrame } from "./manual-frame";
import { ManualEditor } from "./manual-editor";

const field = "h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

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
          {!section.heading && <h2 className="font-display text-xl font-semibold break-words">{section.title}</h2>}
          {/* ТЗ 29.10 §3: the page's own look, in a frame where nothing can run */}
          <div className="-mx-2 overflow-hidden rounded-2xl md:-mx-3">
            <ManualFrame key={section.id} html={section.html} css={m.css} bodyClass={m.body_class} title={section.title} />
          </div>
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

/** «Админ → Методички»: create, upload a ready HTML page, edit it visually or as code, preview, delete — every save
 * is a version of the same manual. */
export function ManualsAdmin() {
  const { data, setData, reload } = useLoad(() => api.admin.manuals(), []);
  const [open, setOpen] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const { busy, run } = useAction();
  if (open !== null) return <ManualEditor id={open} onBack={() => { setOpen(null); void reload(); }} />;
  if (!data) return <Skeleton className="h-40 rounded-3xl" />;
  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <h2 className="font-semibold">Методички</h2>
        <p className="text-sm text-muted-foreground">
          Видны в разделе «Обучение» только тем, кому выданы (выдаёт «Команда» → человек). Загрузите готовую HTML-страницу —
          оформление сохранится, разделы получатся по заголовкам; или наберите и оформите текст в редакторе. Скрипты,
          формы и всё, что грузится из интернета, удаляются; содержимое никуда не передаётся.
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
