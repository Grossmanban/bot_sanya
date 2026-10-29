/** ТЗ 29.10 §7: the label above the role (the «выход» wordmark), set in «Настройки». The last value from /api/me is
 * remembered on the device so the loading and access screens show the same label before /api/me answers. */
export const HEADER_DEFAULT = "выход";
const KEY = "header-title";

/** undefined — not known yet (use the remembered one), null — hidden, string — the label. */
export function rememberHeader(value: string | null | undefined) {
  if (value === undefined) return;
  try {
    localStorage.setItem(KEY, JSON.stringify(value));
  } catch {
    /* storage blocked */
  }
}

export function rememberedHeader(): string | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw === null ? HEADER_DEFAULT : (JSON.parse(raw) as string | null);
  } catch {
    return HEADER_DEFAULT;
  }
}
