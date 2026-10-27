"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Camera as CameraIcon, Check, LoaderCircle, RotateCcw, VideoOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type State = "idle" | "starting" | "live" | "shot" | "denied" | "unsupported" | "error";

const MESSAGES: Partial<Record<State, string>> = {
  denied: "Нет доступа к камере. Разрешите Telegram доступ к камере в настройках телефона и нажмите «Включить камеру» ещё раз.",
  unsupported: "Этот Telegram не даёт приложению камеру. Обновите Telegram или откройте приложение с телефона — "
    + "фото делается только здесь, внутри приложения.",
  error: "Камера не запустилась. Закройте другие приложения, которые её используют, и попробуйте снова.",
};

/** ТЗ 27.10 §34: the camera inside the Mini App (no jump to the phone's camera app): shoot → «Переснять» / «Прикрепить фото».
 * Whatever was entered before stays on the server — a failed camera loses nothing. */
export function Camera({ onAttach, busy }: { onAttach: (photo: Blob) => void; busy?: boolean }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [state, setState] = useState<State>("idle");
  const [shot, setShot] = useState<{ blob: Blob; url: string } | null>(null);

  const stop = useCallback(() => {
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }, []);

  useEffect(() => () => stop(), [stop]);
  useEffect(() => () => { if (shot) URL.revokeObjectURL(shot.url); }, [shot]);

  async function start() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setState("unsupported");
      return;
    }
    setState("starting");
    try {
      stream.current = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false,
      });
      setState("live");
      requestAnimationFrame(() => {
        if (video.current && stream.current) {
          video.current.srcObject = stream.current;
          void video.current.play().catch(() => undefined);
        }
      });
    } catch (e) {
      const name = e instanceof DOMException ? e.name : "";
      setState(name === "NotAllowedError" || name === "SecurityError" ? "denied"
        : name === "NotFoundError" || name === "OverconstrainedError" ? "unsupported" : "error");
    }
  }

  function capture() {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    const scale = Math.min(1, 1920 / Math.max(v.videoWidth, v.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(v.videoWidth * scale);
    canvas.height = Math.round(v.videoHeight * scale);
    canvas.getContext("2d")!.drawImage(v, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) return;
      stop();
      setShot({ blob, url: URL.createObjectURL(blob) });
      setState("shot");
    }, "image/jpeg", 0.85);
  }

  function retake() {
    setShot(null);
    void start();
  }

  return (
    <div className="space-y-3">
      <div className="relative aspect-[3/4] w-full overflow-hidden rounded-2xl bg-black">
        {state === "live" && <video ref={video} playsInline muted autoPlay className="size-full object-cover" />}
        {state === "shot" && shot && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={shot.url} alt="Снимок" className="size-full object-contain" />
        )}
        {(state === "idle" || state === "starting") && (
          <div className="grid size-full place-items-center text-white/70">
            {state === "starting" ? <LoaderCircle className="size-8 animate-spin" /> : <CameraIcon className="size-10" />}
          </div>
        )}
        {MESSAGES[state] && (
          <div className="grid size-full place-items-center p-6 text-center text-sm text-white/90">
            <span><VideoOff className="mx-auto mb-3 size-8" />{MESSAGES[state]}</span>
          </div>
        )}
      </div>
      {state === "live" ? (
        <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" onClick={capture}>
          <CameraIcon /> Сделать фото
        </Button>
      ) : state === "shot" ? (
        <div className="grid grid-cols-2 gap-2">
          <Button variant="secondary" className="press h-12 rounded-2xl" disabled={busy} onClick={retake}>
            <RotateCcw /> Переснять
          </Button>
          <Button className={cn("press h-12 rounded-2xl font-semibold")} disabled={busy || !shot}
                  onClick={() => shot && onAttach(shot.blob)}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Check />} Прикрепить фото
          </Button>
        </div>
      ) : (
        <Button variant="secondary" className="press h-12 w-full rounded-2xl text-[15px] font-semibold"
                disabled={state === "starting"} onClick={start}>
          <CameraIcon /> Включить камеру
        </Button>
      )}
    </div>
  );
}
