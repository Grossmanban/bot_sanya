"use client";

import { useEffect, useMemo, useState } from "react";
import { Archive, FileSpreadsheet, LoaderCircle, Plus, Send } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type Point, type ScheduleSettings, type SettingsResponse, type User } from "@/lib/api";
import { WEEKDAYS, plural } from "@/lib/format";
import { useCan } from "@/lib/caps";
import { useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { GlobalLinkCard, HeaderTitleCard, ReasonsCard, TextsCard } from "./templates-section";

const field = "h-12 w-full rounded-2xl border-0 bg-muted px-4 outline-none focus:ring-2 focus:ring-ring/25";

/* ---------------------------------------------------------------- Excel export */

export function ExportCard({ from, to, user }: { from: string; to: string; user: User | null }) {
  const { busy, run } = useAction();
  return (
    <section className="flex flex-wrap items-center gap-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <span className="grid size-11 place-items-center rounded-2xl bg-go-soft text-go">
        <FileSpreadsheet className="size-5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-semibold">Выгрузка в Excel</p>
        <p className="text-sm text-muted-foreground">
          {user ? `${user.first_name} ${user.last_name}` : "Вся команда"}: итоги, смены и штрафы за выбранный период. Файл придёт в бот.
        </p>
      </div>
      <Button
        className="press h-11 w-full rounded-2xl font-semibold sm:w-auto sm:px-5"
        disabled={!!busy}
        onClick={async () => {
          try {
            await run("export", () => api.admin.exportXlsx(from, to, user?.id), "Файл отправлен в бот");
          } catch {
            /* toast shown */
          }
        }}
      >
        {busy ? <LoaderCircle className="animate-spin" /> : <Send />} Отправить файл
      </Button>
    </section>
  );
}

/* ---------------------------------------------------------------- points */

export function PointsSection() {
  const { data, reload } = useLoad(() => api.admin.points(), []);
  const [name, setName] = useState("");
  const [capacity, setCapacity] = useState("");
  const { busy, run } = useAction();

  async function add() {
    if (!name.trim()) {
      toast.error("Напишите название точки");
      return;
    }
    try {
      await run("add", () => api.admin.createPoint(name.trim(), capacity ? Number(capacity) : null), "Точка добавлена");
      setName("");
      setCapacity("");
      await reload();
    } catch {
      /* toast shown */
    }
  }

  const active = data?.items.filter((p) => p.active) ?? [];
  const hidden = data?.items.filter((p) => !p.active) ?? [];

  return (
    <div className="space-y-4">
      <section className="space-y-4 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div>
          <h2 className="font-semibold">Новая точка</h2>
          <p className="text-sm text-muted-foreground">
            Когда в списке есть точки, сотрудники выбирают точку из него, а не вводят текстом. Лимит — сколько человек может выйти на точку в один день; пусто — без лимита.
          </p>
        </div>
        <div className="grid grid-cols-[1fr_7rem] gap-2">
          <input className={field} placeholder="Название, например Европа" maxLength={100} value={name}
                 onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
          <input className={cn(field, "text-center")} inputMode="numeric" placeholder="Лимит" aria-label="Лимит людей, пусто — без лимита" value={capacity}
                 onChange={(e) => setCapacity(e.target.value.replace(/\D/g, "").slice(0, 3))} />
        </div>
        <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!!busy} onClick={add}>
          {busy === "add" ? <LoaderCircle className="animate-spin" /> : <Plus />} Добавить точку
        </Button>
      </section>

      {!data ? (
        <Skeleton className="h-40 rounded-3xl" />
      ) : (
        <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
          <h2 className="px-5 pt-5 pb-2 font-semibold md:px-6">
            Точки <span className="text-muted-foreground">{active.length}</span>
          </h2>
          {active.length === 0 && (
            <p className="px-5 pb-5 text-sm text-muted-foreground md:px-6">Пока точек нет: сотрудники вводят точку текстом.</p>
          )}
          <ul className="divide-y">
            {[...active, ...hidden].map((p) => (
              <PointRow key={p.id} point={p} onChanged={reload} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function PointRow({ point, onChanged }: { point: Point; onChanged: () => Promise<void> }) {
  const [name, setName] = useState(point.name);
  const [capacity, setCapacity] = useState(point.capacity ? String(point.capacity) : "");
  const { busy, run } = useAction();
  const dirty = name.trim() !== point.name || capacity !== (point.capacity ? String(point.capacity) : "");

  async function save(body: Parameters<typeof api.admin.updatePoint>[1], success: string) {
    try {
      await run("save", () => api.admin.updatePoint(point.id, body), success);
      await onChanged();
    } catch {
      /* toast shown */
    }
  }

  return (
    <li className={cn("space-y-2 px-5 py-3.5 md:px-6", !point.active && "opacity-55")}>
      <div className="grid grid-cols-[1fr_5.5rem] gap-2">
        <input className={cn(field, "h-11")} value={name} onChange={(e) => setName(e.target.value)} aria-label="Название точки" />
        <input className={cn(field, "h-11 text-center")} inputMode="numeric" placeholder="∞" value={capacity}
               aria-label="Лимит людей" onChange={(e) => setCapacity(e.target.value.replace(/\D/g, "").slice(0, 3))} />
      </div>
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <Switch
            checked={!!point.active}
            onCheckedChange={(on) => save({ active: on }, on ? "Точка снова в списке" : "Точка скрыта")}
          />
          {point.active ? "в списке" : "скрыта"}
        </label>
        {dirty && (
          <Button size="sm" className="press h-9 rounded-xl px-4" disabled={!!busy}
                  onClick={() =>
                    save(
                      capacity ? { name: name.trim(), capacity: Number(capacity) } : { name: name.trim(), clear_capacity: true },
                      "Точка сохранена",
                    )
                  }>
            {busy && <LoaderCircle className="animate-spin" />} Сохранить
          </Button>
        )}
      </div>
    </li>
  );
}

/* ---------------------------------------------------------------- scheduled messages */

type Row = {
  title: string;
  text: string;
  enabled: keyof ScheduleSettings;
  time?: keyof ScheduleSettings;
  weekday?: keyof ScheduleSettings;
  delay?: keyof ScheduleSettings;
  /** minutes before the report deadline */
  lead?: keyof ScheduleSettings;
  /** who receives it */
  audience?: keyof ScheduleSettings;
  /** work days a week (ТЗ 12.4) */
  days?: keyof ScheduleSettings;
  /** a second time: the deadline after the reminder */
  deadline?: keyof ScheduleSettings;
  fixedDay?: string;
};

const AUDIENCES: { value: ScheduleSettings["report_digest_to"]; label: string }[] = [
  { value: "owners", label: "Владельцам" },
  { value: "admins", label: "Владельцам и администраторам" },
  { value: "staff", label: "+ тренерам (по своей сети)" },
];

const ROWS: Row[] = [
  { title: "Напоминание накануне", text: "Сотруднику: завтра выход, время, точка и кто ещё на точке.", enabled: "eve_reminder_enabled", time: "eve_reminder_time" },
  { title: "Распред на завтра", text: "Админам: готовый текст распреда для рабочего чата.", enabled: "auto_raspred_enabled", time: "auto_raspred_time" },
  { title: "График на следующую неделю", text: "Тем, кто работает, но ещё ничего не отметил на следующую неделю.", enabled: "week_reminder_enabled", time: "week_reminder_time", weekday: "week_reminder_weekday" },
  { title: "Запрос отчёта", text: "После конца смены бот попросит факт и скриншот.", enabled: "fact_request_enabled", delay: "fact_request_delay" },
  { title: "Напоминание об отчёте", text: "Сотруднику, если отчёт ещё не сдан: за отсутствие отчёта может быть штраф.", enabled: "report_reminder_enabled", lead: "report_reminder_lead" },
  { title: "Кто не сдал отчёт", text: "Список несданных отчётов за день, ожидающих проверки и неявок.", enabled: "report_digest_enabled", time: "report_digest_time", audience: "report_digest_to" },
  { title: "Общий факт: вечером", text: "Владельцам, если общий факт за сегодня ещё не введён.", enabled: "global_actual_evening_enabled", time: "global_actual_evening_time" },
  { title: "Общий факт: утром", text: "Владельцам, если общий факт за вчера так и не введён.", enabled: "global_actual_morning_enabled", time: "global_actual_morning_time" },
  { title: "Итоги недели", text: "Админам: план и факт по каждому и файл Excel за прошлую неделю.", enabled: "weekly_report_enabled", time: "weekly_report_time", fixedDay: "по понедельникам" },
  { title: "Общая норма выходов", text: "Кто за прошлую неделю вышел меньше нормы — заявка в «Дисциплину». Личная норма задаётся в карточке сотрудника («Команда») и проверяется, даже если общая выключена.", enabled: "attendance_check_enabled", time: "attendance_check_time", fixedDay: "по понедельникам", days: "weekly_attendance_quota" },
  { title: "Отметка графика на завтра", text: "Тем, у кого «Работаю», а на завтра нет ни смены, ни выходного: напоминание. Кто не отметит к сроку — нарушение в «Дисциплину» (по умолчанию 3 штрафные единицы × ставка или «Простить»). «Не работаю» не проверяется. В тот же срок фиксируется смена на завтра: объём и статус больше не меняются, время и точка — до 00:00.", enabled: "daily_mark_enabled", time: "daily_mark_reminder_time", deadline: "daily_mark_deadline" },
  { title: "Резервная копия базы", text: "Владельцам: файл базы данных каждый день.", enabled: "backup_enabled", time: "backup_time" },
];

export function SettingsSection() {
  const canRates = useCan("money"); // rates are money: owners only
  const { data } = useLoad(() => api.admin.settings(), []);
  const [cfg, setCfg] = useState<ScheduleSettings | null>(null);
  const { busy, run } = useAction();
  useEffect(() => {
    if (data) setCfg(data.settings);
  }, [data]);

  const dirty = useMemo(() => !!cfg && !!data && JSON.stringify(cfg) !== JSON.stringify(data.settings), [cfg, data]);

  if (!cfg || !data) return <Skeleton className="h-96 rounded-3xl" />;
  const set = (patch: Partial<ScheduleSettings>) => setCfg({ ...cfg, ...patch });

  async function save() {
    try {
      const res = await run("save", () => api.admin.saveSettings(cfg!), "Настройки сохранены");
      if (res) setCfg(res.settings);
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-3xl bg-card shadow-lift">
        <div className="px-5 pt-5 pb-1 md:px-6">
          <h2 className="font-semibold">Автоматические сообщения</h2>
          <p className="text-sm text-muted-foreground">Время московское. Каждое сообщение уходит один раз в день.</p>
        </div>
        <ul className="divide-y">
          {ROWS.map((r) => {
            const on = cfg[r.enabled] as boolean;
            return (
              <li key={r.enabled} className="space-y-3 px-5 py-4 md:px-6">
                <label className="flex cursor-pointer items-start gap-4">
                  <span className="flex-1">
                    <span className="block font-medium">{r.title}</span>
                    <span className="block text-sm text-muted-foreground">{r.text}</span>
                  </span>
                  <Switch checked={on} onCheckedChange={(v) => set({ [r.enabled]: v,
                    ...(r.days && v && !cfg[r.days] ? { [r.days]: 3 } : {}) } as Partial<ScheduleSettings>)} />
                </label>
                {on && (
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    {r.weekday && (
                      <select className="h-10 rounded-xl border-0 bg-muted px-3 outline-none" value={cfg[r.weekday] as number}
                              onChange={(e) => set({ [r.weekday!]: Number(e.target.value) } as Partial<ScheduleSettings>)}>
                        {WEEKDAYS.map((w, i) => (
                          <option key={w} value={i}>{w}</option>
                        ))}
                      </select>
                    )}
                    {r.days && (
                      <label className="flex items-center gap-2 text-muted-foreground">
                        не меньше
                        <select className="h-10 rounded-xl border-0 bg-muted px-3 text-foreground outline-none" value={(cfg[r.days] as number) || 3}
                                onChange={(e) => set({ [r.days!]: Number(e.target.value) } as Partial<ScheduleSettings>)}>
                          {[1, 2, 3, 4, 5, 6, 7].map((n) => (
                            <option key={n} value={n}>{n}</option>
                          ))}
                        </select>
                        {plural((cfg[r.days] as number) || 3, "дня", "дней", "дней")} в неделю,
                      </label>
                    )}
                    {r.fixedDay && <span className="text-muted-foreground">{r.fixedDay}</span>}
                    {r.time && r.deadline && <span className="text-muted-foreground">напомнить в</span>}
                    {r.time && (
                      <input type="time" className="h-10 rounded-xl border-0 bg-muted px-3 tabular outline-none" value={cfg[r.time] as string}
                             onChange={(e) => e.target.value && set({ [r.time!]: e.target.value } as Partial<ScheduleSettings>)} />
                    )}
                    {r.deadline && (
                      <label className="flex items-center gap-2 text-muted-foreground">
                        срок до
                        <input type="time" className="h-10 rounded-xl border-0 bg-muted px-3 text-foreground tabular outline-none"
                               value={cfg[r.deadline] as string}
                               onChange={(e) => e.target.value && set({ [r.deadline!]: e.target.value } as Partial<ScheduleSettings>)} />
                      </label>
                    )}
                    {r.delay && (
                      <label className="flex items-center gap-2 text-muted-foreground">
                        через
                        <input inputMode="numeric" className="h-10 w-16 rounded-xl border-0 bg-muted text-center text-foreground tabular outline-none"
                               value={String(cfg[r.delay])}
                               onChange={(e) => set({ [r.delay!]: Math.min(240, Number(e.target.value.replace(/\D/g, "") || 0)) } as Partial<ScheduleSettings>)} />
                        мин после конца смены
                      </label>
                    )}
                    {r.lead && (
                      <label className="flex items-center gap-2 text-muted-foreground">
                        за
                        <select className="h-10 rounded-xl border-0 bg-muted px-3 text-foreground outline-none" value={cfg[r.lead] as number}
                                onChange={(e) => set({ [r.lead!]: Number(e.target.value) } as Partial<ScheduleSettings>)}>
                          {[30, 60, 120, 180, 240, 360].map((m) => (
                            <option key={m} value={m}>{m < 60 ? `${m} мин` : `${m / 60} ч`}</option>
                          ))}
                        </select>
                        до срока сдачи
                      </label>
                    )}
                    {r.audience && (
                      <select className="h-10 rounded-xl border-0 bg-muted px-3 outline-none" value={cfg[r.audience] as string}
                              onChange={(e) => set({ [r.audience!]: e.target.value } as Partial<ScheduleSettings>)}>
                        {AUDIENCES.map((a) => (
                          <option key={a.value} value={a.value}>{a.label}</option>
                        ))}
                      </select>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <label className="flex cursor-pointer items-start gap-4">
          <span className="flex-1">
            <span className="block font-medium">Имена коллег в графике</span>
            <span className="block text-sm text-muted-foreground">
              {cfg.show_colleague_names
                ? "Сотрудники видят, кто ещё выходит в их день."
                : "Сотрудники видят только, сколько человек выходит. Руководители видят имена всегда."}
            </span>
          </span>
          <Switch checked={cfg.show_colleague_names} onCheckedChange={(v) => set({ show_colleague_names: v })} />
        </label>
      </section>
      <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div className="flex items-start gap-4">
          <span className="flex-1">
            <span className="block font-medium">Архив целей и событий</span>
            <span className="block text-sm text-muted-foreground">
              {cfg.feed_hide_after_days
                ? `Завершённые уходят из ленты в «Архив» через ${cfg.feed_hide_after_days} ${plural(cfg.feed_hide_after_days, "день", "дня", "дней")} после окончания.`
                : "Завершённые уходят в «Архив» сразу после окончания."}
              {" "}Скрыть или вернуть вручную — глазом в «Целях» и «Событиях».
            </span>
          </span>
          <select aria-label="Через сколько дней в архив" className="h-10 rounded-xl border-0 bg-muted px-3 outline-none"
                  value={cfg.feed_hide_after_days} onChange={(e) => set({ feed_hide_after_days: Number(e.target.value) })}>
            {[0, 1, 2, 3, 5, 7, 14, 30].map((d) => <option key={d} value={d}>{d === 0 ? "сразу" : `${d} дн.`}</option>)}
          </select>
        </div>
      </section>
      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div>
          <span className="block font-medium">Стажировка и второй этап</span>
          <span className="block text-sm text-muted-foreground">
            Сколько единиц к оплате нужно для проверки. Стажировка — учебные смены, они не оплачиваются; второй этап — рабочие
            смены после стажировки. Засчитывается то количество, которое администратор подтвердил в отчётах.
          </span>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {([["internship_units", "Стажировка, ед."], ["second_stage_units", "Второй этап, ед."], ["training_cancel_limit", "Предупреждать после отмен обучения"]] as const).map(([k, label]) => (
            <label key={k} className="space-y-1">
              <span className="text-xs text-muted-foreground">{label}</span>
              <input inputMode="numeric" aria-label={label} className="h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] tabular outline-none focus:ring-2 focus:ring-ring/25"
                     value={cfg[k] || ""} onChange={(e) => set({ [k]: Number(e.target.value.replace(/\D/g, "").slice(0, 5)) || 0 } as Partial<ScheduleSettings>)} />
            </label>
          ))}
        </div>
      </section>
      <section className="space-y-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <div>
          <span className="block font-medium">Группы «Предзакреп» и «Основная команда»</span>
          <span className="block text-sm text-muted-foreground">
            С какого подтверждённого рабочего дня — в основную команду; сверка членства раз в день; автоматические личные приглашения
            (каждому — один раз на группу).
          </span>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">Основная команда с, дней</span>
            <input inputMode="numeric" aria-label="Основная команда с, дней" value={cfg.main_team_days || ""}
                   onChange={(e) => set({ main_team_days: Number(e.target.value.replace(/\D/g, "").slice(0, 3)) || 0 })}
                   className="h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] tabular outline-none focus:ring-2 focus:ring-ring/25" />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">Сверка в</span>
            <input type="time" aria-label="Сверка в" value={cfg.groups_check_time}
                   onChange={(e) => set({ groups_check_time: e.target.value })}
                   className="h-11 w-full rounded-xl border-0 bg-muted px-3 text-[15px] tabular outline-none focus:ring-2 focus:ring-ring/25" />
          </label>
        </div>
        <label className="flex cursor-pointer items-center gap-3">
          <span className="min-w-0 flex-1 text-sm">Приглашать автоматически</span>
          <Switch checked={cfg.groups_auto_invite} onCheckedChange={(v) => set({ groups_auto_invite: v })} />
        </label>
      </section>
      <section className="flex items-center justify-between gap-3 rounded-3xl bg-card p-5 shadow-lift md:p-6">
        <span className="min-w-0">
          <span className="block font-medium">Реферальные ссылки меняются</span>
          <span className="block text-sm text-muted-foreground">
            Старая ссылка и её QR-код перестают работать — у сотрудника в «Мои рефералы» таймер до смены.
          </span>
        </span>
        <select aria-label="Как часто меняются ссылки" className="h-10 rounded-xl border-0 bg-muted px-3 outline-none"
                value={cfg.link_ttl_minutes} onChange={(e) => set({ link_ttl_minutes: Number(e.target.value) })}>
          {[[30, "раз в 30 мин"], [60, "раз в час"], [180, "раз в 3 ч"], [720, "раз в 12 ч"], [1440, "раз в сутки"], [10080, "раз в неделю"]]
            .map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </section>
      <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!dirty || !!busy} onClick={save}>
        {busy && <LoaderCircle className="animate-spin" />} {dirty ? "Сохранить настройки" : "Всё сохранено"}
      </Button>
      {canRates && <RatesCard cfg={cfg} set={set} />}
      <ArchiveCard archive={data.archive} />
      <HeaderTitleCard />
      <GlobalLinkCard />
      <ReasonsCard />
      <TextsCard />
    </div>
  );
}

const ARCHIVE_TAGS = ["#нарушение", "#штраф", "#бонус", "#блок", "#пауза", "#роль", "#доступ", "#отчёт", "#выплата"];

function ArchiveCard({ archive }: { archive: SettingsResponse["archive"] }) {
  return (
    <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <div className="flex items-start gap-3">
        <span className={cn("grid size-10 shrink-0 place-items-center rounded-xl",
          archive.connected ? "bg-go-soft text-go" : "bg-muted text-muted-foreground")}>
          <Archive className="size-5" strokeWidth={1.8} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">Архивная группа</h2>
          <p className="text-sm text-muted-foreground">
            {archive.connected
              ? `Подключена: ${archive.title || "группа"}. Сюда уходят деньги, статусы и дисциплина.`
              : "Не подключена. События сохраняются в журнале и начнут уходить в группу после подключения."}
          </p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-1.5">
        {ARCHIVE_TAGS.map((t) => (
          <span key={t} className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">{t}</span>
        ))}
      </div>
      {!archive.connected && (
        <ol className="mt-4 list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
          <li>Создайте закрытую группу и добавьте в неё бота.</li>
          <li>Владелец отправляет в группе команду <code className="rounded bg-muted px-1.5 py-0.5 text-foreground">/archive_here</code>.</li>
          <li>Отключить — <code className="rounded bg-muted px-1.5 py-0.5 text-foreground">/archive_off</code> там же.</li>
        </ol>
      )}
    </section>
  );
}

type RateKey = "rate_base" | "rate_lazy" | "lazy_threshold" | "trainer_commission" | "instructor_commission" | "piece_cap"
  | "revenue_per_piece" | "alice_rate";
const RATES: { key: RateKey; label: string; unit: string }[] = [
  { key: "rate_base", label: "Ставка", unit: "₽ за шт." },
  { key: "rate_lazy", label: "Налог на лень: ставка", unit: "₽ за шт." },
  { key: "lazy_threshold", label: "Налог на лень, если меньше", unit: "шт. за день" },
  { key: "trainer_commission", label: "Тренеру с объёма сети", unit: "₽ за шт." },
  { key: "instructor_commission", label: "Инструктору с его людей", unit: "₽ за шт." },
  { key: "piece_cap", label: "Предел за штуку", unit: "₽ — ставка + тренер + инструктор" },
  { key: "revenue_per_piece", label: "Доход бизнеса за штуку", unit: "₽ — для заработка владельца" },
  { key: "alice_rate", label: "«Яндекс Алиса» и «Яндекс Плюс» за выполнение", unit: "₽ — одна ставка для обоих продуктов; проведённые выплаты не меняются" },
];

/** Piece rates (ТЗ 11.1): used by cards, stats, Excel and as defaults in the payout wizard. */
function RatesCard({ cfg, set }: { cfg: ScheduleSettings; set: (p: Partial<ScheduleSettings>) => void }) {
  return (
    <section className="rounded-3xl bg-card p-5 shadow-lift md:p-6">
      <h2 className="font-semibold">Ставки</h2>
      <p className="text-sm text-muted-foreground">Сохраняются кнопкой выше. В мастере выплаты их можно изменить для одной выплаты.</p>
      <div className="mt-4 grid grid-cols-2 gap-3">
        {RATES.map((r) => (
          <label key={r.key} className="space-y-1.5">
            <span className="text-xs text-muted-foreground">{r.label}</span>
            <span className="flex items-center gap-2">
              <input inputMode="numeric" className="h-11 w-24 rounded-xl border-0 bg-muted px-3 tabular outline-none focus:ring-2 focus:ring-ring/25"
                     value={String(cfg[r.key])}
                     onChange={(e) => set({ [r.key]: Math.min(100000, Number(e.target.value.replace(/\D/g, "") || 0)) } as Partial<ScheduleSettings>)} />
              <span className="text-sm text-muted-foreground">{r.unit}</span>
            </span>
          </label>
        ))}
      </div>
    </section>
  );
}
