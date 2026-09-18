# SWAGcleaner

Локальный клинер для Windows. Работает целиком на твоей машине: ни одного сетевого запроса,
никаких аккаунтов, никакой телеметрии. Ни одно удаление и ни одно отключение не выполняется
без подтверждения.

Память проекта (честный аудит «что работает, а что заглушка», план внутрянки, история решений и
промтов) — в [`context.md`](context.md). Если что-то расходится с этим README — прав `context.md`.

## Что внутри

- **Rust-ядро сканирования** — `rust/swagscan/`: многопоточный обход, агрегаты «куда ушло место»,
  стрим кандидатов, дубликаты по BLAKE3 в три стадии, удаление в две дорожки, отмена. Отдельный
  бинарь `swagscan.exe` (~0.8 МБ), говорит с оболочкой NDJSON-протоколом; в 2–2.5 раза быстрее
  Python-обхода на равных данных, суммы сверяются до байта.
- **Мост Python→Rust** — `core/swagscan.py`: один живой процесс ядра, сериализация команд,
  отмена через stdin, поиск бинаря (env → сборка → dev-пути).
- **Сканер системы** — установленные программы (реестр, HKLM 64/32 + HKCU), процессы, службы,
  элементы автозагрузки. По умолчанию только чтение.
- **Категории мусора** — девять правил в ядре: temp, кеши браузеров и приложений, дампы, логи,
  остатки обновлений, старые установщики, крупные старые файлы. У каждой — дорожка удаления,
  риск и «вернётся ли само».
- **Советник** — набор правил вместо «умного решения за тебя»: каждая рекомендация с причиной,
  риском и весом, итог сортируется по влиянию.
- **AI-слой (опционально)** — `ai/`: помощница-объяснитель поверх результатов. По умолчанию
  **выключен**; работает через локальный Ollama или OpenAI-совместимый эндпоинт, который
  настраиваешь сам. Ни одного запроса без явного включения.
- **CLI** — `--disk` (куда ушло место), `--candidates` (мусор по категориям), `--explain`
  (пояснение AI), `--json`.
- **Дубликаты фото** — точные хеши (SHA-256 в Python-слое, BLAKE3 в ядре) и похожие
  (perceptual hashing через `imagehash`).
- **Твики** — службы, автозагрузка, UWP (в планах; сначала чтение, запись — только со снапшотом).

## Стек

| Слой | Технология |
|---|---|
| Ядро сканирования/удаления | Rust (stable GNU-toolchain, `rayon`, `blake3`, Win32 API напрямую) |
| Оболочка | Python 3.12 + PySide6 (Qt widgets), без веб-технологий |
| Мост | NDJSON-протокол поверх stdin/stdout, один долгоживущий процесс |
| AI (опция) | stdlib `urllib` → Ollama / OpenAI-совместимый API |
| Сборка | PyInstaller (spec подкладывает `swagscan.exe`) |
| Тесты | pytest (225) + `cargo test` (38) |
| Полигон | VirtualBox + Windows 11 24H2 (тесты удаления не на живом железе) |

## Как работает чистка (политика удаления)

Клинер не решает за тебя, что мусор. Он находит, показывает объём и риск по каждому пункту и
делает только то, что ты подтвердил. Способ удаления выбирает **категория**, а не путь: путь
относит файл к категории, категория несёт способ удаления.

- **В корзину** — всё, что человек может захотеть вернуть: личные файлы (загрузки, документы,
  фото, видео), дубликаты фото, крупные и старые файлы. Откат полный: обычное «Восстановить»
  в корзине.
- **Сразу, мимо корзины** — только регенерируемое и только в известных системных местах:
  `%TEMP%`, `%SystemRoot%\Temp`, кеши браузеров и приложений, эскизы, логи, отчёты об ошибках,
  дампы, осиротевшие файлы установщиков и обновлений Windows. Такое содержимое создаётся заново
  само, а корзина на десятки гигабайт — не откат, а второй мусор. В диалоге такие пункты помечены
  **«без корзины — не восстановить»**, по умолчанию они не отмечены — их выбирают вручную.
- **Никогда и ни при каких настройках** — то, что ломает систему или необратимо: `WinSxS`, точки
  восстановления, `pagefile.sys`, `hiberfil.sys`, правки реестра «пакетом», содержимое папок
  установленных программ, файлы вне белого списка мест, симлинки и junction-точки (по ним можно
  снести чужие данные), любые личные файлы без явной галочки.
- **Ничего без подтверждения.** Диалог показывает пункты, объём, риск и итог «освободится N».
  По умолчанию отмечено только безопасное; опасное и необратимое — пустое.
- **Сбой одного действия не роняет остальные.** Ошибки собираются списком и показываются в отчёте.
- **Способ удаления выбирает категория, а не путь.** Путь только относит файл к категории;
  категория знает, как её убирать и что будет с откатом.

### Как проходит сессия чистки

1. **Индекс диска.** Один быстрый проход: где что лежит, сколько занимает, когда менялось.
2. **Категории.** Индекс фильтруется правилами: временное, кеши, логи, обновления, эскизы,
   корзина, дампы, брошенные установщики, дубликаты фото, крупные и старые файлы. У каждой
   категории есть причина, риск и способ удаления («вернётся само» / «только вручную»).
3. **Показ.** Пункты с галочками и объёмом по каждому, итог «освободится N», а дерево по объёму
   отвечает на главный вопрос забитого диска — «куда вообще ушло место».
4. **Подтверждение.** Лишние галочки снимаются в диалоге; про необходимость прав администратора
   предупреждение появляется сразу, а не системным окном посередине работы.
5. **Выполнение.** Пакетными операциями, с прогрессом по категориям и возможностью отменить.
6. **Отчёт.** Сколько освободилось, сколько файлов, что можно вернуть из корзины и что удалено
   без возможности восстановления. Строка в журнале — на каждое действие.

Полное описание конвейера (индекс, категории, дубликаты, службы, откат, план по Rust) —
разделы 8 и 9 в [`context.md`](context.md).

## Статус на 16.09.2026 (честно)

Интерфейс — настоящий: боковое меню, пять страниц, две темы, ru/en, пиксельный шрифт Handjet,
звуки, помощница в стиле визуальной новеллы, диалог подтверждения, живые счётчики, фоновые
задачи, 239 + 40 тестов.

Ядро сканирования — настоящее и проверенное: Rust-обход, категории, дубликаты BLAKE3, удаление
в две дорожки, dry-run, отмена; мост и протокол покрыты интеграционными тестами; на живой машине
сверка Rust/Python до байта. Полигон VirtualBox (Win11 24H2) прогоняет ядро целиком: посев
реальных файлов → сверка категорий → реальное удаление → CRC32-сверка; с 16.09 весь бандл живёт
на общих папках хоста, в госте копий нет. Собранный `.exe` проверен внутри VM: `--self-test` и
`--disk --candidates` работают, скриншоты GUI — в `shots/polygon-2026-09-16/`.
Интерфейс подключён к ядру через слой-сессию: скан и удаление идут в настоящее ядро, числа на
плитках — из `ScanResult`, прогресс — из событий ядра (включая живой прогресс удаления). После
скана открывается **экран категорий**: карточки с галочками, объёмами, дорожками и риском;
удаляются только выбранные категории, диалог подтверждения показывает именно их. Полный реестр
заглушек — раздел 7.1 в `context.md`.

Python-слой `core/` (процессы, службы, автозагрузка, исполнитель, бэкапы) — по-прежнему заглушки
из ранних итераций; реальную работу делает Rust-ядро. Это осознанный следующий этап: M1–M6 из
раздела 8 `context.md`, плюс полигон на VirtualBox для тестов удаления.

## Как запустить

```bash
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt
./venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./venv/Scripts/python.exe swagcleaner.py
```

CLI-режимы (без Qt):

```bash
./venv/Scripts/python.exe swagcleaner.py --scan
./venv/Scripts/python.exe swagcleaner.py --advisor
./venv/Scripts/python.exe swagcleaner.py --self-test
./venv/Scripts/python.exe swagcleaner.py --disk            # куда ушло место (Rust-ядро)
./venv/Scripts/python.exe swagcleaner.py --disk --candidates
./venv/Scripts/python.exe swagcleaner.py --disk --explain  # с включённым AI
```

## Сборка Rust-ядра

```bash
rustup toolchain install stable-x86_64-pc-windows-gnu
# нужен MinGW-w64 (D:\mingw64) в PATH
cd rust/swagscan
cargo +stable-x86_64-pc-windows-gnu build --release    # target/release/swagscan.exe
cargo +stable-x86_64-pc-windows-gnu test               # 38 тестов
```

## Тесты

```bash
# весь набор Python (225 тестов: модели, ядро, UI, AI, мост до Rust)
./venv/Scripts/python.exe -m pytest -o addopts="" -q -p no:cacheprovider
# Rust-ядро (38 тестов)
cd rust/swagscan && cargo +stable-x86_64-pc-windows-gnu test
```

## Сборка

```bash
run_build.bat                        # один файл (--onefile)
set SWAGCLEANER_ONEDIR=1 && run_build.bat   # папкой — стартует быстрее, удобнее при доводке UI
dist/SWAGcleaner.exe --self-test
```

## Принципы

1. **Только локально.** Ни одного запроса в сеть, ни аккаунтов, ни слежки.
2. **Ничего не удаляется само.** Дефолт — только чтение; любое изменение после подтверждения.
3. **Удаление — в корзину**, кроме регенерируемого мусора в известных системных местах
   (правило выше), и никогда — восстановимо ценой поломки системы.
4. **Откат там, где он честный.** Перед изменением системы сохраняется снапшот; если откат для
   действия невозможен — об этом говорится в диалоге заранее, а не после.
5. **Права — по кнопке и с объяснением**, а не при старте приложения.

## Лицензия

MIT.

---

# SWAGcleaner (EN)

A local Windows cleaner. Everything runs on your machine: no network calls, no accounts,
no telemetry, no tracking. Nothing is deleted or disabled without your confirmation.

Project memory (honest audit of what actually works, internal wiring plan, decision history) lives
in [`context.md`](context.md).

What's inside: system scanner (installed apps from the registry, processes, services, startup
items; read-only by default), a one-pass disk index, a rule-based advisor, junk cleanup, exact and
perceptual photo-duplicate detection, and tweaks (services, startup, UWP — planned).

## Deletion policy

- **To the Recycle Bin** — anything a person may want back: personal files, photo duplicates,
  large and old files. Full undo via "Restore".
- **Immediately, bypassing the Bin** — only regenerable content in known system locations:
  `%TEMP%`, `%SystemRoot%\Temp`, browser and app caches, thumbnails, logs, crash reports, dumps,
  orphaned installer and Windows-update leftovers. Such items are explicitly marked
  "no Recycle Bin — cannot be undone" and are never pre-checked.
- **Never** — `WinSxS`, restore points, `pagefile.sys`, `hiberfil.sys`, bulk registry edits,
  installed program folders, files outside the whitelist, symlinks and junctions, and any personal
  file without an explicit checkbox.
- Every deletion and every disable requires confirmation; the dialog shows items, size, risk and
  the total. Failures never abort the whole run — they are collected into the report.

Status (17 Sep 2026): the interface is real (two themes, ru/en, mascot, confirm dialog, workers)
and is wired to the core through a session layer: scan and purge run against the real engine,
tiles show real numbers, progress comes from core events (including live purge progress). After
a scan the **category screen** opens: cards with checkboxes, sizes, lanes and risk — only the
selected categories are deleted, and the confirm dialog lists exactly them. The scan core itself
is real too — Rust scanner with categories, BLAKE3 duplicates, two-lane deletion, dry-run and
cancellation, covered by 239 pytest + 40 cargo tests, byte-exact against the Python reference.
The VirtualBox polygon (Win11 24H2) runs the core end to end from host shared folders; the built
`.exe` was verified inside the VM (`--self-test`, `--disk --candidates`, GUI screenshots in
`shots/polygon-2026-09-16/`). The old Python `core/` modules (processes, services, startup,
executor, backups) remain stubs. Full stub registry: section 7.1 in `context.md`.

MIT License.
