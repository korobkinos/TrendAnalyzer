# Trend Analyzer v1.5.2

Приложение для онлайн/офлайн анализа технологических сигналов с Modbus TCP, архивом и печатью графиков.

Начиная с версии `1.2.0` поддерживается разделение на две роли:
- `Viewer/Configurator` (основной UI: графики, теги, анализ, настройка),
- `Recorder` (внешний фоновый регистратор, пишет в БД независимо от UI).

С версии `1.2.1` добавлен отдельный `Tray Recorder`:
- работает в системном трее без главного окна,
- может автоматически запускать/останавливать фоновый Recorder,
- позволяет открыть UI-конфигуратор по требованию.

С версии `1.3.0` онлайн-режим работает по схеме единого источника:
- `Recorder` опрашивает Modbus и пишет в БД,
- UI в онлайн-режиме читает live-хвост данных из БД для графика и таблиц.

С версии `1.4.0` добавлен мульти-источниковый режим:
- `Recorder API v1` для удалённых клиентов,
- окно `Источники данных` с ручным добавлением и авто-сканером сети,
- импорт тегов из удалённых recorder в график,
- чтение/запись регистров через выбранный удалённый recorder.

## Основные возможности

- Опрос Modbus TCP.
- Несколько сигналов на одном графике.
- Несколько шкал Y с привязкой сигналов.
- Курсор и анализ участка графика.
- Архив в SQLite, экспорт/импорт архива.
- Режим экономии архива:
  - запись всех точек,
  - запись только при изменении (`deadband`, `keepalive`).
- Профили настроек, автозагрузка профиля.
- Автозапуск с Windows и автоподключение.
- Внешний регистратор:
  - запуск/останов из UI,
  - отдельный статус регистратора,
  - запись продолжается без зависимости от окна графиков.
- Recorder API (`HTTP`) для удалённых клиентов:
  - `/v1/health`, `/v1/tags`, `/v1/live`, `/v1/history`, `/v1/config`,
  - `/v1/modbus/read`, `/v1/modbus/write`.
- Сборка portable EXE (one-file).

## Быстрый запуск (из исходников)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Viewer/Configurator:

```bash
python main.py
```

Recorder (фоновый режим):

```bash
python main.py --recorder
```

Tray Recorder (иконка в трее):

```bash
python main.py --recorder-tray
```

В tray-режиме:
- запись запускается автоматически при старте tray-приложения,
- доступны действия `Старт записи`, `Стоп записи`, `Статус регистратора...`,
- пункт `Открыть интерфейс настройки` поднимает обычный UI-конфигуратор.

## Где хранятся данные

- Конфигурация UI/профилей: `~/.trend_analyzer/config.json`
- Архив SQLite: `~/.trend_analyzer/archive.db`
- Конфиг внешнего регистратора: `~/.trend_analyzer/recorder_config.json`
- Статус внешнего регистратора: `~/.trend_analyzer/recorder_status.json`
- Команда управления регистратором: `~/.trend_analyzer/recorder_control.json`
- PID внешнего регистратора: `~/.trend_analyzer/recorder.pid`

Для portable EXE:

- `dist\TrendClient.exe` и `dist\TrendRecorder.exe` в одной папке
- `data\config.json` рядом с `exe`
- `data\archive.db` рядом с `exe`
- `data\recorder_config.json` рядом с `exe`
- `data\recorder_status.json` рядом с `exe`
- `data\recorder_control.json` рядом с `exe`

## Сборка portable EXE (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File .\build_portable.ps1
```

Результат:

- `dist\TrendClient.exe`
- `dist\TrendRecorder.exe` (tray + recorder core через ключ `--recorder`)

## Версионирование

Используется SemVer: `MAJOR.MINOR.PATCH`.

- `PATCH` (`1.1.x`) — исправления и небольшие улучшения.
- `MINOR` (`1.x.0`) — новые возможности без ломки совместимости.
- `MAJOR` (`x.0.0`) — крупные изменения с возможной несовместимостью.

История изменений:

- см. `CHANGELOG.md`
- см. `SESSION_HANDOFF_RU.md`

## Separate recorder and client binaries (recommended)

Now the project supports role-based startup and split builds:

- `TrendClient` - UI client (charts, analysis, source management).
- `TrendRecorder` - recorder for background archiving.  
  On Windows it starts as tray app and can run recorder core (`--recorder`).

### Build on Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\build_roles_windows.ps1
```

Output:
- `dist\TrendClient.exe`
- `dist\TrendRecorder.exe`

### Build on Linux

```bash
chmod +x ./build_roles_linux.sh
./build_roles_linux.sh
```

Output:
- `dist/TrendClient`
- `dist/TrendRecorder`

### Build .deb packages on Linux (Debian/Ubuntu/Mint)

```bash
chmod +x ./build_deb_roles.sh
./build_deb_roles.sh amd64
```

Output:
- `dist/deb/trend-client_<version>_amd64.deb`
- `dist/deb/trend-recorder_<version>_amd64.deb`

### Headless deployment flow

1. Install/start `TrendRecorder` on machine with PLC.
2. From client machine open: `Настройка -> Дополнительно -> Источники данных...`.
3. Add recorder manually (IP/port/token) or run subnet scan.
4. Use `Применить профиль на источник` to push current profile to recorder.
5. Recorder starts polling/writing archive using pushed config.
