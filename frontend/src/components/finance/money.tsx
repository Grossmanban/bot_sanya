"use client";

import { useState } from "react";
import { Gift, LoaderCircle, MinusCircle, Receipt, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, type Balance, type Tx, type TxType } from "@/lib/api";
import { useCan } from "@/lib/caps";
import { dayMonth, rub } from "@/lib/format";
import { moscowToday, useAction } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { PhotoPicker, PhotoThumb } from "../report/photo";

export const TYPE_META: Record<TxType, { label: string; icon: typeof Gift; sign: "+" | "−"; tone: string }> = {
  fine: { label: "Штраф", icon: Receipt, sign: "−", tone: "text-cancel" },
  bonus: { label: "Бонус", icon: Gift, sign: "+", tone: "text-go" },
  deduction: { label: "Вычет", icon: MinusCircle, sign: "−", tone: "text-final" },
  payout_offset: { label: "Зачёт", icon: Receipt, sign: "−", tone: "text-muted-foreground" },
};

export const STATUS_LABEL: Record<Tx["status"], string> = {
  draft: "ждёт суммы",
  pending_admin_approval: "на согласовании",
  active: "",
  paid: "погашен",
  offset: "учтён в выплате",
  cancelled: "аннулирован",
  rejected: "отклонён",
};

const DEAD: Tx["status"][] = ["cancelled", "rejected"];

/** Balance as the employee sees it: bonuses − fines − deductions. */
export function BalanceCard({ balance, className }: { balance: Balance; className?: string }) {
  const b = balance.balance;
  return (
    <section className={cn("rounded-3xl bg-hero p-5 text-hero-foreground shadow-lift-strong md:p-6", className)}>
      <p className="text-sm text-hero-muted">Баланс</p>
      <p className={cn("font-display text-4xl font-semibold tabular", b < 0 && "text-[#ff8a8a]", b > 0 && "text-[#6ee7a8]")}>
        {b > 0 ? "+" : b < 0 ? "−" : ""}{rub(Math.abs(b))}
      </p>
      <dl className="mt-4 grid grid-cols-3 gap-2 text-sm">
        <div><dt className="text-hero-muted">Штрафы</dt><dd className="font-semibold tabular">{rub(balance.fines)}</dd></div>
        <div><dt className="text-hero-muted">Бонусы</dt><dd className="font-semibold tabular">{rub(balance.bonuses)}</dd></div>
        <div><dt className="text-hero-muted">Вычеты</dt><dd className="font-semibold tabular">{rub(balance.deductions)}</dd></div>
      </dl>
      {(balance.drafts > 0 || (balance.pending ?? 0) > 0) && (
        <p className="mt-3 text-xs text-hero-muted">
          {balance.drafts > 0 && `Без суммы: ${balance.drafts}. `}
          {(balance.pending ?? 0) > 0 && `На согласовании: ${rub(balance.pending ?? 0)}.`}
        </p>
      )}
    </section>
  );
}

/** One ledger entry; staff get annul / fill-in-amount actions. */
export function TxRow({ tx, showName, actions, onChanged }: { tx: Tx; showName?: boolean; actions?: boolean; onChanged?: () => void }) {
  const meta = TYPE_META[tx.type];
  const Icon = meta.icon;
  const dead = DEAD.includes(tx.status);
  const [mode, setMode] = useState<null | "cancel" | "amount">(null);
  const canFine = useCan("fine_direct");
  const canMoney = useCan("money");
  const canCancel = actions && ["draft", "active", "pending_admin_approval"].includes(tx.status)
    && (tx.type === "deduction" ? canMoney : canFine);
  const canFill = actions && tx.status === "draft" && canFine;

  return (
    <li className="row-in px-5 py-3.5 md:px-6">
      <div className="flex items-start gap-3">
        <span className={cn("mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl bg-muted", meta.tone, dead && "opacity-40")}>
          <Icon className="size-[18px]" />
        </span>
        <div className="min-w-0 flex-1">
          <p className={cn("text-sm font-medium", dead && "text-muted-foreground line-through")}>
            {showName && tx.name ? <span className="font-semibold">{tx.name} · </span> : null}
            {tx.reason}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {meta.label}, {dayMonth(tx.event_date)}
            {tx.created_by ? ` · ${tx.created_by}` : ""}
            {STATUS_LABEL[tx.status] && <span className={cn("font-semibold", tx.status === "pending_admin_approval" || tx.status === "draft" ? "text-wait" : "")}> · {STATUS_LABEL[tx.status]}</span>}
            {tx.decision_comment && ` · ${tx.decision_comment}`}
          </p>
        </div>
        {tx.media_url && <PhotoThumb src={tx.media_url} className="size-10 rounded-lg" />}
        <span className={cn("shrink-0 font-display font-semibold tabular", dead ? "text-muted-foreground line-through" : meta.tone)}>
          {tx.amount != null ? `${meta.sign}${rub(tx.amount)}` : "—"}
        </span>
      </div>
      {(canCancel || canFill) && !mode && (
        <div className="mt-2 flex gap-2 pl-12">
          {canFill && <Button size="sm" className="press h-8 rounded-lg" onClick={() => setMode("amount")}>Назначить сумму</Button>}
          {canCancel && (
            <Button size="sm" variant="ghost" className="press h-8 rounded-lg text-muted-foreground" onClick={() => setMode("cancel")}>
              <Undo2 /> Аннулировать
            </Button>
          )}
        </div>
      )}
      {mode === "cancel" && <CancelForm tx={tx} onClose={() => setMode(null)} onDone={onChanged} />}
      {mode === "amount" && <AmountForm tx={tx} onClose={() => setMode(null)} onDone={onChanged} />}
    </li>
  );
}

const input = "h-10 min-w-0 flex-1 rounded-xl border-0 bg-muted px-3 text-[15px] outline-none focus:ring-2 focus:ring-ring/25";

function CancelForm({ tx, onClose, onDone }: { tx: Tx; onClose: () => void; onDone?: () => void }) {
  const [reason, setReason] = useState("");
  const { busy, run } = useAction();
  return (
    <div className="mt-2 flex gap-2 pl-12">
      <input autoFocus className={input} placeholder="Причина аннулирования" value={reason} onChange={(e) => setReason(e.target.value.slice(0, 300))} />
      <Button size="sm" className="press h-10 rounded-xl" disabled={!!busy || !reason.trim()}
              onClick={async () => {
                try {
                  await run("cancel", (k) => api.admin.cancelTx(tx.id, reason.trim(), k), "Аннулировано");
                  onClose();
                  onDone?.();
                } catch {
                  /* toast shown */
                }
              }}>
        {busy && <LoaderCircle className="animate-spin" />} OK
      </Button>
      <Button size="sm" variant="ghost" className="h-10 rounded-xl" onClick={onClose}>Отмена</Button>
    </div>
  );
}

const PRESETS = [500, 1000, 2000];

function AmountForm({ tx, onClose, onDone }: { tx: Tx; onClose: () => void; onDone?: () => void }) {
  const [amount, setAmount] = useState("");
  const { busy, run } = useAction();
  return (
    <div className="mt-2 flex flex-wrap gap-2 pl-12">
      {PRESETS.map((p) => (
        <button key={p} onClick={() => setAmount(String(p))}
                className={cn("press rounded-full px-3 py-1.5 text-sm font-semibold", Number(amount) === p ? "bg-cancel text-white" : "bg-cancel-soft text-cancel")}>
          {p} ₽
        </button>
      ))}
      <input inputMode="numeric" className={cn(input, "w-28 flex-none")} placeholder="сумма" value={amount}
             onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 7))} />
      <Button size="sm" className="press h-10 rounded-xl" disabled={!!busy || !Number(amount)}
              onClick={async () => {
                try {
                  await run("fill", (k) => api.admin.fillTx(tx.id, Number(amount), k), "Штраф назначен");
                  onClose();
                  onDone?.();
                } catch {
                  /* toast shown */
                }
              }}>
        {busy && <LoaderCircle className="animate-spin" />} Назначить
      </Button>
      <Button size="sm" variant="ghost" className="h-10 rounded-xl" onClick={onClose}>Отмена</Button>
    </div>
  );
}

const QUICK_REASONS: Record<"fine" | "bonus" | "deduction", string[]> = {
  fine: ["Опоздание", "Нарушение правил точки", "Не вышел без предупреждения"],
  bonus: ["Лучший результат дня", "Помощь команде", "Перевыполнил план"],
  deduction: ["Форма", "Аванс", "Материалы"],
};

/** Fine / bonus / deduction for one person (ТЗ 4.1 quick actions). Trainer fines go to an owner. */
export function TxForm({ userId, initial = "fine", onDone }: { userId: number; initial?: "fine" | "bonus" | "deduction"; onDone?: () => void }) {
  const canFine = useCan("fine_direct");
  const canRequest = useCan("fine_request");
  const canBonus = useCan("bonus");
  const canMoney = useCan("money");
  const types = ([
    (canFine || canRequest) && "fine",
    canBonus && "bonus",
    canMoney && "deduction",
  ].filter(Boolean)) as ("fine" | "bonus" | "deduction")[];
  const [type, setType] = useState<"fine" | "bonus" | "deduction">(types.includes(initial) ? initial : types[0]);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [photo, setPhoto] = useState<Blob | null>(null);
  // deductions may be entered in pieces for a day (customer round B): rubles are counted on the server
  const [inPieces, setInPieces] = useState(false);
  const [day, setDay] = useState(moscowToday());
  const pieceMode = type === "deduction" && inPieces;
  const [uploaded, setUploaded] = useState<{ blob: Blob; id: string } | null>(null);
  const { busy, run } = useAction();
  const n = Number(amount);
  if (types.length === 0) return null;

  async function submit() {
    try {
      const res = await run("tx", async (k) => {
        // the photo goes to Telegram first; a retry reuses the uploaded file
        let media: string | null = null;
        if (photo) {
          media = uploaded?.blob === photo ? uploaded.id : (await api.admin.uploadMedia(photo)).file_id;
          setUploaded({ blob: photo, id: media });
        }
        if (pieceMode) {
          const out = await api.admin.pieceDeduction({ user_id: userId, day, pieces: n, reason: reason.trim(), media_file_id: media }, k);
          return { tx: out.items[0], trainer: out.items[1] };
        }
        return { ...(await api.admin.createTx(userId, type, n, reason.trim(), k, media)), trainer: undefined };
      });
      if (res && pieceMode) {
        toast.success(`Вычет ${n} шт.: ${rub(res.tx.amount ?? 0)}` + (res.trainer ? `, тренеру ${rub(res.trainer.amount ?? 0)}` : ""));
        setAmount("");
        setReason("");
        setPhoto(null);
        onDone?.();
      } else if (res) {
        toast.success(res.tx.status === "pending_admin_approval" ? "Заявка на штраф отправлена владельцу" : `${TYPE_META[type].label} ${rub(n)} начислен`);
        setAmount("");
        setReason("");
        setPhoto(null);
        onDone?.();
      }
    } catch {
      /* toast shown */
    }
  }

  return (
    <div className="space-y-3">
      {types.length > 1 && (
        <div className="grid gap-1 rounded-2xl bg-muted p-1" style={{ gridTemplateColumns: `repeat(${types.length}, minmax(0, 1fr))` }}>
          {types.map((t) => (
            <button key={t} onClick={() => setType(t)}
                    className={cn("press h-10 rounded-xl text-sm font-semibold", type === t ? "bg-card shadow-lift" : "text-muted-foreground")}>
              {TYPE_META[t].label}
            </button>
          ))}
        </div>
      )}
      {type === "deduction" && (
        <div className="flex gap-1 rounded-xl bg-muted p-1">
          {[false, true].map((v) => (
            <button key={String(v)} onClick={() => { setInPieces(v); setAmount(""); }}
                    className={cn("press h-8 flex-1 rounded-lg text-xs font-semibold", inPieces === v ? "bg-card shadow-lift" : "text-muted-foreground")}>
              {v ? "В штуках за день" : "В рублях"}
            </button>
          ))}
        </div>
      )}
      {pieceMode && (
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            День
            <input type="date" max={moscowToday()} value={day} onChange={(e) => e.target.value && setDay(e.target.value)}
                   className={cn(input, "w-auto flex-none")} />
          </label>
          <p className="text-xs text-muted-foreground">
            Сумма = штуки × ставка сотрудника за этот день; у тренера сети — штуки × его ставка лестницы за этот день.
            Оба увидят минус в рублях.
          </p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {(pieceMode ? [1, 3, 5, 10] : type === "bonus" ? [300, 500, 1000] : PRESETS).map((p) => (
          <button key={p} onClick={() => setAmount(String(p))}
                  className={cn("press rounded-full px-3.5 py-1.5 text-sm font-semibold",
                    n === p ? (type === "bonus" ? "bg-go text-white" : "bg-cancel text-white") : "bg-muted")}>
            {p} {pieceMode ? "шт." : "₽"}
          </button>
        ))}
        <input inputMode="numeric" className={cn(input, "w-28 flex-none")} placeholder={pieceMode ? "штук" : "своя сумма"} value={amount}
               onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 7))} />
      </div>
      <div className="flex flex-wrap gap-1.5">
        {QUICK_REASONS[type].map((r) => (
          <button key={r} onClick={() => setReason(r)}
                  className={cn("press rounded-full px-3 py-1.5 text-xs font-semibold", reason === r ? "bg-primary text-primary-foreground" : "bg-muted")}>
            {r}
          </button>
        ))}
      </div>
      <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value.slice(0, 500))} placeholder="Причина"
                className="w-full resize-none rounded-xl border-0 bg-muted px-3 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-ring/25" />
      <PhotoPicker value={photo} onChange={setPhoto} label="Фото или скриншот" optional />
      {type === "fine" && !canFine && <p className="text-xs text-muted-foreground">Штраф от тренера вступит в силу после одобрения владельцем.</p>}
      <Button className={cn("press h-12 w-full rounded-2xl text-[15px] font-semibold", type === "bonus" && "bg-go text-white hover:bg-go/90",
        type === "fine" && "bg-cancel text-white hover:bg-cancel/90")}
              disabled={!!busy || !n || !reason.trim()} onClick={submit}>
        {busy && <LoaderCircle className="animate-spin" />}
        {n
          ? pieceMode ? `Вычет ${n} шт.` : `${type === "fine" && !canFine ? "Запросить штраф" : TYPE_META[type].label} ${rub(n)}`
          : pieceMode ? "Сколько штук" : "Выберите сумму"}
      </Button>
    </div>
  );
}
