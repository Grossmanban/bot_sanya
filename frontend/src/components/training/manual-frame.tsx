"use client";

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { openExternal } from "@/lib/telegram";

/**
 * ТЗ 29.10 §3: a manual is shown as its own page — its styles, colours, tables and pictures — inside a sandboxed
 * frame: no scripts run there (the sandbox has no allow-scripts, and a Content-Security-Policy forbids them and any
 * outside loads too). The server has already cleaned the HTML and the style sheet; this is the second wall.
 * The frame grows to its content (the app scrolls, not the frame), links open outside, #anchors scroll the page.
 * With `editable`, the page itself is edited in place (the visual editor, §4).
 */
export function ManualFrame({ html, css, bodyClass, editable, frameRef, onInput, className, title }: {
  html: string;
  css?: string;
  bodyClass?: string;
  editable?: boolean;
  frameRef?: RefObject<HTMLIFrameElement | null>;
  onInput?: (bodyHtml: string) => void;
  className?: string;
  title?: string;
}) {
  const own = useRef<HTMLIFrameElement>(null);
  const ref = frameRef ?? own;
  const [height, setHeight] = useState(120);
  const theme = useTheme(!css?.trim());
  const onInputRef = useRef(onInput);
  useEffect(() => {
    onInputRef.current = onInput;
  }, [onInput]);

  const srcDoc = useMemo(() => {
    const origin = typeof window === "undefined" ? "" : window.location.origin;
    const policy = `default-src 'none'; img-src ${origin} data: blob:; style-src 'unsafe-inline'; font-src data:; ` +
      "script-src 'none'; frame-src 'none'; form-action 'none'";
    // a sheet can never close its own <style> tag (the server escapes «<» as well)
    const safeCss = (css ?? "").replace(/</g, "\\3C ");
    const cls = (bodyClass ?? "").replace(/[^\w\s-]/g, "");
    return `<!doctype html><html lang="ru"><head><meta charset="utf-8">` +
      `<meta http-equiv="Content-Security-Policy" content="${policy}">` +
      `<meta name="viewport" content="width=device-width, initial-scale=1">` +
      `<style>${base(theme, !!editable)}</style><style>${safeCss}</style></head>` +
      `<body class="${cls}">${html}</body></html>`;
    // `html` is only the starting content while editing: the frame isn't rebuilt on every keystroke
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [css, bodyClass, theme, editable, editable ? null : html]);

  // the frame grows to its content: measured after load, after pictures load and while it changes
  useEffect(() => {
    const frame = ref.current;
    if (!frame) return;
    let last = 0;
    const measure = () => {
      const doc = frame.contentDocument;
      if (!doc?.documentElement) return;
      const h = Math.max(doc.documentElement.scrollHeight, doc.body?.scrollHeight ?? 0);
      if (h && Math.abs(h - last) > 1) {
        last = h;
        setHeight(h);
      }
    };
    // editing: changes are noticed by comparing the page (WebKit — Telegram on iPhone — doesn't deliver the frame's
    // events to the app's listeners while the frame may not run scripts)
    let seen: string | null = null;
    const watch = () => {
      measure();
      if (!editable) return;
      const body = frame.contentDocument?.body;
      if (!body || body.contentEditable !== "true") return;
      const now = body.innerHTML;
      if (seen !== null && now !== seen) onInputRef.current?.(now);
      seen = now;
    };
    const timer = setInterval(watch, 400);
    window.addEventListener("resize", measure);
    return () => {
      clearInterval(timer);
      window.removeEventListener("resize", measure);
    };
  }, [ref, srcDoc, editable]);

  const onLoad = () => {
    const frame = ref.current;
    const doc = frame?.contentDocument;
    if (!frame || !doc?.body) return;
    setHeight(Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight));
    doc.addEventListener("load", () => setHeight(doc.documentElement.scrollHeight), true);  // pictures
    if (editable) {
      doc.body.contentEditable = "true";
      doc.body.spellcheck = true;
      try {
        doc.execCommand("styleWithCSS", false, "true");
        doc.execCommand("defaultParagraphSeparator", false, "p");
      } catch {
        /* older engines */
      }
      doc.addEventListener("input", () => onInputRef.current?.(doc.body.innerHTML));  // Chromium; WebKit — the poll above
    }
    doc.addEventListener("click", (e) => {
      const a = (e.target as Element | null)?.closest?.("a");
      const href = a?.getAttribute("href");
      if (!a || !href) return;
      e.preventDefault();
      if (editable) return;  // editing: a link is text to change, not a way out
      if (href.startsWith("#")) {
        const target = doc.getElementById(decodeURIComponent(href.slice(1)));
        if (target) {
          const top = frame.getBoundingClientRect().top + target.getBoundingClientRect().top + window.scrollY - 72;
          window.scrollTo({ top, behavior: "smooth" });
        }
        return;
      }
      if (/^(https?:|mailto:)/i.test(href)) openExternal(href);
    });
  };

  return (
    <iframe
      ref={ref}
      title={title ?? "Методичка"}
      srcDoc={srcDoc}
      // no allow-scripts: nothing in a manual can run. allow-same-origin only lets the app measure and edit the page;
      // popups — so a link opens outside where the app can't catch the click (WebKit)
      sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
      onLoad={onLoad}
      scrolling="no"
      className={className}
      style={{ width: "100%", height, border: 0, display: "block", colorScheme: "normal" }}
    />
  );
}

type Theme = { fg: string; bg: string; muted: string; border: string; link: string } | null;

/** A manual without its own styles takes the app's colours (light or dark); one with styles — a white page. */
function useTheme(follow: boolean): Theme {
  const [theme, setTheme] = useState<Theme>(null);
  useEffect(() => {
    if (!follow) {
      setTheme(null);
      return;
    }
    const read = () => {
      const s = getComputedStyle(document.documentElement);
      const v = (name: string, fallback: string) => s.getPropertyValue(name).trim() || fallback;
      setTheme({ fg: v("--foreground", "#161a33"), bg: v("--card", "#ffffff"), muted: v("--muted-foreground", "#6b7090"),
        border: v("--border", "#e3e5ee"), link: v("--foreground", "#161a33") });
    };
    read();
    const watch = new MutationObserver(read);
    watch.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style"] });
    return () => watch.disconnect();
  }, [follow]);
  return theme;
}

function base(t: Theme, editable: boolean): string {
  const fg = t?.fg ?? "#1a1d2e";
  const bg = t?.bg ?? "#ffffff";
  const border = t?.border ?? "#dfe1ea";
  return `html,body{margin:0}body{padding:16px 18px;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,` +
    `"Helvetica Neue",Arial,sans-serif;color:${fg};background:${bg};overflow-wrap:anywhere;-webkit-text-size-adjust:100%}` +
    `img{max-width:100%;height:auto}table{border-collapse:collapse;max-width:100%}td,th{border:1px solid ${border};` +
    `padding:6px 8px;vertical-align:top}a{color:inherit;text-decoration:underline}pre{white-space:pre-wrap}` +
    `h1,h2,h3,h4{line-height:1.25;margin:1em 0 .5em}h1:first-child,h2:first-child{margin-top:0}blockquote{margin:0;` +
    `padding-left:12px;border-left:4px solid ${border}}` +
    (editable ? `body{outline:none;min-height:240px}body:focus{outline:none}` : "");
}
