"use client";

import { useEffect, useState } from "react";
import { HEADER_DEFAULT, rememberedHeader } from "@/lib/header";
import { cn } from "@/lib/utils";

/** ТЗ 29.10 §7: the label above the role — «выход» by default, any text or hidden from «Настройки»; the same font,
 * size and colour; a long one is cut with «…»; hidden — takes no space. `text` undefined — the remembered value. */
export function Wordmark({ className, text }: { className?: string; text?: string | null }) {
  const remembered = useRememberedHeader();
  const shown = text === undefined ? remembered : text;
  if (!shown) return null;
  return <span title={shown} className={cn("inline-block max-w-full truncate align-bottom font-display font-semibold tracking-tight", className)}>{shown}</span>;
}

function useRememberedHeader(): string | null {
  const [value, setValue] = useState<string | null>(HEADER_DEFAULT);  // the static HTML has the default
  useEffect(() => setValue(rememberedHeader()), []);
  return value;
}
