"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Camera, ImageOff, LoaderCircle, X } from "lucide-react";
import { toast } from "sonner";
import { compressImage } from "@/lib/image";
import { tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";

/** Full-screen view of a screenshot; tap anywhere to close. */
function Viewer({ src, onClose }: { src: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div role="dialog" aria-label="Скриншот" onClick={onClose}
         className="screen-in fixed inset-0 z-[100] grid place-items-center bg-black/90 p-4 pt-safe pb-safe">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt="Скриншот отчёта" className="max-h-full max-w-full rounded-xl object-contain" />
      <button aria-label="Закрыть" className="absolute top-[calc(var(--safe-top)+12px)] right-4 grid size-10 place-items-center rounded-full bg-white/15 text-white">
        <X className="size-5" />
      </button>
    </div>,
    document.body,
  );
}

/** Small preview of a report screenshot that opens full screen. */
export function PhotoThumb({ src, label, className }: { src: string | null | undefined; label?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const [broken, setBroken] = useState(false);
  if (!src) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => {
          tapHaptic();
          setOpen(true);
        }}
        className={cn("press relative block size-20 shrink-0 overflow-hidden rounded-xl bg-muted", className)}
        aria-label={label ?? "Открыть скриншот"}
      >
        {broken ? (
          <ImageOff className="m-auto size-5 text-muted-foreground" />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt="" loading="lazy" onError={() => setBroken(true)} className="size-full object-cover" />
        )}
        {label && <span className="absolute inset-x-0 bottom-0 bg-black/45 py-0.5 text-center text-[10px] font-semibold text-white">{label}</span>}
      </button>
      {open && <Viewer src={src} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Pick one screenshot (JPG/PNG/WebP), compressed on the device. */
export function PhotoPicker({
  value,
  onChange,
  label = "Прикрепить скриншот",
  optional,
}: {
  value: Blob | null;
  onChange: (b: Blob | null) => void;
  label?: string;
  optional?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (!value) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(value);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [value]);

  async function pick(file: File | undefined) {
    if (!file) return;
    setWorking(true);
    try {
      onChange(await compressImage(file));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Не удалось прочитать файл");
    } finally {
      setWorking(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div>
      <input ref={input} type="file" accept="image/jpeg,image/png,image/webp,image/*" className="hidden"
             onChange={(e) => pick(e.target.files?.[0])} />
      {preview ? (
        <div className="flex items-center gap-3">
          <PhotoThumb src={preview} className="size-16" />
          <div className="flex-1 text-sm">
            <p className="font-semibold">Скриншот прикреплён</p>
            <div className="mt-1 flex gap-3">
              <button type="button" className="font-semibold text-muted-foreground underline-offset-2 hover:underline" onClick={() => input.current?.click()}>
                Заменить
              </button>
              <button type="button" className="font-semibold text-cancel underline-offset-2 hover:underline" onClick={() => onChange(null)}>
                Убрать
              </button>
            </div>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => input.current?.click()}
          disabled={working}
          className="press flex h-16 w-full items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-border bg-card text-[15px] font-semibold text-muted-foreground hover:text-foreground"
        >
          {working ? <LoaderCircle className="size-5 animate-spin" /> : <Camera className="size-5" />}
          {working ? "Обрабатываю…" : label}
          {optional && !working && <span className="font-normal">(необязательно)</span>}
        </button>
      )}
    </div>
  );
}
