"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Camera, Film, ImageOff, LoaderCircle, Play, X } from "lucide-react";
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

/** Full-screen video (a prize); tap outside the player to close. */
function VideoViewer({ src, onClose }: { src: string; onClose: () => void }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div role="dialog" aria-label="Видео" onClick={onClose}
         className="screen-in fixed inset-0 z-[100] grid place-items-center bg-black/90 p-4 pt-safe pb-safe">
      {broken ? (
        <p className="max-w-xs text-center text-sm text-white/80">Видео не открылось в приложении — оно есть в чате с ботом.</p>
      ) : (
        <video src={src} controls autoPlay playsInline onClick={(e) => e.stopPropagation()} onError={() => setBroken(true)}
               className="max-h-full max-w-full rounded-xl bg-black" />
      )}
      <button aria-label="Закрыть" className="absolute top-[calc(var(--safe-top)+12px)] right-4 grid size-10 place-items-center rounded-full bg-white/15 text-white">
        <X className="size-5" />
      </button>
    </div>,
    document.body,
  );
}

/** A video tile with ▶ that plays full screen. */
export function VideoThumb({ src, label, className }: { src: string | null | undefined; label?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  if (!src) return null;
  return (
    <>
      <button type="button" onClick={() => { tapHaptic(); setOpen(true); }} aria-label={label ? `Видео: ${label}` : "Открыть видео"}
              className={cn("press relative grid size-20 shrink-0 place-items-center overflow-hidden rounded-xl bg-gradient-to-br from-zinc-700 to-zinc-900 text-white", className)}>
        <span className="grid size-9 place-items-center rounded-full bg-white/20 backdrop-blur"><Play className="ml-0.5 size-4 fill-current" /></span>
        {label && <span className="absolute inset-x-0 bottom-0 bg-black/45 py-0.5 text-center text-[10px] font-semibold text-white">{label}</span>}
      </button>
      {open && <VideoViewer src={src} onClose={() => setOpen(false)} />}
    </>
  );
}

/** Pick one video (MP4/MOV up to 50 MB — what Telegram takes from a bot), with a preview. */
export function VideoPicker({ value, onChange, label = "Добавить видео" }: {
  value: File | null; onChange: (f: File | null) => void; label?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!value) {
      setUrl(null);
      return;
    }
    const u = URL.createObjectURL(value);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [value]);
  const pick = (file: File | undefined) => {
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      toast.error("Видео больше 50 МБ — Telegram не примет его от бота. Сожмите или обрежьте");
      return;
    }
    onChange(file);
  };
  return (
    <div>
      <input ref={input} type="file" accept="video/mp4,video/quicktime,video/*" className="hidden"
             onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
      {value && url ? (
        <div className="relative overflow-hidden rounded-2xl bg-black">
          <video src={url} controls playsInline className="max-h-64 w-full" />
          <button type="button" aria-label="Убрать видео" onClick={() => onChange(null)}
                  className="absolute top-2 right-2 grid size-9 place-items-center rounded-full bg-black/60 text-white">
            <X className="size-4" />
          </button>
          <p className="bg-card px-3 py-2 text-xs text-muted-foreground">{value.name} · {(value.size / 1024 / 1024).toFixed(1)} МБ</p>
        </div>
      ) : (
        <button type="button" onClick={() => input.current?.click()}
                className="press flex h-14 w-full items-center justify-center gap-2 rounded-2xl border-2 border-dashed text-[15px] font-semibold text-muted-foreground">
          <Film className="size-5" /> {label} <span className="font-normal">(до 50 МБ)</span>
        </button>
      )}
    </div>
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
