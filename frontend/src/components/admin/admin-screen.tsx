"use client";

import { useMemo, useState } from "react";
import { Copy, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, type AdminShift, type Shift, type User } from "@/lib/api";
import { STATUS_META, addDays, ddmm, parseDay, plural, rub, shortDate, timeRange, WEEKDAYS_SHORT } from "@/lib/format";
import { moscowToday, useLoad } from "@/lib/hooks";
import { haptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";
import { AdminShiftSheet, type EditableShift } from "./admin-shift-sheet";

export function AdminScreen() {
  const [editing, setEditing] = useState<EditableShift | null>(null);
  const [version, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);

  return (
    <div className="space-y-4">
      <Tabs defaultValue="raspred">
        <TabsList className="grid h-11 w-full grid-cols-4 rounded-xl bg-card shadow-sm">
          <TabsTrigger value="raspred" className="rounded-lg">Распред</TabsTrigger>
          <TabsTrigger value="day" className="rounded-lg">День</TabsTrigger>
          <TabsTrigger value="stats" className="rounded-lg">Стата</TabsTrigger>
          <TabsTrigger value="penalties" className="rounded-lg">Штрафы</TabsTrigger>
        </TabsList>
        <TabsContent value="raspred" className="mt-4">
          <RaspredTab version={version} />
        </TabsContent>
        <TabsContent value="day" className="mt-4">
          <DayTab version={version} onEdit={setEditing} />
        </TabsContent>
        <TabsContent value="stats" className="mt-4">
          <StatsTab version={version} onEdit={setEditing} />
        </TabsContent>
        <TabsContent value="penalties" className="mt-4">
          <PenaltiesTab version={version} onEdit={setEditing} />
        </TabsContent>
      </Tabs>
      <AdminShiftSheet shift={editing} onClose={() => setEditing(null)} onChanged={() => { setEditing(null); bump(); }} />
    </div>
  );
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  haptic("success");
  toast.success("Скопировано");
}

function RaspredTab({ version }: { version: number }) {
  const { data, loading, reload } = useLoad(() => api.admin.raspred(), [version]);
  if (!data) return <Skeleton className="h-48 rounded-2xl bg-card" />;
  return (
    <section className="space-y-3 rounded-2xl border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold">Распред на завтра</h2>
          <p className="text-sm text-muted-foreground">{shortDate(data.date)}</p>
        </div>
        <Button size="icon-sm" variant="ghost" onClick={reload} aria-label="Обновить">
          <RefreshCw className={cn(loading && "animate-spin")} />
        </Button>
      </div>
      {data.rows.length === 0 ? (
        <p className="rounded-xl bg-muted p-4 text-sm text-muted-foreground">На завтра нет сотрудников в графике.</p>
      ) : (
        <ul className="divide-y rounded-xl border">
          {data.rows.map((r) => (
            <li key={r.shift_id} className="flex items-center justify-between gap-2 px-3 py-2.5 text-sm">
              <div className="min-w-0">
                <p className="font-semibold">{r.start_time}–{r.end_time}</p>
                <p className="truncate text-muted-foreground">
                  {r.name}
                  {r.username ? ` (@${r.username})` : ""} · {r.location}
                </p>
              </div>
              <span className="shrink-0 font-semibold">{r.volume} шт.</span>
            </li>
          ))}
        </ul>
      )}
      <div className="grid grid-cols-2 gap-2 text-center">
        <Stat label={plural(data.total_count, "сотрудник", "сотрудника", "сотрудников")} value={String(data.total_count)} />
        <Stat label="плановый объём" value={`${data.total_volume} шт.`} />
      </div>
      <pre className="max-h-48 overflow-auto rounded-xl bg-muted p-3 text-xs whitespace-pre-wrap">{data.text}</pre>
      <Button className="h-11 w-full" onClick={() => copyText(data.text)}>
        <Copy /> Скопировать
      </Button>
      <p className="text-center text-xs text-muted-foreground">То же самое в боте: /raspred_zavtra</p>
    </section>
  );
}

function DayTab({ version, onEdit }: { version: number; onEdit: (s: EditableShift) => void }) {
  const [day, setDay] = useState(() => addDays(moscowToday(), 1));
  const { data, loading } = useLoad(() => api.admin.day(day), [day, version]);
  return (
    <section className="space-y-3 rounded-2xl border bg-card p-4 shadow-sm">
      <div className="space-y-1.5">
        <Label htmlFor="admin-day">Дата</Label>
        <Input id="admin-day" type="date" className="h-11" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} />
        <div className="grid grid-cols-2 gap-2 pt-1">
          <Button variant="outline" onClick={() => setDay(moscowToday())}>Сегодня</Button>
          <Button variant="outline" onClick={() => setDay(addDays(moscowToday(), 1))}>Завтра</Button>
        </div>
      </div>
      {loading && !data ? (
        <Skeleton className="h-32 rounded-xl" />
      ) : data && data.items.length === 0 ? (
        <p className="rounded-xl bg-muted p-4 text-sm text-muted-foreground">На эту дату записей нет.</p>
      ) : (
        <ul className="divide-y rounded-xl border">
          {data?.items.map((s) => (
            <ShiftRow key={s.id} shift={s} title={s.name} onClick={() => onEdit(toEditable(s, s.name, s.username))} />
          ))}
        </ul>
      )}
      {data && (
        <p className="text-sm text-muted-foreground">
          Выходят: <b className="text-foreground">{data.total_count}</b> · объём <b className="text-foreground">{data.total_volume} шт.</b>
        </p>
      )}
    </section>
  );
}

function ShiftRow({ shift, title, onClick }: { shift: Shift; title: string; onClick: () => void }) {
  const meta = STATUS_META[shift.status];
  return (
    <li>
      <button onClick={onClick} className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left text-sm active:bg-muted">
        <div className="min-w-0">
          <p className="truncate font-semibold">{title}</p>
          <p className="truncate text-muted-foreground">
            {shift.start_time ? `${timeRange(shift)} · ${shift.location} · ${shift.volume} шт.` : "—"}
          </p>
        </div>
        <span className={cn("shrink-0 text-xs font-semibold", meta.className)}>
          {meta.emoji} {meta.label}
        </span>
      </button>
    </li>
  );
}

function toEditable(s: Shift, name: string, username: string | null): EditableShift {
  return { ...s, name, username };
}

function useUsers(version: number) {
  return useLoad(() => api.admin.users(), [version]);
}

function UserPicker({ users, value, onChange }: { users: User[]; value: User | null; onChange: (u: User | null) => void }) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase().replace(/^@/, "");
    if (!s) return users;
    return users.filter((u) =>
      `${u.first_name} ${u.last_name} ${u.last_name} ${u.first_name} ${u.username ?? ""}`.toLowerCase().includes(s),
    );
  }, [q, users]);

  if (value) {
    return (
      <div className="flex items-center justify-between rounded-xl border bg-accent px-3 py-2.5">
        <div>
          <p className="font-semibold">{value.first_name} {value.last_name}</p>
          {value.username && <p className="text-xs text-muted-foreground">@{value.username}</p>}
        </div>
        <Button size="sm" variant="ghost" onClick={() => onChange(null)}>Сменить</Button>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="h-11 pl-9" placeholder="Имя, фамилия или @username" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <ul className="max-h-56 divide-y overflow-y-auto rounded-xl border">
        {filtered.map((u) => (
          <li key={u.id}>
            <button className="flex w-full items-center justify-between px-3 py-2.5 text-left text-sm active:bg-muted" onClick={() => onChange(u)}>
              <span className="font-medium">{u.first_name} {u.last_name}</span>
              <span className="text-xs text-muted-foreground">{u.username ? `@${u.username}` : ""}</span>
            </button>
          </li>
        ))}
        {filtered.length === 0 && <li className="px-3 py-2.5 text-sm text-muted-foreground">Никого не нашли</li>}
      </ul>
    </div>
  );
}

function presets(today: string) {
  const { weekday, y, m } = parseDay(today);
  const monday = addDays(today, -weekday);
  const monthStart = `${y}-${String(m).padStart(2, "0")}-01`;
  const nextMonth = m === 12 ? `${y + 1}-01-01` : `${y}-${String(m + 1).padStart(2, "0")}-01`;
  return [
    { label: "Текущая неделя", from: monday, to: addDays(monday, 6) },
    { label: "Прошлая неделя", from: addDays(monday, -7), to: addDays(monday, -1) },
    { label: "Текущий месяц", from: monthStart, to: addDays(nextMonth, -1) },
  ];
}

function StatsTab({ version, onEdit }: { version: number; onEdit: (s: EditableShift) => void }) {
  const users = useUsers(version);
  const [user, setUser] = useState<User | null>(null);
  const ranges = useMemo(() => presets(moscowToday()), []);
  const [range, setRange] = useState(ranges[0]);
  const stats = useLoad(
    () => (user ? api.admin.stats(user.id, range.from, range.to) : Promise.resolve(null)),
    [user?.id, range.from, range.to, version],
  );

  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-2xl border bg-card p-4 shadow-sm">
        <Label>Сотрудник</Label>
        {users.data ? <UserPicker users={users.data.items} value={user} onChange={setUser} /> : <Skeleton className="h-11 rounded-xl" />}
        <Label>Период</Label>
        <div className="flex flex-wrap gap-1.5">
          {ranges.map((r) => (
            <button key={r.label} onClick={() => setRange(r)}
                    className={cn("rounded-full border px-3 py-1 text-xs font-medium",
                      range.label === r.label ? "border-primary bg-accent text-accent-foreground" : "bg-muted text-muted-foreground")}>
              {r.label}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Input type="date" className="h-11" value={range.from}
                 onChange={(e) => e.target.value && setRange({ label: "custom", from: e.target.value, to: range.to })} />
          <Input type="date" className="h-11" value={range.to}
                 onChange={(e) => e.target.value && setRange({ label: "custom", from: range.from, to: e.target.value })} />
        </div>
      </section>

      {stats.error && <p className="text-sm text-destructive">{stats.error.message}</p>}
      {user && stats.data && (
        <section className="space-y-3 rounded-2xl border bg-card p-4 shadow-sm">
          <div className="grid grid-cols-2 gap-2 text-center">
            <Stat label="смен (всего)" value={String(stats.data.totals.shifts)} />
            <Stat label="плановый объём" value={`${stats.data.totals.active_volume} шт.`} />
            <Stat label="отменено" value={`${stats.data.totals.cancelled_volume} шт.`} />
            <Stat label="штрафы" value={rub(stats.data.totals.penalties)} />
            <Stat label="заработок (план)" value={rub(stats.data.totals.expected_pay)} wide />
          </div>
          {stats.data.items.length === 0 ? (
            <p className="rounded-xl bg-muted p-4 text-sm text-muted-foreground">За этот период смен нет.</p>
          ) : (
            <div className="-mx-4 overflow-x-auto px-4">
              <Table className="text-xs">
                <TableHeader>
                  <TableRow>
                    <TableHead>Дата</TableHead>
                    <TableHead>Время</TableHead>
                    <TableHead>Точка</TableHead>
                    <TableHead className="text-right">Объём</TableHead>
                    <TableHead>Статус</TableHead>
                    <TableHead>Примечание</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stats.data.items.map((i) => {
                    const meta = STATUS_META[i.status];
                    return (
                      <TableRow key={i.id} className="cursor-pointer"
                                onClick={() => onEdit({ ...i, name: stats.data!.user.name, username: stats.data!.user.username })}>
                        <TableCell className="whitespace-nowrap">{ddmm(i.date)} {WEEKDAYS_SHORT[parseDay(i.date).weekday]}</TableCell>
                        <TableCell className="whitespace-nowrap">{timeRange(i)}</TableCell>
                        <TableCell>{i.location ?? "—"}</TableCell>
                        <TableCell className="text-right">{i.volume ?? "—"}</TableCell>
                        <TableCell className={cn("whitespace-nowrap font-medium", meta.className)}>{meta.emoji} {meta.label}</TableCell>
                        <TableCell className="whitespace-nowrap text-muted-foreground">{i.notes}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function PenaltiesTab({ version, onEdit }: { version: number; onEdit: (s: EditableShift) => void }) {
  const users = useUsers(version);
  const [user, setUser] = useState<User | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const { data, loading } = useLoad(() => api.admin.penalties({ userId: user?.id, from, to }), [user?.id, from, to, version]);
  const total = data?.items.reduce((s, p) => s + (p.amount ?? 0), 0) ?? 0;

  async function openShift(shiftId: number, date: string, name: string, username: string | null) {
    const day = await api.admin.day(date);
    const s = day.items.find((x: AdminShift) => x.id === shiftId);
    if (s) onEdit(toEditable(s, name, username));
  }

  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-2xl border bg-card p-4 shadow-sm">
        <Label>Сотрудник (необязательно)</Label>
        {users.data ? <UserPicker users={users.data.items} value={user} onChange={setUser} /> : <Skeleton className="h-11 rounded-xl" />}
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">С</Label>
            <Input type="date" className="h-11" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label className="text-xs text-muted-foreground">По</Label>
            <Input type="date" className="h-11" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </div>
      </section>
      <section className="rounded-2xl border bg-card p-4 shadow-sm">
        {loading && !data ? (
          <Skeleton className="h-24 rounded-xl" />
        ) : data && data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Штрафов нет.</p>
        ) : (
          <>
            <ul className="divide-y">
              {data?.items.map((p) => (
                <li key={p.shift_id}>
                  <button className="flex w-full items-start justify-between gap-2 py-2.5 text-left text-sm active:bg-muted"
                          onClick={() => openShift(p.shift_id, p.date, p.name, p.username)}>
                    <div className="min-w-0">
                      <p className="font-semibold">{p.name}{p.username ? ` · @${p.username}` : ""}</p>
                      <p className="text-muted-foreground">
                        Смена {shortDate(p.date)} · {p.reason}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {p.set_by ? `Назначил: ${p.set_by}${p.set_at ? `, ${p.set_at.slice(0, 16).replace("T", " ")}` : ""}` : "Сумма ещё не назначена — нажмите, чтобы указать"}
                      </p>
                    </div>
                    <span className={cn("shrink-0 font-semibold", p.amount != null ? "text-final" : "text-wait")}>
                      {p.amount != null ? rub(p.amount) : "—"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-2 flex justify-between border-t pt-3 font-semibold">
              <span>Итого</span>
              <span>{rub(total)}</span>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={cn("rounded-xl bg-muted px-3 py-2.5", wide && "col-span-2")}>
      <p className="text-lg leading-tight font-bold">{value}</p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  );
}
