"""Who may do what. One place for the role rules so the API, the bot and the UI agree.

Roles (agreed with the owner, ТЗ v9.1 + one extra):
  owner    — Super Admin: ADMIN_IDS from config, there may be several. Everything, incl. money
             (deductions, payouts), blocking accounts, roles, the day's global output.
  admin    — works with everyone: schedule, reports, fines without approval, bonuses, pauses,
             broadcasts. No money operations, no blocking, no roles.
  trainer  — like an admin but only for people of their own network; fines go to an owner for approval.
  instructor — (ТЗ 27.10 §22–23) works under the network's trainer with the people assigned to them only:
             schedule, reports, discipline; fines go for approval. The role alone opens nobody else.
  employee — their own schedule, reports and balance.
"""

from .config import settings

OWNER, ADMIN, TRAINER, INSTRUCTOR, EMPLOYEE = "owner", "admin", "trainer", "instructor", "employee"
ROLE_TITLES = {OWNER: "Владелец", ADMIN: "Администратор", TRAINER: "Тренер", INSTRUCTOR: "Инструктор", EMPLOYEE: "Сотрудник"}


def is_owner(user: dict | None) -> bool:
    return bool(user) and user["telegram_id"] in settings.admin_ids


def role_of(user: dict | None) -> str:
    if not user:
        return EMPLOYEE
    if is_owner(user):
        return OWNER
    return user["role"] if user["role"] in (ADMIN, TRAINER, INSTRUCTOR) else EMPLOYEE


def is_staff(user: dict | None) -> bool:
    """Has the admin panel at all (scope may still be limited for trainers)."""
    return role_of(user) in (OWNER, ADMIN, TRAINER, INSTRUCTOR) and user["status"] == "approved" \
        and user["account_state"] != "blocked"


def sees_everyone(user: dict | None) -> bool:
    return role_of(user) in (OWNER, ADMIN)


def manages(actor: dict | None, target: dict | None) -> bool:
    """Actor may act on target's schedule/reports/discipline."""
    if not actor or not target or not is_staff(actor):
        return False
    if sees_everyone(actor):
        return True
    if role_of(actor) == INSTRUCTOR:  # only the people assigned to them
        return target.get("instructor_id") == actor["id"] and target["id"] != actor["id"]
    # a trainer — the network they lead (networks.trainer_id, ТЗ 28.10 В32); set_role/set_member keep a trainer the head
    # of their own network, so the fallback to network_id only covers rows read without led_network_id
    led = actor.get("led_network_id") or actor.get("network_id")
    return led is not None and led == target.get("network_id") and target["id"] != actor["id"]


# capability -> roles that have it (trainers additionally need manages(actor, target))
CAN = {
    "money": {OWNER},               # deductions, payouts, paying off debts in bulk
    "block": {OWNER},
    "roles": {OWNER},
    "global_actual": {OWNER},
    "networks": {OWNER},
    "fine_direct": {OWNER, ADMIN},  # trainers' fines wait for an owner or an admin
    "approve_fines": {OWNER, ADMIN},
    "forgive_fines": {OWNER, ADMIN, TRAINER, INSTRUCTOR},  # «Простить» a fine, the reason is optional; a trainer — their network
    "fine_request": {TRAINER, INSTRUCTOR},
    "bonus": {OWNER, ADMIN, TRAINER},
    "pause": {OWNER, ADMIN, TRAINER, INSTRUCTOR},
    "review": {OWNER, ADMIN, TRAINER, INSTRUCTOR},
    "schedule": {OWNER, ADMIN, TRAINER, INSTRUCTOR},
    "broadcast_all": {OWNER, ADMIN},
    "broadcast_network": {OWNER, ADMIN, TRAINER, INSTRUCTOR},  # an instructor — only the people assigned to them
    "settings": {OWNER, ADMIN},
    "points": {OWNER, ADMIN},
    "registrations": {OWNER, ADMIN},
    "profiles": {OWNER, ADMIN},
    "manuals": {OWNER, ADMIN},      # ТЗ 28.10 §12: create, upload, edit, give and take back manuals     # ТЗ 28.10 §7: approve, reject and edit employees' profiles (payment details — money)
    "remind_profiles": {OWNER, ADMIN, TRAINER, INSTRUCTOR},  # «📢 Напомнить о заполнении»: a trainer — their people
    "export": {OWNER, ADMIN, TRAINER, INSTRUCTOR},
    "raspred": {OWNER, ADMIN, TRAINER, INSTRUCTOR},  # the list for the work chat; a trainer gets their network's
    "all_people": {OWNER, ADMIN},   # works with the whole team, not one network (company-wide goals, events)
    "invites": {OWNER},             # ТЗ 27.10 §6, §8: owner invitations, «Обновить все ссылки»
    "access_log": {OWNER, ADMIN},   # ТЗ 27.10 §5: «Попытки доступа»
    "groups": {OWNER, ADMIN},       # ТЗ 27.10 §26–27: «Предзакреп» / «Основная команда»
    "products": {OWNER, ADMIN},     # ТЗ 27.10 §28: give and take access to «Работа» products
    "alice": {OWNER, ADMIN},        # ТЗ 27.10 §32, §38: QR codes, search queries, moderation, statistics
}


def can(actor: dict | None, capability: str, target: dict | None = None) -> bool:
    if not actor or not is_staff(actor) or role_of(actor) not in CAN[capability]:
        return False
    if target is not None and not manages(actor, target):
        return False
    return True


def capabilities(user: dict) -> list[str]:
    return sorted(c for c in CAN if can(user, c))
