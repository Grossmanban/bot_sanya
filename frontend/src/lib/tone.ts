/** ТЗ 28.10 §27 (согласовано 29.10): one colour scale for every status chip — «Алиса», обучение, профили, выплаты.
 *  wait — ждёт (чьего-то действия), ok — готово / в порядке, bad — отказ или ошибка, archive — архив, завершено без итога. */
export type Tone = "wait" | "ok" | "bad" | "archive";

export const TONE_CHIP: Record<Tone, string> = {
  wait: "bg-wait-soft text-wait",
  ok: "bg-go-soft text-go",
  bad: "bg-cancel-soft text-cancel",
  archive: "bg-muted text-muted-foreground",
};

export const TONE_TEXT: Record<Tone, string> = {
  wait: "text-wait",
  ok: "text-go",
  bad: "text-cancel",
  archive: "text-muted-foreground",
};
