import type { User } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Avatar({ user, className }: { user: Pick<User, "name">; className?: string }) {
  // «Иванов Александр А.» → «ИА»: the first letters of the display name's first two words
  const initials = (user.name ?? "").split(" ").slice(0, 2).map((w) => w[0] ?? "").join("").toUpperCase();
  return (
    <span className={cn("grid shrink-0 place-items-center bg-primary font-display font-semibold text-primary-foreground", className)}>
      {initials}
    </span>
  );
}
