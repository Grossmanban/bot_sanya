"""ТЗ 27.10 §26–27: «Предзакреп» / «Основная команда» by confirmed work days, personal invitations."""

from datetime import date, timedelta
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from app import app_settings, groups
from app import services as svc

from .test_features import run
from .test_rates import did

PRE, MAIN = -1001, -1002


class FakeBot:
    """The parts of the Bot API the groups use; members[(chat, tg)] = status."""

    id = 42

    def __init__(self, rights=True):
        self.members: dict[tuple[int, int], str] = {}
        self.links: list[str] = []
        self.answers: list[tuple[str, int, int]] = []
        self.rights = rights

    async def get_chat_member(self, chat_id, user_id):
        if user_id == self.id:
            return SimpleNamespace(status="administrator", can_invite_users=self.rights, can_restrict_members=self.rights)
        if chat_id not in (PRE, MAIN):
            raise TelegramBadRequest(method=None, message="chat not found")
        return SimpleNamespace(status=self.members.get((chat_id, user_id), "left"))

    async def create_chat_invite_link(self, chat_id, name, expire_date, creates_join_request):
        assert creates_join_request  # a request the bot answers — never a link anyone may use
        self.links.append(f"https://t.me/+link{len(self.links)}")
        return SimpleNamespace(invite_link=self.links[-1])

    async def approve_chat_join_request(self, chat_id, user_id):
        self.answers.append(("approve", chat_id, user_id))
        self.members[(chat_id, user_id)] = "member"

    async def decline_chat_join_request(self, chat_id, user_id):
        self.answers.append(("decline", chat_id, user_id))

    async def ban_chat_member(self, chat_id, user_id):
        if not self.rights:
            raise TelegramBadRequest(method=None, message="not enough rights")
        self.members[(chat_id, user_id)] = "kicked"

    async def unban_chat_member(self, chat_id, user_id, only_if_banned):
        self.members[(chat_id, user_id)] = "left"


def connected(conn, owner):
    groups.connect(conn, "pre", PRE, "Предзакреп", owner["telegram_id"])
    groups.connect(conn, "main", MAIN, "Основа", owner["telegram_id"])


def test_work_days_count_dates_not_reports(conn, make_user):
    u = make_user(first="Иван")
    mon = date(2026, 10, 12)
    did(conn, u, mon, 25)
    did(conn, u, mon + timedelta(days=1), 25)
    conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status, fact_volume,
                    report_state, training, created_at, updated_at) VALUES (?, ?, '10:00', '18:00', 20, 'Европа', 'active',
                    5, 'approved', 1, 't', 't')""", (u["id"], (mon + timedelta(days=2)).isoformat()))  # a training shift counts
    conn.execute("""INSERT INTO shifts (user_id, shift_date, start_time, end_time, volume, location, status, fact_volume,
                    report_state, created_at, updated_at) VALUES (?, ?, '10:00', '18:00', 20, 'Европа', 'active', 5,
                    'pending_review', 't', 't')""", (u["id"], (mon + timedelta(days=3)).isoformat()))  # not confirmed yet
    assert groups.work_days(conn)[u["id"]] == (3, mon.isoformat())
    assert groups.expected(2, 3) == "pre" and groups.expected(3, 3) == "main"


def test_personal_invitation_only_for_that_person(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    connected(conn, owner)
    bot = FakeBot()
    u, other = make_user(first="Иван"), make_user(first="Чужой")
    inv = run(groups.invite(conn, bot, u, "pre", owner))
    # someone else came with the forwarded link — declined, nothing changes
    ok, who = run(groups.join_request(conn, bot, PRE, other["telegram_id"], inv["link"]))
    assert not ok and who["id"] == u["id"] and bot.answers[-1] == ("decline", PRE, other["telegram_id"])
    ok, _ = run(groups.join_request(conn, bot, PRE, u["telegram_id"], inv["link"]))
    assert ok and bot.answers[-1] == ("approve", PRE, u["telegram_id"])
    assert groups.states(conn)[(u["id"], "pre")]["state"] == "member"
    # an old link after a new one was made — no longer valid
    inv2 = run(groups.invite(conn, bot, u, "main", owner))
    run(groups.invite(conn, bot, u, "main", owner))
    assert not run(groups.join_request(conn, bot, MAIN, u["telegram_id"], inv2["link"]))[0]


def test_board_auto_invites_and_reconciliation(conn, make_user):
    """ТЗ 28.10 §6, §15: people without a questionnaire go straight to the main group; both groups are a problem
    only until the admin allows them; per-person groups and «Не новичок»."""
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    connected(conn, owner)
    bot = FakeBot()
    new, vet, both = make_user(first="Новичок"), make_user(first="Опытный"), make_user(first="Везде")
    bot.members[(PRE, vet["telegram_id"])] = "member"
    bot.members[(PRE, both["telegram_id"])] = "member"
    bot.members[(MAIN, both["telegram_id"])] = "member"
    sent = []

    async def notify(user, kind, inv):
        sent.append((user["first_name"], kind))
        return True

    out = run(groups.auto_invites(conn, bot, notify))
    assert sorted(sent) == [("Новичок", "main"), ("Опытный", "main")]  # «Везде» already in the main team
    assert len(out) == 2 and run(groups.auto_invites(conn, bot, notify)) == []  # once
    run(groups.check(conn, bot))
    rows = {r["name"].split()[0]: r for r in groups.board(conn)["items"]}
    assert rows["Новичок"]["required"] == ["main"] and rows["Новичок"]["issues"] == ["Не хватает: «Основная команда»"]
    assert rows["Новичок"]["invites"]["main"]["auto"] and rows["Новичок"]["action"] == "invite_main"
    assert rows["Опытный"]["issues"] == ["Не хватает: «Основная команда»", "Остался в «Предзакрепе»"]
    assert rows["Везде"]["issues"] == ["Состоит в обеих группах"]
    assert rows["Везде"]["problems"][0]["actions"] == ["remove_pre", "allow_both"]
    # В13: the admin allows both groups — it's normal now, and joining the main group won't move them out
    groups.set_mode(conn, owner, both["id"], "both", svc.new_key())
    rows = {r["name"].split()[0]: r for r in groups.board(conn)["items"]}
    assert rows["Везде"]["issues"] == [] and rows["Везде"]["required"] == ["pre", "main"]
    assert groups.keeps_both(conn, svc.get_user(conn, both["id"]))
    # a person kept in «Предзакреп» who got into the main team: «Не новичок — подтвердить» settles it, and it's undone
    groups.set_mode(conn, owner, new["id"], "pre", svc.new_key())
    bot.members[(MAIN, new["telegram_id"])] = "member"
    run(groups.check(conn, bot, {new["id"]}))
    row = next(r for r in groups.board(conn)["items"] if r["user_id"] == new["id"])
    assert "Новичок в основной команде" in row["issues"]
    groups.set_mode(conn, owner, new["id"], None, svc.new_key())
    groups.set_not_newbie(conn, owner, new["id"], True, svc.new_key())
    row = next(r for r in groups.board(conn)["items"] if r["user_id"] == new["id"])
    assert row["issues"] == [] and row["not_newbie"] and svc.get_user(conn, new["id"])["role"] == "employee"
    groups.set_not_newbie(conn, owner, new["id"], False, svc.new_key())
    assert not next(r for r in groups.board(conn)["items"] if r["user_id"] == new["id"])["not_newbie"]
    # moving out of «Предзакреп» needs the right to ban — without it, a clear error, nothing changes
    run(groups.remove(conn, bot, svc.get_user(conn, both["id"]), "pre", owner))
    assert groups.states(conn)[(both["id"], "pre")]["state"] == "out"
    weak = FakeBot(rights=False)
    try:
        run(groups.remove(conn, weak, svc.get_user(conn, vet["id"]), "pre", owner))
        raise AssertionError("expected no_rights")
    except svc.ServiceError as e:
        assert e.code == "no_rights"
    # unknown membership is never guessed: a group the bot can't see stays «unknown»
    assert run(groups.member_state(bot, -999, new["telegram_id"])) == "unknown"


def test_threshold_is_a_setting(conn, make_user):
    u = make_user(first="Иван")
    for i in range(2):
        did(conn, u, date(2026, 10, 12) + timedelta(days=i), 25)
    app_settings.update(conn, {"main_team_days": 2})
    assert groups.board(conn)["items"][0]["expected"] == "main"
