// Minimal typing for the parts of Telegram.WebApp we use.
// https://core.telegram.org/bots/webapps#initializing-mini-apps

type HapticNotification = "success" | "error" | "warning";

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: {
    user?: { id: number; first_name?: string; last_name?: string; username?: string; allows_write_to_pm?: boolean };
    start_param?: string;
  };
  /** Bot API 6.9: ask the person to let the bot write to them */
  requestWriteAccess?: (cb: (granted: boolean) => void) => void;
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

/**
 * The bot must be able to write to the person (training, reminders, shifts). Someone who opened the Mini App
 * without pressing «Старт» in the chat is asked for it. true — can write, false — refused,
 * null — an old client that can't ask (go on: the staff are warned if the bot can't reach them).
 */
export function ensureWriteAccess(): Promise<boolean | null> {
  const app = webApp();
  if (!app) return Promise.resolve(null);
  if (app.initDataUnsafe.user?.allows_write_to_pm) return Promise.resolve(true);
  if (!app.requestWriteAccess || !app.isVersionAtLeast("6.9")) return Promise.resolve(null);
  return new Promise((resolve) => {
    try {
      app.requestWriteAccess!((granted) => resolve(granted));
    } catch {
      resolve(null);
    }
  });
}

/** The chat with the bot; a referral code rides along so /start counts it. */
export function openBotChat(botUsername: string) {
  const ref = webApp()?.initDataUnsafe.start_param;
  const url = `https://t.me/${botUsername}?start=${ref?.startsWith("ref_") ? ref : "app"}`;
  try {
    webApp()?.openTelegramLink(url);
  } catch {
    window.open(url, "_blank");
  }
}

let botName: string | null = null;
/** The bot's username, for «open the chat with the bot» (set once /api/me answers). */
export function setBotUsername(name: string | null | undefined) {
  botName = name ?? null;
}
export function botUsername(): string | null {
  return botName;
}

export type ProfileTarget = { telegramId: number; name?: string | null };
let profileFallback: ((p: ProfileTarget) => void) | null = null;
/** The app shell registers the sheet shown for people without a username (ТЗ 28.10 §17). */
export function onProfileFallback(fn: ((p: ProfileTarget) => void) | null) {
  profileFallback = fn;
}

/** ТЗ 28.10 §17: the one way to open a person's Telegram profile, always by the Telegram ID (never the database id).
 * With a username — t.me/<username>, which every client opens. Without one Telegram guarantees nothing, and
 * tg://user?id does nothing inside the Mini App on iOS/Android — so an honest sheet opens instead: the ID to copy
 * and a card sent to the chat with the bot (whose button works when the person's privacy allows it). */
export function openProfile(username: string | null | undefined, telegramId?: number | null, name?: string | null) {
  const app = webApp();
  if (username) {
    const url = `https://t.me/${username}`;
    if (app) app.openTelegramLink(url);
    else window.open(url, "_blank");
    return;
  }
  if (!telegramId) return;
  profileFallback?.({ telegramId, name });
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
