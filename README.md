# SWAGcleaner

Тулза для ПК. Локальная, без отправки данных в сеть, без регистраций, без слежения.

SWAGcleaner — это программа для Windows, которая помогает разобраться с тем, что происходит на компьютере, и аккуратно его привести в порядок. Всё работает на вашем ПК, ничего не уходит куда-то ещё.

Что внутри:

- Сканер — показывает установленные программы, процессы, службы, элементы автозагрузки. Только чтение, без изменений.
- Советник — на основе простых правил предлагает, что можно проверить или отключить (старт программ, ненужные службы, потенциально лишнее ПО). Не «решает за тебя», а выдаёт список с пояснениями, чтобы ты сам решил.
- Оптимизация и твики — в будущем будут доступны отключение лишних служб, управление автозапуском, удаление встроенных приложений (UWP). Каждое действие требует подтверждения и делает бэкап перед этим.
- Поиск дубликатов фото — находит одинаковые и похожие фотографии по хешам (точным и визуальным). Скрытое «умное» сравнение идёт через обычные алгоритмы, а не через интернет — всё на машине.
- Чистка — ищет временные файлы, кэши, мусор и кидает их в корзину (не удаляет навсегда без спроса).

Главные принципы:

1. Только локально. Никаких запросов в интернет. Если в приложении появятся функции, связанные с моделями, они будут запускаться у вас дома, а не на чужом сервере.
2. Ничего не Deleting'ится само. Удаление, отключение, удаление приложений — всё с подтверждением. Дефолт — только чтение.
3. Бэкапы. Перед каждым важным изменением программа сохраняет состояние, чтобы можно было откатиться.
4. Приватность по умолчанию. Сканируемые данные не собираются, не передаются, не оставляют логов в сети. Всё остаётся на машине.

Техническая часть (если интересно):

- Язык — Python.
- UI — PySide6 (Qt).
- Для поиска дубликатов фото — точные хеши и perceptual hashing через imagehash.
- Для удаления в корзину — send2trash.
- Для работы с системой — собственные модули, без лишних зависимостей.
- Проект задуман как нарастающий: сначала ядро и сканер, потом советник, потом UI и твики, потом Can expand и всякое такое.

Статус:

Это каркас и первая рабочая часть — ядро. Пока нет готового десктопного интерфейса с кнопками, но логика соображений уже есть: сканер, правила, чистка, бэкапы, поиск дубликатов. Всё это можно тестировать, развивать и доделывать.

Сборка:

Для запуска нужен Python и зависимости из requirements.txt. Виртуальное окружение создаётся через venv. Запуск — через run.bat (для Windows) или python -m swagcleaner (когда entry point готов).

Почему так:

Задумка — дать человеку инструмент, который разбирается в системе без лишних разговоров с серверами. Без облачного анализа, без передачи списка программ, без скрытых зависимостей. Утилита, которая работает у вас на машине и не требует «входа», «аккаунта» и прочей музики.

MIT License.

---

# SWAGcleaner

A local PC tool. Runs on your machine, sends nothing online, no accounts, no tracking.

SWAGcleaner is a Windows utility that helps you understand what's going on in your system and tidy it up — safely and quietly. Everything stays on your PC.

What it does:

- Scanner — shows installed apps, running processes, services, startup items. Read-only by default.
- Advisor — based on simple rules, it suggests what's worth checking or disabling (startup programs, unnecessary services, likely bloatware). It doesn't decide for you; it hands you a list with explanations so you choose.
- Tweaks & optimization — future functions will include disabling extra services, managing startup, removing built-in apps (UWP). Every action needs confirmation and is done with a backup first.
- Photo duplicate finder — detects identical and visually similar photos using hashes (exact + perceptual via imagehash). Comparison runs locally, no cloud involved.
- Cleaner — finds temp files, caches and junk, and sends them to the Recycle Bin (not permanent deletion without asking).

Core principles:

1. Fully local. No internet calls. If any model-based feature is added later, it will run on your own machine.
2. Nothing gets deleted by itself. Removals and disables need your confirmation. Default mode is read-only.
3. Backups. Before any meaningful change, the app saves the current state so you can roll back.
4. Privacy by default. Scanned data is not collected, never sent anywhere, and doesn't leave logs on the network. Everything stays on your device.

Under the hood:

- Python.
- UI via PySide6 (Qt).
- Duplicate photo detection via exact hashes and perceptual hashing (imagehash).
- Recycle Bin deletion via send2trash.
- Custom system modules, keeping dependencies minimal.
- Designed to grow in layers: core and scanner first, then advisor, UI, tweaks, and more over time.

Status:

This is the core layer — the foundation. The desktop interface isn't finished yet, but the reasoning pieces are already here: scanning, rules, cleaning, backups, duplicate finding. It's ready to be tested, extended, and shaped into the full app.

Running it:

You need Python and the dependencies listed in requirements.txt. Use a venv. On Windows, run.bat starts it (once the entry point is ready). For now the core can also be exercised directly from code or CLI when the entry point exists.

Why it's built this way:

The goal is a tool that understands your system without phoning home. No cloud analysis, no app lists sent anywhere, no hidden dependencies. A utility that lives on your machine and doesn't ask for a login, an account, or anything else along those lines.

MIT License.
