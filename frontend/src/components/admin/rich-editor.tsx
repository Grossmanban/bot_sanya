"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  Bold,
  Code,
  EyeOff,
  Italic,
  Link2,
  RemoveFormatting,
  Strikethrough,
  TextQuote,
  Underline,
  Unlink,
} from "lucide-react";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";

/*
 * A small WYSIWYG editor for Telegram messages. What you see is what the bot sends:
 * the DOM is serialized into Telegram's HTML subset (b, i, u, s, tg-spoiler, code,
 * blockquote, a). The server sanitizes it again before sending.
 */

export interface RichEditorHandle {
  getHtml: () => { html: string; length: number };
  clear: () => void;
  /** put Telegram HTML (a saved template) into the editor */
  setHtml: (html: string) => void;
}

type Mark = "bold" | "italic" | "underline" | "strike" | "spoiler" | "code" | "quote" | "link";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Serialize the editor DOM into Telegram HTML. */
export function toTelegramHtml(root: HTMLElement): { html: string; length: number } {
  const out: string[] = [];
  let length = 0;
  const push = (s: string) => out.push(s);
  const endsWithNewline = () => {
    const last = out.join("").replace(/(<[^>]+>)+$/, "");
    return last === "" || last.endsWith("\n");
  };

  const walk = (node: Node, inCode: boolean) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = (node.textContent ?? "").replace(/ /g, " ");
      length += text.length;
      push(esc(text));
      return;
    }
    if (!(node instanceof HTMLElement)) return;
    const tag = node.tagName.toLowerCase();
    if (tag === "br") {
      length += 1;
      push("\n");
      return;
    }
    const block = tag === "div" || tag === "p";
    if (block && !endsWithNewline()) {
      length += 1;
      push("\n");
    }
    const style = node.getAttribute("style") ?? "";
    const wrappers: string[] = [];
    let href: string | null = null;
    if (!inCode) {
      if (tag === "b" || tag === "strong" || /font-weight:\s*(bold|[6-9]00)/.test(style)) wrappers.push("b");
      if (tag === "i" || tag === "em" || /font-style:\s*italic/.test(style)) wrappers.push("i");
      if (tag === "u" || /text-decoration[^;]*underline/.test(style)) wrappers.push("u");
      if (tag === "s" || tag === "strike" || tag === "del" || /text-decoration[^;]*line-through/.test(style)) wrappers.push("s");
      if (node.classList.contains("tg-spoiler")) wrappers.push("tg-spoiler");
      if (tag === "blockquote") wrappers.push("blockquote");
      if (tag === "code") wrappers.push("code");
      if (tag === "a") {
        href = node.getAttribute("href");
        if (href && /^(https?:\/\/|tg:\/\/)/i.test(href)) wrappers.push("a");
      }
    }
    for (const w of wrappers) push(w === "a" ? `<a href="${esc(href!).replace(/"/g, "&quot;")}">` : `<${w}>`);
    node.childNodes.forEach((child) => walk(child, inCode || wrappers.includes("code")));
    for (const w of [...wrappers].reverse()) push(`</${w}>`);
    if (tag === "blockquote" && !endsWithNewline()) {
      length += 1;
      push("\n");
    }
  };

  root.childNodes.forEach((child) => walk(child, false));
  return { html: out.join("").replace(/^\n+|\n+$/g, ""), length };
}

const SELECTORS: Partial<Record<Mark, string>> = {
  spoiler: "span.tg-spoiler",
  code: "code",
  quote: "blockquote",
  link: "a",
};

function closestIn(node: Node | null, selector: string, root: HTMLElement): HTMLElement | null {
  let el: Node | null = node;
  while (el && el !== root) {
    if (el instanceof HTMLElement && el.matches(selector)) return el;
    el = el.parentNode;
  }
  return null;
}

function unwrap(el: HTMLElement) {
  const parent = el.parentNode;
  if (!parent) return;
  while (el.firstChild) parent.insertBefore(el.firstChild, el);
  parent.removeChild(el);
}

export const RichEditor = forwardRef<RichEditorHandle, { placeholder?: string; onChange?: (length: number) => void }>(
  function RichEditor({ placeholder, onChange }, ref) {
    const root = useRef<HTMLDivElement>(null);
    const saved = useRef<Range | null>(null);
    const [active, setActive] = useState<Partial<Record<Mark, boolean>>>({});
    const [linkOpen, setLinkOpen] = useState(false);
    const [linkUrl, setLinkUrl] = useState("https://");

    const emit = useCallback(() => {
      if (root.current) onChange?.(toTelegramHtml(root.current).length);
    }, [onChange]);

    useImperativeHandle(ref, () => ({
      getHtml: () => (root.current ? toTelegramHtml(root.current) : { html: "", length: 0 }),
      clear: () => {
        if (root.current) root.current.innerHTML = "";
        emit();
      },
      setHtml: (html: string) => {
        // the server sanitized it to Telegram's tags; spoilers are spans here, line breaks are <br>
        if (root.current) {
          root.current.innerHTML = html
            .replace(/<tg-spoiler>/g, '<span class="tg-spoiler">').replace(/<\/tg-spoiler>/g, "</span>")
            .replace(/<\/blockquote>\n/g, "</blockquote>").replace(/\n/g, "<br>");
        }
        emit();
      },
    }));

    // keep the toolbar in sync with the formatting under the caret
    useEffect(() => {
      const update = () => {
        const sel = document.getSelection();
        const el = root.current;
        if (!sel || !sel.rangeCount || !el || !el.contains(sel.anchorNode)) return;
        saved.current = sel.getRangeAt(0).cloneRange();
        const node = sel.anchorNode;
        setActive({
          // check real tags: a link looks underlined but is not <u>
          bold: !!closestIn(node, "b, strong", el),
          italic: !!closestIn(node, "i, em", el),
          underline: !!closestIn(node, "u", el),
          strike: !!closestIn(node, "s, strike, del", el),
          spoiler: !!closestIn(node, SELECTORS.spoiler!, el),
          code: !!closestIn(node, SELECTORS.code!, el),
          quote: !!closestIn(node, SELECTORS.quote!, el),
          link: !!closestIn(node, SELECTORS.link!, el),
        });
      };
      document.addEventListener("selectionchange", update);
      return () => document.removeEventListener("selectionchange", update);
    }, []);

    const restore = () => {
      const sel = document.getSelection();
      root.current?.focus();
      if (sel && saved.current) {
        sel.removeAllRanges();
        sel.addRange(saved.current);
      }
    };

    const wrap = (mark: "spoiler" | "code" | "quote") => {
      const el = root.current;
      const sel = document.getSelection();
      if (!el || !sel || !sel.rangeCount) return;
      const range = sel.getRangeAt(0);
      if (!el.contains(range.commonAncestorContainer)) return;
      const existing = closestIn(range.startContainer, SELECTORS[mark]!, el);
      if (existing) {
        unwrap(existing);
      } else {
        if (range.collapsed) return;
        const node =
          mark === "spoiler"
            ? Object.assign(document.createElement("span"), { className: "tg-spoiler" })
            : document.createElement(mark === "quote" ? "blockquote" : "code");
        node.appendChild(range.extractContents());
        range.insertNode(node);
        const r = document.createRange();
        r.selectNodeContents(node);
        sel.removeAllRanges();
        sel.addRange(r);
      }
      el.normalize();
    };

    const apply = (mark: Mark) => {
      tapHaptic();
      restore();
      if (mark === "bold") document.execCommand("bold");
      else if (mark === "italic") document.execCommand("italic");
      else if (mark === "underline") document.execCommand("underline");
      else if (mark === "strike") document.execCommand("strikeThrough");
      else if (mark === "link") {
        const a = root.current && closestIn(saved.current?.startContainer ?? null, "a", root.current);
        if (a) unwrap(a);
        else {
          setLinkOpen(true);
          return;
        }
      } else wrap(mark);
      emit();
      document.dispatchEvent(new Event("selectionchange"));
    };

    const addLink = () => {
      const url = linkUrl.trim();
      if (!/^(https?:\/\/|tg:\/\/)\S+\.\S+|^tg:\/\/\S+/i.test(url)) return;
      restore();
      const sel = document.getSelection();
      if (sel && sel.rangeCount && sel.getRangeAt(0).collapsed) {
        const a = document.createElement("a");
        a.href = url;
        a.textContent = url;
        sel.getRangeAt(0).insertNode(a);
      } else {
        document.execCommand("createLink", false, url);
      }
      setLinkOpen(false);
      setLinkUrl("https://");
      emit();
    };

    const clearFormat = () => {
      restore();
      document.execCommand("removeFormat");
      const el = root.current;
      const node = saved.current?.startContainer ?? null;
      if (el)
        for (const sel of Object.values(SELECTORS)) {
          const found = closestIn(node, sel!, el);
          if (found) unwrap(found);
        }
      emit();
    };

    const tools: { mark: Mark; icon: typeof Bold; label: string }[] = [
      { mark: "bold", icon: Bold, label: "Жирный" },
      { mark: "italic", icon: Italic, label: "Курсив" },
      { mark: "underline", icon: Underline, label: "Подчёркнутый" },
      { mark: "strike", icon: Strikethrough, label: "Зачёркнутый" },
      { mark: "spoiler", icon: EyeOff, label: "Скрытый текст" },
      { mark: "code", icon: Code, label: "Моноширинный" },
      { mark: "quote", icon: TextQuote, label: "Цитата" },
      { mark: "link", icon: active.link ? Unlink : Link2, label: active.link ? "Убрать ссылку" : "Ссылка" },
    ];

    return (
      <div className="overflow-hidden rounded-2xl bg-muted">
        <div className="no-scrollbar flex gap-1 overflow-x-auto border-b border-border/70 p-1.5">
          {tools.map(({ mark, icon: Icon, label }) => (
            <button
              key={mark}
              type="button"
              aria-label={label}
              title={label}
              aria-pressed={!!active[mark]}
              // keep the text selection: act on pointer down, never take focus
              onPointerDown={(e) => {
                e.preventDefault();
                apply(mark);
              }}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), apply(mark))}
              className={cn(
                "grid size-10 shrink-0 place-items-center rounded-xl transition-colors",
                active[mark] ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-card hover:text-foreground",
              )}
            >
              <Icon className="size-[18px]" strokeWidth={2.1} />
            </button>
          ))}
          <span className="mx-1 w-px shrink-0 bg-border" />
          <button
            type="button"
            aria-label="Очистить форматирование"
            title="Очистить форматирование"
            onPointerDown={(e) => {
              e.preventDefault();
              clearFormat();
            }}
            className="grid size-10 shrink-0 place-items-center rounded-xl text-muted-foreground hover:bg-card hover:text-foreground"
          >
            <RemoveFormatting className="size-[18px]" />
          </button>
        </div>

        {linkOpen && (
          <div className="flex gap-2 border-b border-border/70 p-2">
            <input
              autoFocus
              inputMode="url"
              value={linkUrl}
              onChange={(e) => setLinkUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addLink();
                }
                if (e.key === "Escape") setLinkOpen(false);
              }}
              placeholder="https://"
              className="h-10 min-w-0 flex-1 rounded-xl border-0 bg-card px-3 outline-none focus:ring-2 focus:ring-ring/25"
            />
            <button type="button" onClick={addLink} className="h-10 rounded-xl bg-primary px-4 text-sm font-semibold text-primary-foreground">
              Добавить
            </button>
            <button type="button" onClick={() => setLinkOpen(false)} className="h-10 rounded-xl px-3 text-sm text-muted-foreground">
              Отмена
            </button>
          </div>
        )}

        <div
          ref={root}
          role="textbox"
          aria-multiline
          aria-label="Текст сообщения"
          contentEditable
          suppressContentEditableWarning
          data-placeholder={placeholder}
          onInput={emit}
          onPaste={(e) => {
            // paste as plain text so foreign styles never leak in
            e.preventDefault();
            document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
          }}
          className="tg-editor min-h-40 px-4 py-3 text-[15px] leading-relaxed outline-none"
        />
      </div>
    );
  },
);
