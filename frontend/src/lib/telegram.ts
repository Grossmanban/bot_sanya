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
  /** Bot API 8.0: a shortcut on the phone's home screen */
  addToHomeScreen?: () => void;
  checkHomeScreenStatus?: (cb: (status: "unsupported" | "unknown" | "added" | "missed") => void) => void;
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

/** «Как в Telegram» follows the client's night mode; light/dark are the person's own choice on this device. */
export type ThemePref = "auto" | "light" | "dark";
const THEME_KEY = "vykhod-theme";
let reapplyTheme: (() => void) | null = null;
let memoryPref: ThemePref | null = null; // the choice made in this session (storage may be unavailable)

export function getThemePref(): ThemePref {
  if (memoryPref) return memoryPref;
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
}

export function setThemePref(pref: ThemePref) {
  memoryPref = pref;
  try {
    if (pref === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  } catch {
    /* private mode: the choice lasts until the app is closed */
  }
  reapplyTheme?.();
}

function systemScheme(): "light" | "dark" {
  const tg = webApp();
  if (tg) return tg.colorScheme;
  return typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function setupWebApp(onScheme: (scheme: "light" | "dark") => void): () => void {
  const tg = webApp();
  const apply = () => {
    const pref = getThemePref();
    const scheme = pref === "auto" ? systemScheme() : pref;
    // switch the class first so the colours read below belong to the new scheme
    document.documentElement.classList.toggle("dark", scheme === "dark");
    onScheme(scheme);
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--background").trim();
    if (tg && bg && tg.isVersionAtLeast("6.1")) {
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
  reapplyTheme = apply;
  if (!tg) {
    apply();
    return () => {
      reapplyTheme = null;
    };
  }
  tg.ready();
  tg.expand();
  try {
    tg.disableVerticalSwipes?.();
    // phones get true fullscreen (Bot API 8.0+); content is padded by Telegram's safe-area CSS vars
    if (["ios", "android"].includes(tg.platform) && tg.isVersionAtLeast("8.0")) tg.requestFullscreen?.();
  } catch {
    /* not supported */
  }
  apply();
  tg.onEvent("themeChanged", apply);
  return () => {
    reapplyTheme = null;
    tg.offEvent("themeChanged", apply);
  };
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

export type HomeScreenStatus = "unsupported" | "unknown" | "added" | "missed";

/** Whether the client can put the Mini App on the home screen, and whether it is there already. */
export function homeScreenStatus(): Promise<HomeScreenStatus> {
  const tg = webApp();
  return new Promise((resolve) => {
    if (!tg?.checkHomeScreenStatus || !tg.isVersionAtLeast("8.0")) return resolve("unsupported");
    try {
      tg.checkHomeScreenStatus((status) => resolve(status));
    } catch {
      resolve("unsupported");
    }
    setTimeout(() => resolve("unknown"), 1500); // older clients never answer
  });
}

export function addToHomeScreen(onAdded: () => void): () => void {
  const tg = webApp();
  if (!tg?.addToHomeScreen) return () => {};
  tg.onEvent("homeScreenAdded", onAdded);
  tg.addToHomeScreen();
  return () => tg.offEvent("homeScreenAdded", onAdded);
}
