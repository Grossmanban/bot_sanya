# Фронтенд Mini App

**Обновлено:** 2026-11-18 · **Исходники:** `frontend/` (Next.js 15, статический экспорт), вход `src/components/mini-app.tsx`, клиент `src/lib/api.ts`

## Устройство

- Next.js 15 + React 19 + shadcn/ui (radix) + Tailwind 4 + lucide. `npm run build` → **static export** в `frontend/out` — его раздаёт бэкенд ([[01 - Архитектура и стек]]). `npm run dev` — прокси `/api` на :8000.
- SDK Telegram загружается синхронно в `<head>`; вне Telegram — экран «Откройте из Telegram» (API без initData = 401).
- Вход: `MiniApp` грузит `GET /api/me`. Незарегистрированным — анкета (`register/register.tsx`) или «Нет доступа» (access-screens). Кандидат видит статус анкеты.
- **Вкладки** (`mini-app.tsx` TABS): «Работа» (по `products`), «График», «Обучение» (по `training`/`manuals`), «События» (пока есть активные), «Профиль», вкладка руководителя — «Админ» у owner/admin, «Тренер» у тренера, «Инструктор» у инструктора (`STAFF_TAB`). Открытая вкладка переживает перезагрузку (`sessionStorage`), кнопка из бота `?open=events` открывает «Админ → События» с подсвеченной заявкой.
- Шапка: аватар, имя, роль (цветная точка по роли), надпись над ролью (настройка `header-title`, ТЗ 29.10 §7), кнопка в другой бот (`global_link`), ☀️/🌙.
- Тема: авто по Telegram / светлая / тёмная (`theme-toggle`, `lib/theme.tsx`, localStorage).
- Глубокие ссылки: `lib/link.ts` (`takeLink`), память скролла `lib/scroll.ts`, счётчики «ждут решения» каждые 30 с — `lib/pending.ts`.

## Экраны сотрудника (components/)

| Путь | Экран |
|---|---|
| `schedule/schedule-screen.tsx`, `day-sheet.tsx`, `shift-form.tsx`, `day-line.tsx` | неделя пн–вс, шторка дня: время, точка, объём с «налогом на лень» и планом заработка, «Выйду/Не выхожу», кнопка «Сдать отчёт», «Оштрафовать», коллеги |
| `report/report-block.tsx`, `photo.tsx` | сдача отчёта (факт + камера/фото), статусы проверок |
| `profile/profile-screen.tsx`, `person-form.tsx`, `quick-access.tsx`, `referrals.tsx` | профиль: Работаю/Не работаю, история, штрафы/бонусы (единицы, штуки к оплате, копилка), «Мои данные» (заявка), реквизиты, «Быстрый доступ» (иконка на экран, `t.me/<бот>?startapp`), «Мои рефералы» (ссылка+QR+таймер) |
| `work/work-screen.tsx` | выполнения продукта: этапы, полоска «Назад», камера, «Прервать», статистика/история |
| `training/training-screen.tsx`, `manuals.tsx`, `manual-frame.tsx`, `manual-editor.tsx`, `shared.tsx` | «Обучение»: кандидаты тренера, методички (изолированная рамка), редактор методичек (для админов) |
| `events/live.tsx` | событие: плашки условий, таймер, рейтинг 5-сек обновление, заявка (число+скрин) |
| `goals/goals.tsx` | карточка «Цели» на графике, «Участвую», прогресс |
| `archive.tsx`, `lifecycle.tsx` | архив целей/событий; общая логика ленты/версий |
| `register/register.tsx` | анкета кандидата (4 шага + черновик + согласия) |

## Экран руководителя (components/admin/)

`admin-screen.tsx` — группы разделов (ТЗ 28.10 §27): **Решения · Люди** (team, onboarding, groups, access) · **Смены**
(raspred, day, reports, discipline, actual, stats) · **Деньги** (penalties, payouts) · **Продукты** (alice, plus, manuals,
goals, events) · **Связь и настройки** (broadcast, points, networks, settings). Раздел виден по capability
(`need: …` из [[03 - Роли и права]]), бейджи «ждут решения» на разделах и суммарный на вкладке.

Крупные компоненты: `team-section.tsx` (карточка человека: роли, состояние, ставки, доступ, продукты, методички, профиль,
«Личные разрешения» — тумблеры «Рассылка всем» и «Онбординг без админа» для employee/trainer/instructor, capability `grants`, 18.11),
`onboarding-section.tsx`, `broadcast-section.tsx` (+ `rich-editor.tsx` — виз. редактор HTML), `alice-admin.tsx`,
`payouts-section.tsx` (мастер выплат; шаг 2 «Команда и суммы» (18.11): галочки-подсветка, пустые поля сумм,
«≈ N шт.» и справка план/факт, строка «Команда: N шт. × R ₽», «Пересчитать»), `money.tsx`/`finance-section.tsx` (журнал, решения), `reports.tsx`, `events-admin.tsx`,
`groups-section.tsx`, `access-section.tsx` (инвайты, попытки), `networks-section.tsx`, `templates-section.tsx` (причины/тексты),
`day-actual-section.tsx`, `admin-shift-sheet.tsx` (правка смены), `shortfall.tsx`, `admin-extra.tsx` (точки, настройки), `violations-section.tsx`.

## Клиент API (`lib/api.ts`, ~1800 строк)

Все типы и методы к [[18 - API справочник]]; идемпотентные ключи генерируются клиентом; `ApiError` с `code`.
Утилиты: `caps.tsx` (по capabilities), `format.ts` (числа/даты/₽), `image.ts` (сжатие фото перед RAW-загрузкой),
`telegram.ts` (WebApp, haptic, тема, initData), `reasons.ts` (шаблоны причин), `tone.ts` (цвета статусов).

## Правила при изменениях

- Новые разделы админки: добавить в `SECTIONS`/`GROUPS` с `need:`-capability; сервер всё равно проверяет право.
- Мини-App на телефоне: ширина ограничена («День» не шире экрана — ТЗ 30.10 §1), тёмная/светлая темы обязательны.
- После сборки проверить `npm run lint && npm run build` ([[23 - Тесты и проверки]]).

## История изменений

- 2026-11-18 — страница создана (коммит `78c73da`).
- 2026-11-18 — «Команда»: блок «Личные разрешения» (рассылка всем / онбординг без админа); вкладка «Админ» открывается и держателю гранта (`isStaff`).
- 2026-11-18 — мастер выплат: шаг «Команда и суммы» (ручные суммы, галочки, справки, «Пересчитать»); «Настройки → Ставки»: сброс копилок налога на лень.
