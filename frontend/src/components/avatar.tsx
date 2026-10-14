import type { User } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Avatar({ user, className }: { user: Pick<User, "first_name" | "last_name">; className?: string }) {
  const initials = `${user.first_name[0] ?? ""}${user.last_name[0] ?? ""}`.toUpperCase();
  return (
    <span className={cn("grid shrink-0 place-items-center bg-primary font-display font-semibold text-primary-foreground", className)}>
      {initials}
    </span>
  );
}
