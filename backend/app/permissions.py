"""Who may do what. One place for the role rules so the API, the bot and the UI agree.

Roles (agreed with the owner, ТЗ v9.1 + one extra):
  owner    — Super Admin: ADMIN_IDS from config, there may be several. Everything, incl. money
             (deductions, payouts), blocking accounts, roles, the day's global output.
  admin    — works with everyone: schedule, reports, fines without approval, bonuses, pauses,
             broadcasts. No money operations, no blocking, no roles.
  trainer  — like an admin but only for people of their own network; fines go to an owner for approval.
  employee — their own schedule, reports and balance.
"""

from .config import settings

OWNER, ADMIN, TRAINER, EMPLOYEE = "owner", "admin", "trainer", "employee"
ROLE_TITLES = {OWNER: "Владелец", ADMIN: "Администратор", TRAINER: "Тренер", EMPLOYEE: "Сотрудник"}


def is_owner(user: dict | None) -> bool:
    return bool(user) and user["telegram_id"] in settings.admin_ids


def role_of(user: dict | None) -> str:
    if not user:
        return EMPLOYEE
    if is_owner(user):
        return OWNER
    return user["role"] if user["role"] in (ADMIN, TRAINER) else EMPLOYEE


def is_staff(user: dict | None) -> bool:
    """Has the admin panel at all (scope may still be limited for trainers)."""
    return role_of(user) in (OWNER, ADMIN, TRAINER) and user["status"] == "approved" and user["account_state"] != "blocked"


def sees_everyone(user: dict | None) -> bool:
    return role_of(user) in (OWNER, ADMIN)


def manages(actor: dict | None, target: dict | None) -> bool:
    """Actor may act on target's schedule/reports/discipline."""
    if not actor or not target or not is_staff(actor):
        return False
    if sees_everyone(actor):
        return True
    return actor.get("network_id") is not None and actor["network_id"] == target.get("network_id") and target["id"] != actor["id"]


# capability -> roles that have it (trainers additionally need manages(actor, target))
CAN = {
    "money": {OWNER},               # deductions, payouts, paying off debts in bulk
    "block": {OWNER},
    "roles": {OWNER},
    "global_actual": {OWNER},
    "networks": {OWNER},
    "fine_direct": {OWNER, ADMIN},  # trainers' fines wait for an owner
    "approve_fines": {OWNER},
    "fine_request": {TRAINER},
    "bonus": {OWNER, ADMIN, TRAINER},
    "pause": {OWNER, ADMIN, TRAINER},
    "review": {OWNER, ADMIN, TRAINER},
    "schedule": {OWNER, ADMIN, TRAINER},
    "broadcast_all": {OWNER, ADMIN},
    "broadcast_network": {OWNER, ADMIN, TRAINER},
    "settings": {OWNER, ADMIN},
    "points": {OWNER, ADMIN},
    "registrations": {OWNER, ADMIN},
    "export": {OWNER, ADMIN, TRAINER},
    "raspred": {OWNER, ADMIN, TRAINER},  # the list for the work chat; a trainer gets their network's
    "all_people": {OWNER, ADMIN},   # works with the whole team, not one network (company-wide goals, events)
}


def can(actor: dict | None, capability: str, target: dict | None = None) -> bool:
    if not actor or not is_staff(actor) or role_of(actor) not in CAN[capability]:
        return False
    if target is not None and not manages(actor, target):
        return False
    return True


def capabilities(user: dict) -> list[str]:
    return sorted(c for c in CAN if can(user, c))
