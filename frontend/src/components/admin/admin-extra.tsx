"use client";

import { useEffect, useMemo, useState } from "react";
import { Archive, FileSpreadsheet, LoaderCircle, Plus, Send } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api, type Point, type ScheduleSettings, type SettingsResponse, type User } from "@/lib/api";
import { WEEKDAYS } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { cn } from "@/lib/utils";

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
  fixedDay?: string;
};

const ROWS: Row[] = [
  { title: "Напоминание накануне", text: "Сотруднику: завтра выход, время, точка и кто ещё на точке.", enabled: "eve_reminder_enabled", time: "eve_reminder_time" },
  { title: "Распред на завтра", text: "Админам: готовый текст распреда для рабочего чата.", enabled: "auto_raspred_enabled", time: "auto_raspred_time" },
  { title: "График на следующую неделю", text: "Тем, кто работает, но ещё ничего не отметил на следующую неделю.", enabled: "week_reminder_enabled", time: "week_reminder_time", weekday: "week_reminder_weekday" },
  { title: "Запрос факта", text: "После конца смены бот спросит, сколько сделано.", enabled: "fact_request_enabled", delay: "fact_request_delay" },
  { title: "Итоги недели", text: "Админам: план и факт по каждому и файл Excel за прошлую неделю.", enabled: "weekly_report_enabled", time: "weekly_report_time", fixedDay: "по понедельникам" },
  { title: "Резервная копия базы", text: "Владельцам: файл базы данных каждый день.", enabled: "backup_enabled", time: "backup_time" },
];

export function SettingsSection() {
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
                  <Switch checked={on} onCheckedChange={(v) => set({ [r.enabled]: v } as Partial<ScheduleSettings>)} />
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
                    {r.fixedDay && <span className="text-muted-foreground">{r.fixedDay}</span>}
                    {r.time && (
                      <input type="time" className="h-10 rounded-xl border-0 bg-muted px-3 tabular outline-none" value={cfg[r.time] as string}
                             onChange={(e) => e.target.value && set({ [r.time!]: e.target.value } as Partial<ScheduleSettings>)} />
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
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>
      <Button className="press h-12 w-full rounded-2xl text-[15px] font-semibold" disabled={!dirty || !!busy} onClick={save}>
        {busy && <LoaderCircle className="animate-spin" />} {dirty ? "Сохранить настройки" : "Всё сохранено"}
      </Button>
      <ArchiveCard archive={data.archive} />
    </div>
  );
}

const ARCHIVE_TAGS = ["#штраф", "#бонус", "#блок", "#пауза", "#роль", "#доступ", "#отчёт", "#выплата"];

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
