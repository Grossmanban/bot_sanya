# Справочник REST API

**Обновлено:** 2026-11-19 · **Исходники:** `backend/app/api.py` (все маршруты), фронтенд-клиент `frontend/src/lib/api.ts`

## Общие правила

- Префикс `/api`. Документация (`/docs`) отключена.
- Авторизация: заголовок `X-Telegram-Init-Data` (подпись Telegram, проверяется на каждом запросе, см. [[01 - Архитектура и стек]]). Ошибки: 401 `unauthorized`, 403 `not_registered`/статус/`blocked`/`forbidden`.
- Мутации требуют `idempotency_key` (повтор запроса безопасен). Ошибки домена: `{"detail": "...", "code": "..."}` c HTTP 4xx/409.
- Права: зависимость `staff(capability)` + `require(actor, capability, user_id)` для действий над конкретным человеком ([[03 - Роли и права]]).
- Медиа-GET (фото отчётов, призов, «Алисы», аватаров, картинок методичек) — открытые, но по подписанным ссылкам с `exp`/`sig`.
- Статика Mini App: `GET /healthz`, всё остальное — из `FRONTEND_DIST`.

## Профиль и доступ сотрудника

| Метод и путь | Что |
|---|---|
| `GET /api/me` | всё о текущем: user, role_title, capabilities, products, manageable_products, training, manuals, internship, pricing, global_link, header, events |
| `POST /api/register` | анкета кандидата |
| `GET /api/me/referrals` · `/qr` | реферальная ссылка (TTL, секунды), QR SVG, список приглашённых |
| `PATCH /api/me/work-status` | «Работаю/Не работаю» (18.11: сам сотрудник больше не переключает — 403; персонал свой статус меняет) |
| `PATCH /api/me/notifications` | включить/выключить напоминания |
| `POST /api/profile-link` | карточка человека без username (в чат с ботом) |
| `GET /api/points` | справочник точек |

## Профиль, реквизиты, история, смены

`GET/PUT /api/me/payment-profile` · `GET/POST /api/me/profile` (заявка) · `GET /api/me/penalties` (штрафы/бонусы) ·
`GET /api/me/finances` · `GET /api/schedule/week?offset=` · `GET /api/history` ·
`POST /api/shifts/create` · `PATCH /api/shifts/{id}` · `POST /api/shifts/{id}/cancel|restore|contact-admin|report` ·
`POST /api/shifts/pre-declare-off` · `POST /api/shifts/{id}/clear-off` · `GET /api/media/report/{id}`.

## Цели и события (сотрудник)

`GET /api/events` · `GET /api/events/{id}` · `POST /api/events/{id}/entries` (заявка: число+фото) ·
`GET /api/archive` · `GET /api/goals` · `POST /api/goals/{id}/join` · `GET /api/media/prize_{kind}/{id}` · `GET /api/media/event/{id}`.

## «Работа» (продукты; `{product}` = alice | plus)

> [!warning] 18.11: раздел выключен — `products.PRODUCTS_ENABLED = False`; все маршруты «Работы», «Алисы» и
> продуктовых выплат отвечают `409 products_disabled`, разделы в приложении скрыты. Возврат — `True`.

`GET /api/work` (список доступных) · `GET /api/work/{product}` · `POST …/start` · `POST …/{task}/scanned` ·
`PUT/DELETE …/{task}/photo/{stage}` · `POST …/{task}/goto/{stage}` (назад) · `POST …/{task}/next` · `POST …/{task}/finish` ·
`POST …/{task}/retry` · `POST …/{task}/abandon` · `GET /api/media/alice/{photo}` · `GET /api/media/aliceqr/{qr}`.
Обучение: `GET /api/manuals` · `GET /api/manuals/{id}` · `POST /api/manuals/{id}/sections/{sid}/read` · `GET /api/media/manualimg/{id}` · `GET /api/media/avatar/{user_id}`.

## Админ: команда, доступ, группы

`GET /api/admin/users` · `GET /api/admin/team` (18.11: держателю `money` — `lazy_pool`, копилка налога на лень человека) ·
`POST /api/admin/team/{uid}/role|access|state|work-status|norm|network|instructor|rates|training-access|products|manuals|payment-profile|profile`
(`work-status`, 18.11: `{work_status}` — переключают только начальники: инструктор своим, тренер своей сети, админ/владелец любому; 403 у чужих;
`rates` (19.11): тело с `fines_exempt` — при `true` висящие нарушения и неявки человека прощаются, ответ несёт `cleared {violations, no_shows}`) ·
`POST /api/admin/team/{uid}/grants` — личные разрешения `{broadcast_all?, onboarding?}` (owner/admin, 18.11; в `GET /api/me`
и «Команде» поля приходят как `grants`) ·
инвайты `GET/POST /api/admin/invites`, `/{id}/revoke|qr`, `POST /api/admin/invites/rotate` · попытки доступа
`GET /api/admin/access-attempts`, `POST /api/admin/access-attempts/restore` · группы `GET/POST /api/admin/groups(/check|/invite|/mode|/not-newbie|/remove)`.

## Админ: смены, день, отчёты, дисциплина, факт дня

`GET /api/admin/raspred(/today|/tomorrow)` · `GET /api/admin/day` · `POST /api/admin/day/{day}/mark|remind|fine` ·
`GET /api/admin/reports(/counts)` · `GET /api/admin/stats` · `GET /api/admin/penalties` · `PATCH /api/admin/shifts/{id}` ·
`POST /api/admin/shifts/{id}/review|no-show|result|delete` · `GET /api/admin/violations` · `POST /api/admin/violations/scan` ·
`POST /api/admin/violations/{id}/fine|waive` · `GET /api/media/violation/{id}/{index}` ·
факт дня `GET /api/admin/day-actuals`, `POST /api/admin/day-actuals/{day}`, `POST …/{day}/annul` · `GET /api/media/tx/{id}`.

## Админ: деньги

`GET /api/admin/finances(/summary)` · `POST /api/admin/finances` · `POST /api/admin/finances/{tx}/decide|cancel|forgive|amount` ·
`POST /api/admin/finances/pay-all` · `POST /api/admin/finances/pieces` (вычет в штуках; 18.11 — ставка сотрудника
личная/общая базовая, части тренера и инструктора) ·
выплаты `POST /api/admin/payouts/preview` (18.11: `manual: true` + `amounts` + `included` — ручные суммы; без них —
легаси-авторасчёт), `POST /api/admin/payouts` (choices с `amount`), `GET /api/admin/payouts(/{id})` (в списке — флаг `ocr`),
`POST /api/admin/payouts/{id}/edit|annul|excel` · `POST /api/admin/lazy-pool/reset` (18.11: обнулить копилки налога на лень) ·
`POST /api/admin/payouts/parse-photo?network_id=` (18.11: raw-картинка ведомости → строки с сопоставлением; 409 `ocr_not_configured`) ·
продуктовые выплаты `POST /api/admin/alice/payout/preview|commit`.

## Админ: цели, события, сети

`GET/POST /api/admin/goals` · `PATCH /api/admin/goals/{id}` · `POST …/{id}/visibility|archive|delete|revoke|annul` ·
`GET/POST /api/admin/events` · `PATCH /api/admin/events/{id}` · `POST …/{id}/visibility|delete|cancel` ·
`POST /api/admin/event-entries/{id}/annul|review` · `GET/POST/PATCH /api/admin/networks(/{id})`.

## Админ: онбординг и обучение

`GET /api/admin/onboarding` (18.11: у тренеров в `trainers` — `role` для приоритета в списке; 19.11: в анкетах
`priority` и `waiting {hours, text}` — «сколько ждёт» в «Ожидают рассмотрения») ·
`GET /api/admin/onboarding/{app}/trainer-preview` ·
`POST /api/admin/onboarding/{app}/trainer|trainer-message|timer-reset|priority|remind-referrer|training|reject|cancel-training|start-training|cancel-ack|return|contact|internship|stage|internship-units`
(`internship-units`, 18.11: `{units}` — правка зачёта стажировки владельцем; цель закрывает стажировку;
`priority`, 19.11: `{on}` — приоритетное обучение на любой анкете) ·
`POST /api/admin/training-requests/{id}/decide` · тренер: `GET /api/training`, `POST /api/training/{app}/request|confirm|schedule|cancel|finish`.

## Админ: Алиса/Плюс, методички, рассылки, настройки

Алиса: `GET /api/admin/alice(/queue|/qr|/queries|/stats)` · `POST /api/admin/alice/{task}/decide|retry|delete` ·
`POST /api/admin/alice/qr`, `PUT/PATCH /api/admin/alice/qr/{id}`, `POST …/assign`, `GET …/history`, `PUT /api/admin/alice/queries`.
Методички: `GET/POST /api/admin/manuals`, `GET/PATCH/POST /api/admin/manuals/{id}(/delete|/sections|/upload|/document)`.
Рассылки: `GET/POST /api/admin/broadcasts` (18.11: в `people` — флаги `shift_today`/`shift_tomorrow`, аудитории
`shift_today`/`shift_tomorrow` валидны и в `POST`; 19.11: в POST — `ack_deadline_min` (5 мин…30 дней, включает
`require_ack`) и `ack_fine_units` (1…1000, None — без штрафа); в списке — `ack_deadline_at`, `fined`),
`GET /api/admin/broadcasts/{id}` (в `items` — `fined_at`), `GET /api/media/broadcast/{id}`,
`POST …/{id}/remind`, `POST /api/admin/broadcasts/test`, шаблоны `/api/admin/broadcast-templates`.
Настройки: `GET/PATCH /api/admin/settings` · `GET/POST/PATCH /api/admin/reasons(/{id})` · `GET/PUT /api/admin/texts/{key}` ·
`GET/PUT/DELETE /api/admin/header-title` · `GET/PUT/DELETE /api/admin/global-link` · `GET /api/reasons` · `POST /api/admin/media(/video)`.
Счётчики: `GET /api/admin/pending` (18.11: ключ `no_network` — сотрудники без сети, для владельца/админов). Экспорт: `POST /api/admin/export`.

> [!note] Новые эндпоинты — дописать в эту таблицу (регламент [[00 - Регламент документации]]).

## История изменений

- 2026-11-17 — страница создана по всем маршрутам коммита `78c73da`.
- 2026-11-18 — добавлен `POST /api/admin/team/{uid}/grants` (личные разрешения), поле `grants` в user.
- 2026-11-18 — превью/коммит выплат: `manual`/`amounts`/`included`/`amount`; новый `POST /api/admin/lazy-pool/reset`.
- 2026-11-18 — добавлен `POST /api/admin/payouts/parse-photo` («Заполнить по фото»), флаг `ocr` в списке выплат.
- 2026-11-18 — `GET /api/admin/team`: держателю `money` приходит `lazy_pool` (копилка налога на лень, шт.).
- 2026-11-18 — `POST /api/admin/team/{uid}/work-status`: «Работаю» переключают только начальники; `PATCH /api/me/work-status`
  самим сотрудником — 403.
- 2026-11-18 — `POST /api/admin/onboarding/{app}/internship-units`; `GET /api/admin/pending`: `no_network`;
  `GET /api/admin/onboarding`: `role` в списке тренеров.
- 2026-11-18 — `GET /api/admin/broadcasts`: в `people` флаги `shift_today`/`shift_tomorrow`; аудитории `shift_today`/`shift_tomorrow` в `POST`.
- 2026-11-18 — `POST /api/admin/finances/pieces`: вычет всегда по базовой ставке сотрудника (личная/общая), в долю
  добавлен инструктор человека.
- 2026-11-18 — «Алиса»/«Яндекс Плюс» выключены (`products.PRODUCTS_ENABLED = False`): `/api/work*`, `/api/admin/alice*`,
  `/api/admin/team/{uid}/products` и продуктовые штрафы отвечают `409 products_disabled`; `GET /api/me` отдаёт
  `products_enabled`.
- 2026-11-19 — `POST /api/admin/broadcasts`: `ack_deadline_min`/`ack_fine_units` (срок отметки + автноштраф);
  `GET /api/admin/broadcasts(/​{id})`: `ack_deadline_at`, `fined`, `fined_at`.
- 2026-11-19 — `POST /api/register`: `priority`; `POST /api/admin/onboarding/{app}/priority` (`{on}`);
  в анкетах `priority` и `waiting`, `GET /api/me`: `application.priority`.
- 2026-11-19 — `POST /api/admin/team/{uid}/rates`: `fines_exempt` («без штрафов», миграция 49); ответ несёт
  `cleared {violations, no_shows}`; в людях `GET /api/admin/team` и `public_user` — `fines_exempt`.
