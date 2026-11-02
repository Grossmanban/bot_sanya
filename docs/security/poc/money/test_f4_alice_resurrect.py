"""F4: a task deleted while its album is being sent comes back as «На модерации» (deliver writes without a status
guard after the await) and can then be accepted and paid."""
from aiogram.exceptions import TelegramBadRequest

from app import alice, products
from app import services as svc
from tests.test_alice import JPG, ForumBot, full_run
from tests.test_features import run


class DeleteWhileSending(ForumBot):
    def __init__(self, conn, admin, fail=False):
        super().__init__()
        self.conn, self.admin, self.fail_after = conn, admin, fail

    async def send_media_group(self, chat_id, media, message_thread_id):
        task_id = int(media[0].media.filename.split("_")[0])
        # meanwhile (the await) another request: the admin deletes the task
        alice.delete_task(self.conn, self.admin, task_id, "дубль", svc.new_key())
        assert alice.get(self.conn, task_id)["status"] == "deleted"
        if self.fail_after:
            raise TelegramBadRequest(method=None, message="Too Many Requests: retry later")
        return await super().send_media_group(chat_id, media, message_thread_id)


def _world(conn, make_user):
    owner = svc.ensure_admin(conn, 999, "Саня", "Владелец", "boss")
    u = make_user(first="Иван")
    alice.connect_forum(conn, -1009, "Алиса")
    products.set_access(conn, owner, u["id"], "alice", True, svc.new_key())
    qr = alice.create_qr(conn, owner, "local:qr.png", "QR-1", svc.new_key())
    alice.assign(conn, owner, qr["id"], u["id"], True, svc.new_key())
    return owner, u


def test_deleted_task_is_resurrected_by_a_successful_send(conn, make_user):
    owner, u = _world(conn, make_user)
    t = full_run(conn, u, JPG + b"1")
    task = run(alice.deliver(conn, DeleteWhileSending(conn, owner), t["id"]))
    assert task["status"] == "moderation" and task["deleted_at"] is not None  # deleted, yet back in the queue
    task, changed = alice.moderate(conn, owner, t["id"], True, None, svc.new_key())
    assert changed and task["status"] == "accepted"


def test_deleted_task_is_resurrected_by_a_failed_send(conn, make_user):
    owner, u = _world(conn, make_user)
    t = full_run(conn, u, JPG + b"2")
    task = run(alice.deliver(conn, DeleteWhileSending(conn, owner, fail=True), t["id"]))
    assert task["status"] == "send_failed"  # will be retried by the job and land in moderation
    assert t["id"] in alice.to_retry(conn)
