// Minimal typing for the parts of Telegram.WebApp we use.
// https://core.telegram.org/bots/webapps#initializing-mini-apps

type HapticNotification = "success" | "error" | "warning";

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: { user?: { id: number; first_name?: string; last_name?: string; username?: string } };
  colorScheme: "light" | "dark";
  version: string;
  platform: string;
  isFullscreen?: boolean;
  requestFullscreen?: () => void;
  ready(): void;
  expand(): void;
  isVersionAtLeast(version: string): boolean;
  setHeaderColor(color: string): void;
  setBackgroundColor(color: string): void;
  onEvent(event: string, cb: () => void): void;
  offEvent(event: string, cb: () => void): void;
  openTelegramLink(url: string): void;
  disableVerticalSwipes?: () => void;
  HapticFeedback?: {
    notificationOccurred(type: HapticNotification): void;
    impactOccurred(style: "light" | "medium" | "heavy" | "rigid" | "soft"): void;
    selectionChanged(): void;
  };
  BackButton?: {
    show(): void;
    hide(): void;
    onClick(cb: () => void): void;
    offClick(cb: () => void): void;
  };
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function webApp(): TelegramWebApp | undefined {
  if (typeof window === "undefined") return undefined;
  return window.Telegram?.WebApp;
}

export function initData(): string {
  return webApp()?.initData ?? "";
}

export function haptic(type: HapticNotification) {
  try {
    webApp()?.HapticFeedback?.notificationOccurred(type);
  } catch {
    /* old clients */
  }
}

export function tapHaptic() {
  try {
    webApp()?.HapticFeedback?.selectionChanged();
  } catch {
    /* old clients */
  }
}

export function setupWebApp(onScheme: (scheme: "light" | "dark") => void): () => void {
  const tg = webApp();
  if (!tg) return () => {};
  tg.ready();
  tg.expand();
  try {
    tg.disableVerticalSwipes?.();
    // phones get true fullscreen (Bot API 8.0+); content is padded by Telegram's safe-area CSS vars
    if (["ios", "android"].includes(tg.platform) && tg.isVersionAtLeast("8.0")) tg.requestFullscreen?.();
  } catch {
    /* not supported */
  }
  const apply = () => {
    // switch the class first so the colours read below belong to the new scheme
    document.documentElement.classList.toggle("dark", tg.colorScheme === "dark");
    onScheme(tg.colorScheme);
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--background").trim();
    if (bg && tg.isVersionAtLeast("6.1")) {
      // Telegram expects hex; resolve the CSS colour through a canvas
      const hex = cssColorToHex(bg);
      if (hex) {
        try {
          tg.setHeaderColor(hex);
          tg.setBackgroundColor(hex);
        } catch {
          /* ignore */
        }
      }
    }
  };
  apply();
  tg.onEvent("themeChanged", apply);
  return () => tg.offEvent("themeChanged", apply);
}

function cssColorToHex(color: string): string | null {
  const ctx = document.createElement("canvas").getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
}

/** Show Telegram's native back button while `active`; returns a cleanup for useEffect. */
export function bindBackButton(active: boolean, onBack: () => void) {
  const back = webApp()?.BackButton;
  if (!back) return () => {};
  if (!active) {
    back.hide();
    return () => {};
  }
  back.show();
  back.onClick(onBack);
  return () => {
    back.offClick(onBack);
    back.hide();
  };
}

/** Open a person's Telegram profile: t.me/username, or tg://user?id= when they have no username. */
export function openChat(user: { username: string | null; telegram_id: number }) {
  if (user.username) {
    const link = `https://t.me/${user.username}`;
    const tg = webApp();
    if (tg) tg.openTelegramLink(link);
    else window.open(link, "_blank");
  } else {
    window.location.href = `tg://user?id=${user.telegram_id}`;
  }
}
