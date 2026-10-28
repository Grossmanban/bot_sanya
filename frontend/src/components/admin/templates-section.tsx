"use client";

import { useState } from "react";
import { Check, ChevronDown, LoaderCircle, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type MessageText, type ReasonKind } from "@/lib/api";
import { useAction, useLoad } from "@/lib/hooks";
import { setReasons } from "@/lib/reasons";
import { cn } from "@/lib/utils";

const input = "w-full rounded-xl border-0 bg-muted px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

/** ТЗ 28.10 §2 (В2): the reason templates by kind of action — offered in every form, edited before sending. */
export function ReasonsCard() {
  const { data, setData } = useLoad(() => api.admin.reasons(), []);
  const [open, setOpen] = useState<string | null>(null);
  if (!data) return <Skeleton className="h-40 rounded-3xl" />;
  const update = (kinds: ReasonKind[]) => {
    setData({ kinds });
    setReasons(kinds);
  };
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <div className="px-5 pt-5 pb-2 md:px-6">
        <h2 className="font-semibold">Шаблоны причин</h2>
        <p className="text-sm text-muted-foreground">
          Появляются кнопками там, где нужно указать причину: штраф, блокировка, отказ, отмена и другие. Текст можно поправить перед отправкой.
        </p>
      </div>
      <ul className="divide-y">
        {data.kinds.map((k) => (
          <li key={k.kind}>
            <button className="press flex w-full items-center gap-3 px-5 py-3.5 text-left md:px-6" onClick={() => setOpen(open === k.kind ? null : k.kind)}>
              <span className="min-w-0 flex-1">
                <span className="block font-medium">{k.title}</span>
                <span className="block truncate text-sm text-muted-foreground">
                  {k.items.length ? k.items.map((i) => i.text).join(" · ") : "шаблонов нет"}
                </span>
              </span>
              <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open === k.kind && "rotate-180")} />
            </button>
            {open === k.kind && <ReasonKindEditor kind={k} onChange={update} />}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ReasonKindEditor({ kind, onChange }: { kind: ReasonKind; onChange: (k: ReasonKind[]) => void }) {
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const { busy, run } = useAction();
  const act = async (name: string, fn: (k: string) => Promise<{ kinds: ReasonKind[] }>, ok?: string) => {
    try {
      const res = await run(name, fn, ok);
      if (res) onChange(res.kinds);
      return res;
    } catch {
      return undefined;
    }
  };
  return (
    <div className="space-y-2 px-5 pb-4 md:px-6">
      {kind.items.map((i) => (
        <div key={i.id} className="flex items-center gap-2 rounded-xl bg-muted/60 p-1.5 pl-3">
          {editing?.id === i.id ? (
            <>
              <input className={cn(input, "bg-card py-1.5")} value={editing.text} autoFocus
                     onChange={(e) => setEditing({ id: i.id, text: e.target.value.slice(0, 500) })} />
              <Button size="icon" className="press size-9 shrink-0 rounded-lg" disabled={!!busy || !editing.text.trim()} aria-label="Сохранить"
                      onClick={async () => { if (await act(`e${i.id}`, (k) => api.admin.editReason(i.id, { text: editing.text }, k), "Сохранено")) setEditing(null); }}>
                <Check />
              </Button>
              <Button size="icon" variant="ghost" className="size-9 shrink-0 rounded-lg" aria-label="Отмена" onClick={() => setEditing(null)}><X /></Button>
            </>
          ) : (
            <>
              <span className="min-w-0 flex-1 text-sm break-words">{i.text}</span>
              <Button size="icon" variant="ghost" className="size-9 shrink-0 rounded-lg" aria-label="Изменить"
                      onClick={() => setEditing({ id: i.id, text: i.text })}><Pencil /></Button>
              <Button size="icon" variant="ghost" className="size-9 shrink-0 rounded-lg text-cancel" aria-label="Удалить" disabled={!!busy}
                      onClick={() => act(`d${i.id}`, (k) => api.admin.editReason(i.id, { delete: true }, k), "Шаблон удалён")}><Trash2 /></Button>
            </>
          )}
        </div>
      ))}
      <div className="flex gap-2">
        <input className={input} placeholder="Новый шаблон" value={text} onChange={(e) => setText(e.target.value.slice(0, 500))} />
        <Button className="press h-11 shrink-0 rounded-xl" disabled={!!busy || !text.trim()}
                onClick={async () => { if (await act("add", (k) => api.admin.addReason(kind.kind, text.trim(), k), "Шаблон добавлен")) setText(""); }}>
          {busy === "add" ? <LoaderCircle className="animate-spin" /> : <Plus />} Добавить
        </Button>
      </div>
    </div>
  );
}

/** ТЗ 28.10 §2 (В1): the bot's messages an admin may reword; placeholders like {ФИО_тренера} are filled by the bot. */
export function TextsCard() {
  const { data, setData } = useLoad(() => api.admin.texts(), []);
  const [open, setOpen] = useState<string | null>(null);
  if (!data) return <Skeleton className="h-40 rounded-3xl" />;
  return (
    <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
      <div className="px-5 pt-5 pb-2 md:px-6">
        <h2 className="font-semibold">Тексты сообщений</h2>
        <p className="text-sm text-muted-foreground">Что бот пишет кандидатам и сотрудникам. Слова в фигурных скобках бот заменит сам — их нужно оставить.</p>
      </div>
      <ul className="divide-y">
        {data.items.map((t) => (
          <li key={t.key}>
            <button className="press flex w-full items-center gap-3 px-5 py-3.5 text-left md:px-6" onClick={() => setOpen(open === t.key ? null : t.key)}>
              <span className="min-w-0 flex-1">
                <span className="block font-medium">{t.title}</span>
                <span className="block text-sm text-muted-foreground">{t.edited ? "изменён" : "исходный текст"}</span>
              </span>
              <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open === t.key && "rotate-180")} />
            </button>
            {open === t.key && <TextEditor item={t} onSaved={(items) => setData({ items })} />}
          </li>
        ))}
      </ul>
    </section>
  );
}

function TextEditor({ item, onSaved }: { item: MessageText; onSaved: (items: MessageText[]) => void }) {
  const [text, setText] = useState(item.text);
  const { busy, run } = useAction();
  const save = async (value: string | null) => {
    try {
      const res = await run(value === null ? "reset" : "save", (k) => api.admin.saveText(item.key, value, k),
        value === null ? "Вернули исходный текст" : "Текст сохранён");
      if (res) {
        onSaved(res.items);
        setText(res.items.find((i) => i.key === item.key)?.text ?? text);
      }
    } catch {
      /* toast shown */
    }
  };
  return (
    <div className="space-y-2 px-5 pb-4 md:px-6">
      <textarea rows={8} className={cn(input, "resize-y leading-snug")} value={text} onChange={(e) => setText(e.target.value.slice(0, 3500))} />
      {item.placeholders.length > 0 && (
        <p className="text-xs text-muted-foreground">Обязательно оставить: {item.placeholders.map((p) => `{${p}}`).join(", ")}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button className="press h-11 rounded-xl" disabled={!!busy || text === item.text || !text.trim()} onClick={() => save(text)}>
          {busy === "save" && <LoaderCircle className="animate-spin" />} Сохранить
        </Button>
        {item.edited && (
          <Button variant="secondary" className="press h-11 rounded-xl" disabled={!!busy} onClick={() => save(null)}>
            <RotateCcw /> Вернуть исходный
          </Button>
        )}
      </div>
    </div>
  );
}
