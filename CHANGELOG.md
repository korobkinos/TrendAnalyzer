# Changelog
Все значимые изменения проекта фиксируются в этом файле.

## [1.5.29] - 2026-03-12
### Changed
- Fix false unsaved-changes prompt by excluding runtime tag values from dirty-check normalization

## [1.5.28] - 2026-03-12
### Changed
- Improve button state visuals: distinct disabled/pressed/checked styles in UI theme

## [1.5.27] - 2026-03-12
### Changed
- Restore sticky Auto-X follow and lock Modbus tag status while pulse is held

## [1.5.26] - 2026-03-12
### Changed
- Fix Auto-X freeze: decouple follow motion from history anchor availability

## [1.5.25] - 2026-03-12
### Changed
- Stop infinite right extrapolation of hold lines; clamp to last real sample ts

## [1.5.24] - 2026-03-12
### Changed
- Fix zoom-in archive visibility with robust hold anchors in history window

## [1.5.23] - 2026-03-12
### Changed
- Fix zoom-in archive visibility by adding hold anchors for sparse/change-only history windows

## [1.5.22] - 2026-03-12
### Changed
- Disable Windows cmd cleanup helper on close and smooth online X-follow to prevent backward jitter

## [1.5.21] - 2026-03-12
### Changed
- Client now starts recorder in tray mode (--recorder-tray) to avoid hidden service-only orphan processes

## [1.5.20] - 2026-03-12
### Changed
- Live chart smoothing and Modbus momentary pulse button

## [1.5.19] - 2026-03-12
### Changed
- Стабилизация live-графика (ограничение стартовой истории, адаптивная выборка) и полная чистка битых строк статуса в UI

## [1.5.18] - 2026-03-11
### Added
- Добавлен визуальный индикатор выполнения ресурсоёмких операций:
  - в нижней строке статуса появился progress bar в режиме «идёт работа»,
  - во время таких операций включается курсор ожидания.

### Changed
- Индикатор занятости подключен к тяжёлым действиям UI:
  - подключение/проверка/сканирование источников,
  - импорт тегов из источников и применение профиля на источник,
  - ручное чтение/запись в окне `Регистры Modbus`,
  - очистка архивной БД.

## [1.5.17] - 2026-03-11
### Fixed
- Исправлен баг в диалоге `Импортировать выбранные теги...`, когда «птички» не ставились:
  - чекбоксы в колонке `Импорт` больше не отключаются для уже импортированных тегов,
  - при повторном импорте такие теги корректно пропускаются без дублей (по существующей защите),
  - в описании диалога добавлена явная подсказка, что уже импортированные теги будут пропущены.

### Documentation
- В `README.md` добавлен раздел `Параметры окна «Настройки подключения»`:
  - описаны все поля формы (Modbus, архив, API регистратора),
  - добавлены пояснения по кнопкам управления профилем и действиям `Применить` / `Очистить архив БД`.

## [1.5.16] - 2026-03-11
### Changed
- В таблице значений под графиком колонка `Источник` теперь показывает реальный источник сигнала:
  - `Локальный recorder` для локальных тегов,
  - `<Имя источника> (<host>:<port>)` для удалённых recorder.
- Убрана неоднозначность, когда в колонке `Источник` отображалось `Курсор/Текущее` вместо фактического источника.

- В окне `Сигналы графика` добавлена отдельная колонка `Источник`, чтобы сразу видеть, откуда каждый тег (локальный или конкретный удалённый recorder).
- Колонка `Источник` в таблице сигналов автоматически обновляется при изменении списка/параметров источников.

## [1.5.15] - 2026-03-11
### Added
- В окно `Источники данных` добавлена явная кнопка `Подключить выбранный`:
  - выполняет проверку доступности источника (`/v1/health`),
  - явно помечает строку как подключенную (`Подключен: <profile_name>`),
  - автоматически включает источник (`Вкл`) и подставляет его в выбор источника окна `Регистры Modbus`.
- Добавлен выборочный импорт тегов:
  - новая кнопка `Импортировать выбранные теги...`,
  - открывается диалог со списком тегов источника и чекбоксами,
  - можно выбрать только нужные теги (важно при тысячах тегов),
  - уже импортированные теги не дублируются.
- Кнопка массового импорта переименована в `Импортировать все теги` для явного разделения сценариев.

## [1.5.14] - 2026-03-11
### Fixed
- Внедрена системная защита от mojibake во всем UI:
  - добавлен глобальный patch Qt-методов установки текста (`setText`, `setWindowTitle`, `setItemText`, `setTabText`, `setTitle` и др.) с автоматическим восстановлением кодировки,
  - добавлена одноразовая нормализация уже созданных виджетов/меню/табличных элементов при старте окна.
- Расширенный декодер теперь стабильно чинит оба частых варианта «битой» кириллицы:
  - UTF-8, ошибочно прочитанный как Latin-1 (`Ð¡Ñ‚...`),
  - UTF-8, ошибочно прочитанный как CP1251 (`РЎС‚...`).

### Added
- Добавлены автотесты на защиту от кодировочных артефактов:
  - проверка декодирования обоих типов mojibake,
  - проверка глобального Qt-patch для `QLabel` и `QTableWidgetItem`.

## [1.5.13] - 2026-03-11
### Fixed
- Улучшено отображение подключения в заголовке окна `TrendClient`:
  - информация о подключении перенесена в начало заголовка (теперь не теряется при обрезке справа),
  - в режиме `онлайн (ожидание старта)` теперь тоже показывается целевой источник (`локальный recorder` и/или `удалённый host:port`),
  - для удалённых источников до старта live-потока показывается состояние `настроен`.

## [1.5.12] - 2026-03-11
### Fixed
- Исправлена «сломанная» кодировка статуса в сценарии `Источники данных -> Применить профиль на источник`.
- Статусные сообщения для отправки профиля на источник переписаны в корректной UTF-8 кириллице.
- Расширена автоматическая нормализация `status_label`: теперь чинятся оба типа mojibake:
  - UTF-8, ошибочно прочитанный как Latin-1 (`Ð¡Ñ‚...`),
  - UTF-8, ошибочно прочитанный как CP1251 (`РЎС‚...`).

## [1.5.11] - 2026-03-11
### Added
- В заголовок главного окна `TrendClient` добавлен динамический индикатор источника live-данных и состояния подключения.
- Для локального режима в заголовке показывается `локальный recorder PID <pid>` или `локальный recorder: нет связи`.
- Для удалённых источников в заголовке показываются `name/host:port` и состояние `online/offline` (или агрегат `connected/total` при нескольких recorder).
- Для offline/ожидания запуска в заголовке отображаются явные состояния (`режим: офлайн`, `режим: онлайн (ожидание старта)`).

## [1.5.10] - 2026-03-11
### Fixed
- Исправлено отображение «сломанной кириллицы» (mojibake вида `Ð¡Ñ‚...`) в нижней строке статуса.
- Добавлена автоматическая нормализация текста для `status_label`: если в статус попадает mojibake, он автоматически восстанавливается в корректный UTF-8 русский текст.

## [1.5.9] - 2026-03-11
### Changed
- Удалена fallback-совместимость со старым медленным чтением регистров для удалённых источников в окне `Регистры Modbus`.
- Клиент теперь использует только batch-метод `POST /v1/modbus/read_many` для массового удалённого чтения.
- Если удалённый recorder не поддерживает `read_many`, показывается явная ошибка чтения (без отката на поштучные запросы).

## [1.5.8] - 2026-03-11
### Changed
- В таблице просмотра сигналов удален отдельный выпадающий режим `Сортировка: ...`.
- Оставлена единая сортировка по клику на заголовки столбцов (индикатор сортировки в `QHeaderView`).
- Удалено сохранение/восстановление устаревшего `values_sort_mode` из `ui_state.view`; сохранена сортировка по выбранному столбцу (`values_sort_column`/`values_sort_desc`).

## [1.5.7] - 2026-03-11
### Changed
- Ускорен Modbus-опрос за счет пакетного чтения подряд идущих адресов (групповые запросы до 125 слов):
  - в `ModbusWorker` (локальный поток чтения),
  - в `RecorderService` (долгоживущий регистратор).
- В окне `Регистры Modbus` чтение локальных тегов переведено на пакетный режим вместо поштучных запросов.
- Для удалённых источников добавлен batch-API:
  - `POST /v1/modbus/read_many`,
  - окно `Регистры Modbus` использует его для пакетного чтения, с fallback на старый поштучный режим для совместимости со старыми recorder.
- Массовая запись (`Записать`) в окне `Регистры Modbus` оптимизирована:
  - убрано лишнее предварительное чтение каждого регистра перед записью.

## [1.5.6] - 2026-03-11
### Fixed
- Исправлен автозапуск `TrendRecorder` из UI-сценария `3) Старт`:
  - при старте live-просмотра клиент автоматически поднимает внешний регистратор (если в профиле есть локальные сигналы),
  - сохранен корректный fallback: если локальный recorder не поднялся, но есть удалённые источники, запускается удалённый live-поток без локального,
  - устранена блокировка управляющих пунктов UI в ситуации, когда recorder ещё не запущен, но доступен для автозапуска.

## [1.5.5] - 2026-03-10
### Fixed
- Исправлено поведение графика в режиме архивирования `только по изменению`:
  - если значение не меняется, линия теперь продолжает обновляться по текущему времени в UI (виртуальные точки),
  - при этом в БД дополнительные точки не пишутся (экономия архива сохраняется).
- Исправлено “зависание времени” в таблице/графике при неизменных значениях:
  - отображается актуальный текущий момент без принудительной записи в архив.

## [1.5.4] - 2026-03-10
### Fixed
- Устранены лаги `TrendClient` при работе с удаленными источниками по сети:
  - удаленный live-poll переведен в неблокирующий режим (через фоновые задачи),
  - UI больше не блокируется на сетевых таймаутах.
- Устранено “дребезжание” состояния подключения (`подключено/не подключено`) при кратковременных сетевых потерях:
  - добавлен устойчивый расчет состояния по последнему успешному ответу (TTL),
  - добавлен backoff на повторные неуспешные запросы к удаленному источнику.

### Changed
- Снижен timeout загрузки стартовой remote-history, чтобы уменьшить фризы при запуске онлайн-просмотра.
- Добавлено корректное завершение фонового пула удаленных запросов при закрытии приложения.

## [1.5.3] - 2026-03-10
### Fixed
- Исправлена ложная индикация `recorder не запущен` при живом процессе (Windows):
  - проверка PID сделана более надежной,
  - добавлено восстановление PID из `recorder_status.json`, если `recorder.pid` потерян/сброшен.
- Исправлена рассинхронизация между фактическим состоянием recorder и UI/tray:
  - `TrendClient` и `TrendRecorder` теперь используют единый `resolve_recorder_pid()`.

### Added
- Новые тесты устойчивости по PID-синхронизации:
  - `tests/test_recorder_shared.py`.

## [1.5.2] - 2026-03-10
### Fixed
- Исправлен критический запуск внешнего регистратора из `TrendClient.exe`:
  - клиент теперь ищет и запускает соседний `TrendRecorder.exe`,
  - убран ошибочный сценарий, когда клиент запускал сам себя с `--recorder`.
- Исправлен сценарий `Старт` в UI: добавлено явное ожидание подтверждения старта регистратора и понятная ошибка, если старт не подтвержден.

### Changed
- Добавлена защита от запуска второго экземпляра:
  - `TrendClient` (single-instance),
  - `TrendRecorder` в tray-режиме (single-instance).
- Упрощено верхнее меню для последовательного сценария:
  - `Настройка`: `1) Подключение`, `2) Сигналы графика`,
  - `Рабочий процесс`: `3) Старт`, `4) Стоп`,
  - редко используемые окна перенесены в `Настройка -> Дополнительно`.
- Обновлен `README` и тексты по сборке/ролям:
  - Windows: только `TrendClient.exe` + `TrendRecorder.exe`.
- Linux сборка упрощена: убран отдельный `TrendRecorderTray` из `build_roles_linux.sh`.
- Усилен `build_roles_windows.ps1`: теперь скрипт останавливается при ошибке шага (нет ложного `Build complete` при неудачной сборке).

## [1.4.0] - 2026-03-10
### Added
- Recorder API v1 (`HTTP`):
  - `GET /v1/health`
  - `GET /v1/tags`
  - `GET /v1/live` (включая `bootstrap=1` для инициализации курсоров)
  - `GET /v1/history`
  - `GET /v1/config`
  - `PUT /v1/config`
  - `POST /v1/modbus/read`
  - `POST /v1/modbus/write`
- Новое окно `Источники данных (регистраторы)`:
  - ручное добавление/редактирование источников,
  - автоматический сканер локальной сети по подсети (`x.x.x.x/24`) и порту API,
  - проверка статуса источников.
- Импорт тегов из выбранных recorder-источников в `Сигналы графика`.
- Поддержка mixed-source сигналов в профиле:
  - локальные сигналы (`source_id=local`),
  - удалённые сигналы (`source_id=<id источника>`, `remote_tag_id=<id тега>`).
- В окне `Регистры Modbus` добавлен выбор источника:
  - локальный прямой Modbus,
  - любой подключённый recorder (чтение/запись через API).

### Changed
- Онлайн-просмотр графика теперь может работать в трёх сценариях:
  - только локальный recorder,
  - только удалённые recorder,
  - смешанный режим локальный + удалённые.
- При старте live-просмотра добавлена подгрузка хвоста истории с удалённых recorder.
- В конфигурацию профиля добавлены настройки Recorder API:
  - `recorder_api_enabled`, `recorder_api_host`, `recorder_api_port`, `recorder_api_token`.
- Recorder runtime теперь поддерживает динамическое применение профиля через API (`PUT /v1/config`).

## [1.3.0] - 2026-03-10
### Changed
- Switched online workflow to single-source architecture:
  - external recorder is the only Modbus poller/writer,
  - UI online mode now renders live data tail from SQLite archive (`samples` + `connection_events`).
- `Старт` action in UI now starts DB-live viewing and auto-starts external recorder if it is not running.
- Removed runtime dependence on in-process online polling for chart updates.
- Updated menu labels for clarity: `Старт просмотра (из БД)` and `Стоп просмотра`.

## [1.2.2] - 2026-03-10
### Changed
- Reworked top menu structure for faster navigation and reduced clutter.
- Replaced `Сеанс` naming with `Управление`.
- Grouped actions by intent:
  - `Управление` (режим, опрос, внешний регистратор),
  - `Окна` (все рабочие окна, включая статистику),
  - `Архив и экспорт`,
  - `Вид`,
  - `Приложение` (трей, поведение закрытия, автозапуск, выход).
- Removed always-visible UI diagnostics action from the regular user menu.

## [1.2.1] - 2026-03-10
### Added
- Added dedicated tray recorder mode (`main.py --recorder-tray`) with no main window.
- Added tray menu actions: start/stop recording, open recorder status, and open viewer/configurator UI.
- Added separate Windows autostart registration for tray recorder (`TrendAnalyzerRecorder`).

### Changed
- `main.py` now supports three launch roles: UI, headless recorder, and tray recorder.
- Startup command builder now supports additional CLI args for autostart scenarios.
- Added startup-command unit tests for default and arg-based autostart command generation.

## [1.2.0] - 2026-03-10
### Added
- Added external recorder runtime (`main.py --recorder`) for long-running archive writes independent from UI rendering.
- Added recorder IPC files in app data (`recorder_config.json`, `recorder_status.json`, `recorder_control.json`, `recorder.pid`).
- Added UI actions in `Режим`: start/stop external recorder and recorder status dialog.

### Changed
- Viewer now acts as recorder configurator: save/apply writes active profile snapshot to recorder config.
- `ProfileConfig.from_dict` now preserves explicitly empty `signals` list (no forced placeholder signal when `signals: []`).
- Added tests for archive signal cleanup and explicit-empty-signals behavior.

## [1.1.15] - 2026-03-10
### Changed
- Added `Удалить все` button in `Сигналы графика` window.
- `Удалить все` now removes all signals from profile without auto-restoring a placeholder signal.
- Added archive cleanup for removed signals: deletes related rows from `samples` and `signals_meta` (with `VACUUM`), reducing DB bloat.

## [1.1.14] - 2026-03-10
### Changed
- Runtime status line now also shows chart render mode: `график: вкл` / `график: выкл`.
- Added immediate runtime-status refresh when `Отрисовка графика` is toggled.

## [1.1.13] - 2026-03-09
### Changed
- Fixed `Отрисовка графика` behavior to be visualization-only:
  - turning it off clears chart display and stops UI redraw,
  - turning it on restores chart from archive history.
- Archive writing remains independent from chart rendering (continues while chart rendering is off).
- Connection overlay updates are skipped while chart rendering is off to avoid visual artifacts in archiver-only mode.

## [1.1.12] - 2026-03-09
### Changed
- Added profile option `Отрисовка графика`:
  - when enabled, online data is rendered as before,
  - when disabled, rendering is skipped and app works as lightweight archiver.
- Added per-column sorting in values table by clicking column headers (ascending/descending toggle).
- Values table color cell rendering improved: color swatch now fills the whole cell width.
- Added persistence of values-table header sort state in `ui_state.view`.

## [1.1.11] - 2026-03-09
### Changed
- Runtime status line now shows archive mode explicitly: `архив выкл` / `архив: все точки` / `архив: изменения`.
- Added immediate runtime-status refresh when archive filter and mode/write options are changed.
- Memory safety improvements:
  - reduced max pending UI render queue batches (backpressure) to lower RAM spikes under heavy load,
  - added pruning of in-memory connection event history to active chart data window.

## [1.1.10] - 2026-03-09
### Changed
- Added archive optimization mode: write samples only when values change (`archive_on_change_only`).
- Added configurable archive filters:
  - `archive_deadband` (minimum delta for write),
  - `archive_keepalive_s` (forced periodic write even without change, `0` = disabled).
- Added UI controls in connection settings for the new archive filters.
- Added save/load of these options in profile config and connection config export/import.
- Runtime archive writer now applies per-signal filtering before DB insert, reducing long-term archive growth.

## [1.1.9] - 2026-03-09
### Changed
- Removed legacy archive compatibility paths; app now works only with the new compact DB schema.
- Deleted fallback SQL that relied on samples.signal_name.
- Legacy samples(..., signal_name, ...) table is now recreated as compact samples(profile_id, signal_id, ts, value) without data migration.

## [1.1.8] - 2026-03-09
### Changed
- Archive storage optimized: migrated to compact samples schema without per-row signal name duplication.
- Added signals_meta dictionary table for signal names and timestamps, used by export/restore logic.
- Added automatic one-time migration from legacy samples(..., signal_name, ...) to compact schema.
- Updated DB purge action to also clear signals_meta.
- Export signal discovery now supports both compact and legacy DB layouts.
## [1.1.7] - 2026-03-09
### Changed
- Added button Очистить архив БД in connection settings window.
- Added confirmation dialog before purge: "Вы уверены? Это удалит все данные архива из базы."
- Archive DB purge now clears samples and connection_events, resets chart/session cache, and restarts polling if it was running.
## [1.1.6] - 2026-03-09
### Changed
- Restored explicit left/right margins for bottom status content using fixed edge spacers.
- Added small bottom inset for status bar.
- App now starts maximized on launch.
## [1.1.5] - 2026-03-09
### Changed
- Status bar spacing refined: added small bottom padding and equal left/right horizontal padding.
- Removed asymmetric right-only margin so left Статус and right runtime block are aligned consistently.

## [1.1.4] - 2026-03-09
### Changed
- Left status message is now auto-cleared to idle text after 5 seconds for regular Статус: ... notifications.
- Error messages (Ошибка: ...) remain visible until the next status update.

## [1.1.3] - 2026-03-09
### Changed
- Bottom runtime status visuals: removed status bar item separators and replaced text delimiters from | to commas.
- Added right padding for runtime text so it no longer sticks to the right window edge.

## [1.1.2] - 2026-03-09
### Changed
- Reworked bottom status UI to native status bar with stable left action messages and right permanent runtime block.
- Restored reliable visibility of runtime metrics (connection/CPU/RAM/archive) and safe fallback text on any read error.
- Clarified start message: now shows 'polling started (waiting for connection)' instead of ambiguous 'starting...'.

## [1.1.1] - 2026-03-09
### Changed
- Fixed runtime RAM usage display in the bottom-right status line using robust Windows memory API fallback.
- Compacted the bottom-right runtime text and constrained its label width to prevent overflow beyond window edge.

## [1.1.0] - 2026-03-09
### Changed
- Start of the new version line `1.1.x`.
- Baseline release for subsequent improvements.

---
Архив изменений ветки `1.0.x`:

## [1.0.31] - 2026-03-09
### Changed
- Added runtime status line on the bottom-right: connection indicator, CPU usage, RAM usage, and archive size.
- Kept action/result status messages on the bottom-left (save, errors, export, etc.) as requested.

## [1.0.30] - 2026-03-09
### Changed
- Added separate configurable UI redraw interval (render_interval_ms) independent from Modbus polling interval.
- Reworked realtime pipeline: worker samples are queued and rendered in batches by a dedicated UI timer, reducing lag under fast polling.
- Improved chart decimation (min/max bucket strategy) to preserve spikes while limiting rendered points.
- Improved runtime reliability: reconnect backoff in Modbus worker, retry-aware reads, and safer config/archive writes via atomic file save.
- Added startup rotating file logging and unhandled exception hook.

## [1.0.29] - 2026-03-09
### Changed
- Print layout: reserve page-number area so legend starts below it and never overlaps page number.

## [1.0.28] - 2026-03-09
### Changed
- Cleanup: remove obsolete legend-axis-offset logic and dead left_axis_ratio code after switching legend to top-right placement.

## [1.0.27] - 2026-03-09
### Changed
- Print: place legend in top-right area (below page number) to avoid overlap with left Y-axes.

## [1.0.26] - 2026-03-09
### Changed
- Print legend: use dynamic offset from measured left-axis area width so legend never overlaps stacked Y-axes.

## [1.0.25] - 2026-03-09
### Changed
- Print legend placement: move legend right with safe offset from left Y-axes and clamp within page bounds.

## [1.0.24] - 2026-03-09
### Changed
- Disable Y-axis SI prefix scaling (x1e+N) for all chart axes to keep explicit value display.

## [1.0.23] - 2026-03-09
### Changed
- Fix signal rename sync: update runtime chart meta, legend and values table immediately when editing name in 'РЎРёРіРЅР°Р»С‹ РіСЂР°С„РёРєР°'.

## [1.0.22] - 2026-03-09
### Changed
- Print: add explicit top-left legend (color + signal + axis) for readable A4 output; hide built-in legend in print render.

## [1.0.21] - 2026-03-09
### Changed
- Print presets scaled up for A4 (small=old large, bigger medium/large) and removed bottom X-axis title 'Р’СЂРµРјСЏ' in print output.

## [1.0.20] - 2026-03-09
### Changed
- Print X-axis readability: use two-line date/time labels in print mode and improve bottom axis spacing/contrast.

## [1.0.19] - 2026-03-09
### Changed
- Print: reserve extra bottom axis height on A4 to prevent 'Р’СЂРµРјСЏ' label overlap with time ticks.

## [1.0.18] - 2026-03-09
### Changed
- Print large-font mode: prevent overlapping time labels on X axis and tune large preset sizing.

## [1.0.17] - 2026-03-09
### Changed
- Print dialog: add font size preset (small/medium/large) affecting axis fonts and line width in A4 print render.

## [1.0.16] - 2026-03-09
### Changed
- Print: increase A4 axis/tick font sizes and spacing for better readability.

## [1.0.15] - 2026-03-09
### Changed
- Print: render dedicated offscreen A4 chart with readable fonts/grid instead of stretching on-screen screenshot.

## [1.0.14] - 2026-03-09
### Changed
- Print A4: stretch chart to full printable area for better readability and no narrow output.

## [1.0.13] - 2026-03-09
### Changed
- Print settings updated: statistics only on separate page option, with clearer page number in top-right corner on all printed pages.

## [1.0.12] - 2026-03-09
### Changed
- Remove disconnect-based polyline splitting and restore continuous line rendering; keep red disconnect overlay only.

## [1.0.11] - 2026-03-09
### Changed
- Fix chart rendering artifacts after history restore by keeping finite check enabled when NaN gap points are inserted.

## [1.0.10] - 2026-03-09
### Changed
- Break signal polylines across disconnect intervals so no line is drawn through red no-connection regions.

## [1.0.9] - 2026-03-09
### Changed
- Auto-load recent online history on profile open/start and mark session downtime as disconnect (red gap), including shutdown disconnect event persistence.

## [1.0.8] - 2026-03-09
### Changed
- Print stats now always use visible chart range; add optional detailed statistics on separate page with pagination.

## [1.0.7] - 2026-03-09
### Changed
- Improve printed statistics table layout: DPI-aware row sizing, readable fonts, top-aligned chart, and tighter chart/table composition.

## [1.0.6] - 2026-03-09
### Changed
- Redesign print flow: A4 landscape fitting, stronger print grid, and print options dialog with optional statistics table.

## [1.0.5] - 2026-03-09
### Changed
- Disable table column width persistence/load from profile as requested; keep all other UI state features.

## [1.0.4] - 2026-03-09
### Changed
- Persist and restore full header state (all table columns) in ui_state to prevent width reset after restart.

## [1.0.3] - 2026-03-09
### Changed
- Ensure all table column widths are restored after profile load using deferred full-table apply.

## [1.0.2] - 2026-03-09
### Changed
- Fix values table column width restore on startup and remove manual graph view save action from UI.

Р¤РѕСЂРјР°С‚ РІРµСЂСЃРёР№: `SemVer` (`MAJOR.MINOR.PATCH`).

## [1.0.1] - 2026-03-09
### Changed
- Р”РѕР±Р°РІР»РµРЅС‹ СЌРєСЃРїРѕСЂС‚/РїРµС‡Р°С‚СЊ РіСЂР°С„РёРєР° С‡РµСЂРµР· РѕС‚РґРµР»СЊРЅС‹Рµ РґРµР№СЃС‚РІРёСЏ РјРµРЅСЋ.
- РџРµС‡Р°С‚СЊ РіСЂР°С„РёРєР° РїРµСЂРµРІРµРґРµРЅР° РІ print-friendly СЃС‚РёР»СЊ (Р±РµР»С‹Р№ С„РѕРЅ).
- РЈР»СѓС‡С€РµРЅРѕ СЃРѕС…СЂР°РЅРµРЅРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРѕРіРѕ СЃРѕСЃС‚РѕСЏРЅРёСЏ РёРЅС‚РµСЂС„РµР№СЃР°.
- Р’С‹РЅРµСЃРµРЅС‹ С‡Р°СЃС‚Рё `ui.py` РІ РѕС‚РґРµР»СЊРЅС‹Рµ РјРѕРґСѓР»Рё (`chart`, `startup`, `archive_bundle`, `ui_tables`).
- РћР±РЅРѕРІР»РµРЅС‹ РЅР°Р·РІР°РЅРёСЏ РїСѓРЅРєС‚РѕРІ РјРµРЅСЋ РґР»СЏ Р±РѕР»РµРµ РїРѕРЅСЏС‚РЅРѕР№ РЅР°РІРёРіР°С†РёРё.

## [1.0.0] - 2026-03-09
### Added
- РџРµСЂРІР°СЏ СЃС‚Р°Р±РёР»РёР·РёСЂРѕРІР°РЅРЅР°СЏ РІРµСЂСЃРёСЏ РёРЅС‚РµСЂС„РµР№СЃР° Trend Analyzer.
- Р‘Р°Р·РѕРІС‹Р№ РѕРЅР»Р°Р№РЅ-РѕРїСЂРѕСЃ Modbus TCP, РіСЂР°С„РёРєРё, РїСЂРѕС„РёР»Рё, Р°СЂС…РёРІ Рё РѕС„Р»Р°Р№РЅ-Р°РЅР°Р»РёР·.




















## [1.5.0] - 2026-03-10
### Added
- Separate role entrypoints:
  - `client_main.py` (Viewer/Configurator),
  - `recorder_main.py` (headless Recorder, no UI),
  - `recorder_tray_main.py` (Tray Recorder).
- New Windows split build script:
  - `build_roles_windows.ps1` -> `TrendClient.exe`, `TrendRecorder.exe`, `TrendRecorderTray.exe`.
- New Linux split build script:
  - `build_roles_linux.sh` -> `dist/TrendClient`, `dist/TrendRecorder`, `dist/TrendRecorderTray`.
- New Debian packaging script for split roles:
  - `build_deb_roles.sh` -> `trend-client_<ver>_<arch>.deb`, `trend-recorder_<ver>_<arch>.deb`.
- Linux systemd unit template for headless recorder:
  - `packaging/linux/trend-recorder.service`.
- Remote profile apply from client to selected recorder source:
  - In `Источники данных` window added button `Применить профиль на источник`,
  - sends `PUT /v1/config` to selected recorder.

### Changed
- Version bumped to `1.5.0`.
- Multi-source workflow now better matches deployment model with dedicated headless recorder binary.
