"use client";

import { useState } from "react";
import { BellRing, CalendarCheck, Check, ChevronLeft, Clock3, Info, LoaderCircle, MessageCircle, Pencil, UserPlus } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Drawer, DrawerContent, DrawerTitle } from "@/components/ui/drawer";
import { api, ApiError, type ApplicationForm, type Me } from "@/lib/api";
import { dayMonth, plural } from "@/lib/format";
import { ensureWriteAccess, haptic, openBotChat, tapHaptic } from "@/lib/telegram";
import { cn } from "@/lib/utils";

const DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const field = "h-12 w-full rounded-2xl border-0 bg-card px-4 text-base shadow-lift outline-none focus:ring-2 focus:ring-ring/25";

/** The questionnaire (the customer's module 3): start → 4 steps → a draft to check and edit → sent. */
export function Registration({ onSent, botUsername }: { onSent: () => void; botUsername?: string | null }) {
  const [step, setStep] = useState(0); // 0 — start, 1–4 — steps, 5 — the draft
  const [back, setBack] = useState<number | null>(null); // edit from the draft: «Готово» returns there
  const [form, setForm] = useState<ApplicationForm>({
    full_name: "", age: 0, metro: "", min_days: 0, training_days: [], communication: 0, truthful: false, consent: false,
  });
  const [askDays, setAskDays] = useState(false);
  const [consentText, setConsentText] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [noWrite, setNoWrite] = useState(false); // refused to let the bot write: notifications would never come
  const set = (patch: Partial<ApplicationForm>) => setForm((f) => ({ ...f, ...patch }));

  const ok: Record<number, boolean> = {
    1: form.full_name.trim().split(/\s+/).length >= 2 && form.age >= 14 && form.age <= 80 && form.metro.trim().length > 0,
    2: form.min_days >= 1,
    3: form.training_days.length > 0,
    4: form.communication >= 1,
  };
  const next = () => {
    tapHaptic();
    if (back !== null) {
      setStep(back);
      setBack(null);
    } else setStep((s) => s + 1);
  };
  const edit = (s: number) => {
    setBack(5);
    setStep(s);
  };

  async function send() {
    setBusy(true);
    try {
      await api.register(form);
      haptic("success");
      setSent(true);
    } catch (e) {
      haptic("error");
      toast.error(e instanceof ApiError ? e.message : "Не удалось отправить анкету");
    } finally {
      setBusy(false);
    }
  }

  // the bot must be able to write first: training, reminders and shifts come only through it
  async function begin() {
    tapHaptic();
    setBusy(true);
    const granted = await ensureWriteAccess();
    setBusy(false);
    if (granted === false) {
      haptic("warning");
      setNoWrite(true);
    } else {
      setNoWrite(false);
      setStep(1);
    }
  }

  if (sent) return <Sent onDone={onSent} />;

  if (noWrite) {
    return (
      <Screen>
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <span className="grid size-16 place-items-center rounded-3xl bg-card shadow-lift"><BellRing className="size-7" strokeWidth={1.8} /></span>
          <h1 className="mt-6 font-display text-2xl font-semibold">Разрешите боту писать вам</h1>
          <p className="mt-3 max-w-xs text-[15px] text-muted-foreground">
            Дата обучения, напоминания и смены приходят только сообщениями от бота. Без разрешения вы их не получите.
          </p>
        </div>
        <div className="grid gap-2">
          <Button size="lg" className="press h-14 w-full rounded-2xl text-base font-semibold" disabled={busy} onClick={begin}>
            {busy && <LoaderCircle className="animate-spin" />} Разрешить
          </Button>
          {botUsername && (
            <Button size="lg" variant="secondary" className="press h-14 w-full rounded-2xl text-base font-semibold"
                    onClick={() => openBotChat(botUsername)}>
              <MessageCircle /> Открыть чат с ботом
            </Button>
          )}
        </div>
      </Screen>
    );
  }

  if (step === 0) {
    return (
      <Screen>
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <span className="grid size-16 place-items-center rounded-3xl bg-card shadow-lift"><UserPlus className="size-7" strokeWidth={1.8} /></span>
          <h1 className="mt-6 font-display text-2xl font-semibold">Присоединяйтесь к команде</h1>
          <p className="mt-3 max-w-xs text-[15px] text-muted-foreground">Анкета займёт пару минут. После неё кураторы назначат обучение.</p>
        </div>
        <Button size="lg" className="press h-14 w-full rounded-2xl text-base font-semibold" disabled={busy} onClick={begin}>
          {busy && <LoaderCircle className="animate-spin" />} Зарегистрироваться
        </Button>
      </Screen>
    );
  }

  const header = (title: string, hint?: string) => (
    <div className="mb-6">
      <div className="mb-4 flex items-center gap-2">
        <button aria-label="Назад" className="press -ml-2 grid size-10 place-items-center rounded-full hover:bg-muted"
                onClick={() => (back !== null ? (setStep(back), setBack(null)) : setStep((s) => s - 1))}>
          <ChevronLeft className="size-5" />
        </button>
        {step <= 4 && (
          <div className="flex flex-1 gap-1.5" aria-label={`Шаг ${step} из 4`}>
            {[1, 2, 3, 4].map((i) => <span key={i} className={cn("h-1.5 flex-1 rounded-full", i <= step ? "bg-primary" : "bg-muted")} />)}
          </div>
        )}
      </div>
      <h1 className="font-display text-2xl font-semibold">{title}</h1>
      {hint && <p className="mt-1 text-[15px] text-muted-foreground">{hint}</p>}
    </div>
  );
  const nextButton = (label = back !== null ? "Готово" : "Дальше", disabled = !ok[step], onClick = next) => (
    <Button size="lg" className="press mt-auto h-14 w-full rounded-2xl text-base font-semibold" disabled={disabled} onClick={onClick}>
      {label}
    </Button>
  );

  if (step === 1) {
    return (
      <Screen>
        {header("О себе")}
        <div className="space-y-4">
          <label className="block space-y-1.5"><span className="text-sm text-muted-foreground">Фамилия, имя, отчество</span>
            <input className={field} autoComplete="name" placeholder="Иванов Иван Иванович" value={form.full_name}
                   onChange={(e) => set({ full_name: e.target.value.slice(0, 100) })} /></label>
          <label className="block space-y-1.5"><span className="text-sm text-muted-foreground">Возраст</span>
            <input className={field} inputMode="numeric" placeholder="Например, 19" value={form.age || ""}
                   onChange={(e) => set({ age: Number(e.target.value.replace(/\D/g, "").slice(0, 2)) })} /></label>
          <label className="block space-y-1.5"><span className="text-sm text-muted-foreground">Ближайшая станция метро</span>
            <input className={field} placeholder="Например, Чкаловская" value={form.metro}
                   onChange={(e) => set({ metro: e.target.value.slice(0, 60) })} /></label>
        </div>
        {nextButton()}
      </Screen>
    );
  }

  if (step === 2) {
    return (
      <Screen>
        {header("Сколько дней в неделю готовы работать?", "Минимум — от этого зависит график")}
        <p className="flex items-start gap-2 rounded-2xl bg-wait-soft p-4 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-wait" /> Учтите занятость: учёба, основная работа, спорт.
        </p>
        <div className="mt-5 grid grid-cols-7 gap-2">
          {[1, 2, 3, 4, 5, 6, 7].map((n) => (
            <button key={n} onClick={() => { tapHaptic(); set({ min_days: n }); }}
                    className={cn("press grid aspect-square place-items-center rounded-2xl font-display text-xl font-semibold shadow-lift",
                      form.min_days === n ? "bg-primary text-primary-foreground" : "bg-card")}>
              {n}
            </button>
          ))}
        </div>
        {form.min_days > 0 && <p className="mt-3 text-center text-sm text-muted-foreground">от {form.min_days} {plural(form.min_days, "дня", "дней", "дней")} в неделю</p>}
        {nextButton()}
      </Screen>
    );
  }

  if (step === 3) {
    const toggle = (d: number) => {
      tapHaptic();
      set({ training_days: form.training_days.includes(d) ? form.training_days.filter((x) => x !== d) : [...form.training_days, d].sort() });
    };
    return (
      <Screen>
        {header("Удобные дни для обучения", "Отметьте все, когда сможете прийти")}
        <ul className="space-y-2">
          {DAYS.map((label, d) => {
            const on = form.training_days.includes(d);
            return (
              <li key={d}>
                <button onClick={() => toggle(d)} aria-pressed={on}
                        className={cn("press flex h-14 w-full items-center justify-between rounded-2xl px-5 text-left font-semibold shadow-lift",
                          on ? "bg-primary text-primary-foreground" : "bg-card")}>
                  {["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][d]}
                  <span className={cn("grid size-7 place-items-center rounded-full", on ? "bg-white/20" : "bg-muted")}>{on && <Check className="size-4" />}</span>
                  <span className="sr-only">{label}</span>
                </button>
              </li>
            );
          })}
        </ul>
        {nextButton(back !== null ? "Готово" : "Дальше", !ok[3], () => setAskDays(true))}
        <AlertDialog open={askDays} onOpenChange={setAskDays}>
          <AlertDialogContent className="rounded-3xl">
            <AlertDialogHeader>
              <AlertDialogTitle className="font-display">Именно эти дни удобны?</AlertDialogTitle>
              <AlertDialogDescription>
                {form.training_days.map((d) => DAYS[d]).join(", ")}. Обучение назначат на один из них.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel className="rounded-xl">Изменить</AlertDialogCancel>
              <AlertDialogAction className="rounded-xl" onClick={next}>Да, удобны</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </Screen>
    );
  }

  if (step === 4) {
    return (
      <Screen>
        {header("Навыки общения", "Оцените себя от 1 до 10: насколько легко заговорить с незнакомым человеком")}
        <div className="grid grid-cols-5 gap-2">
          {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
            <button key={n} onClick={() => { tapHaptic(); set({ communication: n }); }}
                    className={cn("press grid aspect-square place-items-center rounded-2xl font-display text-xl font-semibold shadow-lift",
                      form.communication === n ? "bg-primary text-primary-foreground" : "bg-card")}>
              {n}
            </button>
          ))}
        </div>
        <div className="mt-2 flex justify-between px-1 text-xs text-muted-foreground"><span>сложно</span><span>легко</span></div>
        {nextButton(back !== null ? "Готово" : "Проверить анкету")}
      </Screen>
    );
  }

  // the draft
  const rows: [string, string, number][] = [
    ["ФИО", form.full_name.trim(), 1],
    ["Возраст", `${form.age} ${plural(form.age, "год", "года", "лет")}`, 1],
    ["Метро", form.metro.trim(), 1],
    ["Дней в неделю", `от ${form.min_days}`, 2],
    ["Дни для обучения", form.training_days.map((d) => DAYS[d]).join(", "), 3],
    ["Навыки общения", `${form.communication} из 10`, 4],
  ];
  return (
    <Screen>
      {header("Проверьте анкету", "Нажмите на пункт, чтобы исправить")}
      <ul className="overflow-hidden rounded-3xl bg-card shadow-lift">
        {rows.map(([label, value, s]) => (
          <li key={label} className="border-b last:border-b-0">
            <button className="press flex w-full items-center gap-3 px-5 py-3.5 text-left" onClick={() => edit(s)}>
              <span className="min-w-0 flex-1">
                <span className="block text-xs text-muted-foreground">{label}</span>
                <span className="block truncate font-medium">{value}</span>
              </span>
              <Pencil className="size-4 shrink-0 text-muted-foreground" />
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-5 space-y-3">
        <label className="flex cursor-pointer items-start gap-3 text-[15px]">
          <input type="checkbox" className="mt-1 size-5 shrink-0 accent-[var(--primary)]" checked={form.truthful}
                 onChange={(e) => set({ truthful: e.target.checked })} />
          Я подтверждаю достоверность данных
        </label>
        <label className="flex cursor-pointer items-start gap-3 text-[15px]">
          <input type="checkbox" className="mt-1 size-5 shrink-0 accent-[var(--primary)]" checked={form.consent}
                 onChange={(e) => set({ consent: e.target.checked })} />
          <span>
            Даю согласие на обработку персональных данных{" "}
            <button type="button" className="font-semibold underline underline-offset-2" onClick={() => setConsentText(true)}>(текст согласия)</button>
          </span>
        </label>
      </div>
      <Button size="lg" className="press mt-6 h-14 w-full rounded-2xl text-base font-semibold" disabled={busy || !form.truthful || !form.consent} onClick={send}>
        {busy && <LoaderCircle className="animate-spin" />} Отправить заявку
      </Button>
      <Drawer open={consentText} onOpenChange={setConsentText}>
        <DrawerContent className="mx-auto max-h-[85dvh] max-w-2xl rounded-t-[1.75rem] border-0 bg-card">
          <div className="pb-safe overflow-y-auto px-5 pt-2">
            <DrawerTitle className="font-display text-xl font-semibold">Согласие на обработку персональных данных</DrawerTitle>
            <ConsentText />
            <Button className="press mt-4 mb-2 h-12 w-full rounded-2xl" onClick={() => { set({ consent: true }); setConsentText(false); }}>
              Согласен(на)
            </Button>
          </div>
        </DrawerContent>
      </Drawer>
    </Screen>
  );
}

function Screen({ children }: { children: React.ReactNode }) {
  return <div className="pt-safe mx-auto flex min-h-dvh max-w-md flex-col px-5 pb-8">{children}</div>;
}

/** After sending (ТЗ: the final screen text). */
function Sent({ onDone }: { onDone: () => void }) {
  return (
    <Screen>
      <div className="flex flex-1 flex-col items-center justify-center text-center">
        <span className="grid size-16 place-items-center rounded-3xl bg-go-soft text-go shadow-lift"><Check className="size-8" /></span>
        <h1 className="mt-6 font-display text-2xl font-semibold">Заявка принята!</h1>
        <p className="mt-3 max-w-xs text-[15px] leading-relaxed text-muted-foreground">
          Данные переданы кураторам. Не выключайте уведомления! Бот свяжется с вами после согласования с тренерами,
          чтобы назначить день, время и место обучения.
        </p>
      </div>
      <Button variant="secondary" size="lg" className="press h-12 w-full rounded-2xl" onClick={onDone}>Готово</Button>
    </Screen>
  );
}

/** A candidate whose questionnaire is with the curators: where it stands. */
export function ApplicationStatus({ application }: { application: NonNullable<Me["application"]> }) {
  const t = application.training;
  const scheduled = application.status === "scheduled" && t.training_date;
  return (
    <Screen>
      <div className="flex flex-1 flex-col items-center justify-center text-center">
        <span className="grid size-16 place-items-center rounded-3xl bg-card shadow-lift">
          {scheduled ? <CalendarCheck className="size-7" strokeWidth={1.8} /> : <Clock3 className="size-7" strokeWidth={1.8} />}
        </span>
        <h1 className="mt-6 font-display text-2xl font-semibold">{scheduled ? "Обучение назначено" : "Анкета у кураторов"}</h1>
        <p className="mt-3 max-w-xs text-[15px] leading-relaxed text-muted-foreground">
          {scheduled
            ? `${dayMonth(t.training_date!)} в ${t.training_time}, ${t.training_location}. Подтвердите запись в сообщении от бота.`
            : "Не выключайте уведомления: бот напишет, когда согласует с тренерами день, время и место обучения."}
        </p>
      </div>
    </Screen>
  );
}

/** A typical consent under 152-FZ; the operator's details are filled in by the owner. */
function ConsentText() {
  return (
    <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted-foreground">
      <p>
        Заполняя анкету, я свободно, своей волей и в своём интересе даю согласие оператору — организатору проекта
        «Иди работай!» (далее — Оператор) на обработку моих персональных данных на условиях ниже (ст. 9 Федерального
        закона от 27.07.2006 № 152-ФЗ «О персональных данных»).
      </p>
      <p><b className="text-foreground">Данные:</b> фамилия, имя, отчество; возраст; ближайшая станция метро; сведения о
        занятости и удобных днях; самооценка навыков; идентификатор, имя пользователя и фото профиля Telegram; сведения
        о том, кто меня пригласил; данные о выходах на работу и их результатах.</p>
      <p><b className="text-foreground">Цели:</b> рассмотрение кандидатуры, назначение и проведение обучения, связь со
        мной, организация работы и расчёт вознаграждения, учёт приглашений.</p>
      <p><b className="text-foreground">Действия:</b> сбор, запись, систематизация, накопление, хранение, уточнение,
        использование, передача лицам, участвующим в обучении и организации работы, обезличивание, блокирование,
        удаление, уничтожение — с использованием средств автоматизации и без них.</p>
      <p><b className="text-foreground">Срок:</b> согласие действует до достижения целей обработки или до его отзыва.
        Отозвать согласие можно, написав Оператору в Telegram-боте проекта; после отзыва данные удаляются в течение
        30 дней, кроме случаев, когда закон требует их хранить.</p>
      <p>Я подтверждаю, что мне исполнилось 14 лет, а если мне меньше 18 лет — согласие на обработку данных дано с
        ведома моего законного представителя.</p>
    </div>
  );
}
