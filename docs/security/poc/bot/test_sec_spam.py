import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from harness import make, call_update, feed
from app import invites, recruiting, services as svc
from app.db import connect


def test_candidate_toggles_buttons_to_spam_admins():
    conn = connect(":memory:")
    bot, sess, dp, _ = make(conn)
    owner = svc.ensure_admin(conn, 999, "O", "W", "own")
    emp, _ = svc.create_registration(conn, 2001, "emp", "E", "M")
    emp, _ = svc.decide_registration(conn, emp["id"], True, 999)
    link = invites.current_ref(conn, emp)
    invites.admit(conn, {"id": 3001, "first_name": "c"}, f"ref_{link['token']}")
    _, app = recruiting.submit(conn, {"id": 3001}, {"full_name": "Канд Кандидатов", "age": 20, "metro": "x", "min_days": 2,
                                                   "training_days": [1], "communication": 5, "consent": True, "truthful": True})
    recruiting.contact(conn, owner, app["id"], "boss")
    sess.calls.clear()
    for i in range(20):
        feed(bot, dp, call_update(3001, f"{'ctw' if i % 2 == 0 else 'ctc'}:{app['id']}"))
    to_owner = [c for c in sess.calls if type(c).__name__ == "SendMessage" and int(c.chat_id) == 999]
    print(len(to_owner))
    assert len(to_owner) == 20
