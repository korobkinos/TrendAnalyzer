# Trend Analyzer — Технический журнал разработки

Дата обновления: 2026-03-11

## Версия продукта

- Текущее имя окна: `Trend Analyzer v1.5.29`
- Схема версий: `SemVer` (`MAJOR.MINOR.PATCH`)
  - `1.1.x` — мелкие правки/улучшения (PATCH)
  - `1.x.0` — новые функции без ломки совместимости (MINOR)
  - `x.0.0` — крупные изменения с возможной несовместимостью (MAJOR)

## 1) Что уже реализовано

- Опрос Modbus TCP в отдельном потоке (`ModbusWorker`) с настраиваемыми:
  - IP, порт, Unit ID
  - интервал опроса
  - интервал архивации
  - timeout / retries
  - address offset
- Разделение ролей с версии `1.2.0`:
  - `Viewer/Configurator` (основной UI, графики и анализ),
  - `Recorder` (`main.py --recorder`, отдельный долгоживущий процесс записи в архив).
- Tray-регистратор с версии `1.2.1`:
  - отдельный режим запуска `main.py --recorder-tray`,
  - иконка в системном трее без главного окна,
  - действия: Старт/Стоп записи, Статус, Открыть интерфейс настройки,
  - отдельный автозапуск Windows для tray-роли (`TrendAnalyzerRecorder`).
- График на `pyqtgraph`:
  - несколько сигналов одновременно
  - курсор (вертикальная линия) с просмотром значений в точке
  - масштабирование/панорамирование
  - ось времени в формате дата+время+миллисекунды
  - несколько шкал (оси Y) с привязкой сигналов к шкалам
  - скрытие/показ сигналов «на лету»
- Таблица значений под графиком:
  - видимость сигнала (чекбокс)
  - изменение цвета сигнала «на лету»
  - сортировка
- Окно шкал:
  - Auto Y по каждой шкале
  - ручные Min/Max
- Окно статистики участка графика:
  - период по видимой области
  - период по 2 маркерам
  - min / max / avg / скорость изменения
- Архив:
  - запись в SQLite
  - ограничение глубины хранения (retention days)
  - экспорт/импорт архивов
  - ZIP-пакет с манифестом и проверкой формата
  - экспорт/импорт конфигурации подключения
  - архивирование событий связи (connected/disconnected)
- Окно регистров Modbus:
  - чтение/запись отдельных регистров
  - диапазонное добавление
  - типы: int16 / uint16 / float32 / bool(bit)
  - несколько вкладок регистров с сохранением в профиль
- Профили:
  - создание, клон, удаление
  - автозагрузка последнего активного профиля при старте
- Трей:
  - сворачивание в трей
  - поведение при закрытии (ask/tray/exit)
- Дополнительные опции запуска:
  - автозапуск при старте Windows (HKCU Run)
  - автоподключение при запуске приложения
- Portable EXE:
  - иконка приложения
  - сборка через PyInstaller

## 2) Где что находится

- Точка входа: `main.py`
- UI и логика графика: `trend_analyzer/ui.py`
- Модуль графика: `trend_analyzer/chart.py`
- Модуль архивного формата: `trend_analyzer/archive_bundle.py`
- Модуль таблиц (UI-хелперы): `trend_analyzer/ui_tables.py`
- Модуль запуска ОС (autostart): `trend_analyzer/startup.py`
- Модели конфигурации: `trend_analyzer/models.py`
- Поток Modbus: `trend_analyzer/modbus_worker.py`
- Хранилище конфигурации/архива: `trend_analyzer/storage.py`
- IPC и сервис внешнего регистратора:
  - `trend_analyzer/recorder_shared.py`
  - `trend_analyzer/recorder_service.py`
- Трей-контроллер регистратора:
  - `trend_analyzer/recorder_tray.py`
- Сборка EXE: `build_portable.ps1`, `TrendAnalyzer.spec`

## 3) Сохранение конфигурации (актуально)

Сохраняется в `ProfileConfig.ui_state`:

- размеры окон
- размеры секций главного сплиттера
- ширины столбцов таблиц
- runtime-настройки вида (`ui_state.view`):
  - auto_x
  - cursor_enabled
  - values_sort_mode
  - состояние нижней панели значений (collapsed/closed)
  - x_range (временной интервал на графике)
  - scale_states (по каждой шкале: axis_index, auto_y, y_min, y_max)
  - настройки статистики (маркеры, период)
- app-level настройки (`AppConfig`):
  - `auto_start_windows`
  - `auto_connect_on_launch`

## 4) Важные последние исправления

- 1.5.18: добавлена индикация выполнения тяжёлых операций в UI:
  - в status bar добавлен progress bar (режим неопределённого прогресса) на время выполнения долгих задач,
  - при выполнении долгих задач включается курсор ожидания,
  - индикатор применён к операциям сканирования/проверки/подключения источников, импорта тегов, отправки профиля на источник, ручного чтения/записи регистров и очистки архивной БД.
- 1.5.17: исправлен выбор тегов в диалоге выборочного импорта:
  - в окне `Импортировать выбранные теги...` чекбоксы теперь доступны всегда, даже если тег уже был импортирован ранее,
  - повторный выбор уже импортированных тегов не создаёт дублей (теги по-прежнему отсекаются по ключу `(source_id, remote_tag_id)`),
  - добавлена явная подсказка в диалоге, что уже импортированные теги будут пропущены при нажатии `Импорт`.
  - в `README.md` добавлен раздел с расшифровкой параметров окна `Настройки подключения` (Modbus, архив, API и кнопки управления профилем).
- 1.5.16: улучшена прозрачность источников данных в UI:
  - в таблице значений под графиком колонка `Источник` теперь показывает фактический source сигнала (локальный recorder или удалённый `name host:port`), а не режим курсора,
  - в окне `Сигналы графика` добавлена отдельная колонка `Источник`,
  - добавлено автообновление source-меток сигналов при изменении таблицы `Источники данных`.
- 1.5.15: доработано окно `Источники данных` для явного сценария подключения и выборочного импорта:
  - добавлена отдельная кнопка `Подключить выбранный` (проверка `/v1/health`, авто-включение источника, обновление статуса строки),
  - добавлен диалог `Импортировать выбранные теги...` с чекбоксами по каждому тегу источника,
  - массовый режим импорта оставлен отдельной кнопкой `Импортировать все теги`,
  - исключено дублирование уже импортированных тегов при повторном выборе.
- 1.5.14: выполнен системный фикс кодировок для всего UI:
  - добавлен глобальный patch Qt-методов установки текста, который автоматически восстанавливает mojibake в нормальный UTF-8,
  - добавлена стартовая нормализация существующих текстов виджетов/меню/таблиц, созданных через конструкторы,
  - покрыто тестами: оба типа mojibake (`Ð...` и `Р...`) + проверка patched Qt-setter'ов.
- 1.5.13: доработано отображение подключения в шапке окна:
  - статус подключения перенесен в начало заголовка окна (`<подключение> — Trend Analyzer v...`), чтобы информация не обрезалась справа,
  - в состоянии `онлайн (ожидание старта)` теперь показывается не только режим, но и настроенный источник (локальный и/или удалённый),
  - для удалённых источников до старта live-потока отображается `настроен`, после старта — `online/offline`.
- 1.5.12: исправлена кодировка статусов в действии `Применить профиль на источник`:
  - в обработчике отправки профиля на удалённый recorder заменены «битые» строки (`РЎС‚...`) на корректные UTF-8 сообщения,
  - расширен авто-ремонт `status_label` для второго типа mojibake (UTF-8, прочитанный как CP1251), чтобы подобные статусы автоматически восстанавливались в нормальный русский текст.
- 1.5.11: в заголовок окна добавлен явный индикатор источника подключения:
  - при live-работе показывается, куда именно подключен `TrendClient` (локальный recorder с PID и/или удалённый recorder `name host:port`),
  - для удалённого источника добавлен online/offline-статус по TTL последнего успешного ответа,
  - при offline/ожидании старта показывается отдельное понятное состояние в шапке окна.
- 1.5.10: исправлено отображение статуса внизу окна при битой кириллице:
  - добавлен защитный декодер для `status_label`, который автоматически чинит mojibake вида `Ð¡Ñ‚...` в нормальный русский текст.
- 1.5.9: удалена fallback-совместимость со старым медленным удалённым чтением регистров:
  - окно `Регистры Modbus` для удалённых источников использует только batch-эндпоинт `POST /v1/modbus/read_many`,
  - откат на поштучный `/v1/modbus/read` убран полностью,
  - при старом recorder (без `read_many`) показывается явная ошибка и требуется обновление recorder.
- 1.5.8: упрощена сортировка в таблице просмотра сигналов:
  - удалён отдельный выпадающий список `Сортировка: ...`,
  - оставлена сортировка только по нажатию на заголовки столбцов,
  - убрано сохранение legacy-поля `values_sort_mode` из runtime UI-состояния.
- 1.5.7: ускорен Modbus-опрос при больших наборах подряд идущих адресов:
  - в `ModbusWorker` и `RecorderService` чтение сигналов переведено на групповые запросы (batch до 125 слов),
  - в окне `Регистры Modbus` локальное чтение тегов тоже переведено на пакетный режим,
  - добавлен `Recorder API` endpoint `POST /v1/modbus/read_many` для пакетного чтения удалённых тегов (с fallback на старый поштучный режим),
  - массовая запись в окне `Регистры Modbus` ускорена за счёт отключения лишнего pre-read перед записью каждой строки.
- 1.5.6: исправлен сценарий `3) Старт` в UI, когда live-просмотр не поднимал локальный recorder автоматически:
  - восстановлен реальный автозапуск `TrendRecorder` из клиента (с проверкой PID и ожиданием подтверждения старта),
  - добавлен корректный fallback на удалённые источники, если локальный recorder недоступен,
  - разблокированы recorder-зависимые действия UI в случае, когда recorder ещё не запущен, но доступен для автозапуска.
- 1.3.0: внедрена архитектура единого источника данных для онлайн-режима: внешний recorder стал единственным Modbus-опросчиком, UI перешел на live-чтение хвоста из SQLite (samples/connection_events); `Старт` в UI теперь поднимает просмотр из БД и при необходимости запускает recorder.
- 1.2.2: переработана верхняя структура меню для удобства: раздел `Управление` (вместо `Сеанс`) + группировка пунктов по задачам (`Управление`, `Окна`, `Архив и экспорт`, `Вид`, `Приложение`), убран лишний диагностический пункт из пользовательского меню.
- 1.2.1: добавлен отдельный `Tray Recorder` (`--recorder-tray`) с управлением фоновым процессом записи из иконки в трее, автозапуском tray-роли через отдельный Run-ключ и открытием UI-конфигуратора по требованию.
- 1.2.0: добавлен внешний регистратор (`main.py --recorder`) с управлением из UI (`Старт/Стоп/Статус`), снапшотом активного профиля в `recorder_config.json`, отдельным статусом `recorder_status.json` и PID/control файлами для межпроцессного управления.
- 1.1.15: в окне Сигналы графика добавлена кнопка Удалить все; удаление теперь может оставлять профиль без сигналов и дополнительно очищает архивные хвосты по удалённым signal_id (samples + signals_meta, с VACUUM).
- 1.1.14: в runtime-строку добавлен явный индикатор состояния рендера график: вкл/выкл с моментальным обновлением при переключении.
- Строка статуса перенесена вниз главного окна.
- Убрана лишняя подпись «Значения» в шапке нижней панели.
- Колонка `Статус` в окне «Регистры Modbus» имеет увеличенный минимум ширины и не должна схлопываться.
- Ширины столбцов теперь сохраняются и восстанавливаются из конфигурации.
- Кнопка «Сохранить конфигурацию» больше не должна пересобирать график и сбрасывать вид/шкалы.
- После пересборки графика (например, при применении профиля) повторно применяется сохраненное runtime-состояние вида, включая настройки шкал.
- Добавлена отдельная кнопка/пункт меню «Сохранить вид графика».
- Добавлен пункт меню «Показать текущее ui_state» (для диагностики восстановления).
- Начата декомпозиция `ui.py`: график и часть инфраструктуры вынесены в отдельные модули.
- Добавлен рабочий экспорт/печать графика через контекстное меню (ПКМ по графику).
- Добавлен автосейв пользовательских UI-настроек (в т.ч. ширины столбцов) с debounce.

## 5) Что проверить после изменений (чек-лист)

1. Настроить шкалы вручную (Auto Y off + Min/Max), нажать «Сохранить конфигурацию», перезапустить приложение.
2. Проверить, что шкалы восстановились (включая Auto Y и Min/Max).
3. Проверить, что временной интервал X восстанавливается после перезапуска.
4. Проверить, что ширины столбцов (особенно `Статус` в «Регистры Modbus») не сбрасываются.
5. Проверить, что при переключении профилей сохраняется состояние для каждого профиля отдельно.

## 6) Известные риски / на что обратить внимание

- В проекте много сценариев, где происходит `configure_signals(...)`; при будущих рефакторингах важно не потерять повторное применение `ui_state.view`.
- Для очень длинных сессий основная нагрузка приходится на визуализацию; уже добавлена отрисовка по видимому диапазону + децимация, но это нужно держать под контролем при добавлении новых функций.

## 7) Рекомендуемые следующие шаги

1. Добавить heartbeat-индикатор внешнего регистратора прямо в нижнюю runtime-строку Viewer (по `recorder_status.json`).
2. Добавить отдельный диалог настройки Recorder-профиля (например, явный выбор профиля для фоновой записи, независимо от текущего активного в Viewer).
3. Подготовить миграционный слой на клиент-серверную БД (PostgreSQL/TimescaleDB) как опциональный backend для сценариев с несколькими viewer-клиентами и высокой частотой записи.

## 8) Оперативные правки 2026-03-11 (UX + кодировка + статистика)

- Окно `Регистры Modbus`:
  - отключено нежелательное действие по `Enter`, из-за которого могла создаваться новая вкладка во время ввода;
  - у кнопок диалога отключены `autoDefault/default`, чтобы `Enter` завершал ввод в поле, а не нажимал кнопку.
- Окно `Анализ участка графика`:
  - исправлена битая кодировка строки интервала (`Интервал: ...`).
- График:
  - добавлена зеленая полупрозрачная подсветка диапазона между 2 статистическими маркерами;
  - подсветка автоматически обновляется при перемещении маркеров и скрывается при выключении режима.
- Пересобран клиент:
  - `dist/TrendClient.exe` от `2026-03-11 16:46:04`.


- 2026-03-12 (performance hotfix, UI lag during config apply / Auto X):
  - `ui.py`: Auto X handlers and render enable path now reload local/remote history only for active signal ids.
  - `ui.py`: `_load_recent_online_history_from_db(...)` now supports `signal_ids` filter and applies SQL filtering in main/fallback queries.
  - `ui.py`: `_remote_signal_mapping(...)` now supports `only_enabled` and `signal_ids`; live poll/bootstrap/history use only enabled mapping.
  - `ui.py`: `_apply_current_profile(...)` no longer rebuilds chart on every apply; `chart.configure_signals(...)` is called only when signal signature changed.
  - `chart.py`: reduced redraw pressure for hidden signals:
    - hidden curves are cleared once and skipped on repeated redraws,
    - `_on_x_range_changed(...)` checks decimation redraw need only for enabled signals,
    - `_emit_display_rows(...)` emits rows only for enabled signals.
  - Verification:
    - `.venv` unit tests: `python -m unittest discover -s tests -p "test_*.py"` -> 35 tests OK.
  - Syntax check: `python -m compileall trend_analyzer/ui.py trend_analyzer/chart.py` -> OK.
  - Build:
    - standard `build_roles_windows.ps1` failed because `dist/TrendClient.exe` is locked by running process;
    - built side-by-side binaries for validation:
      - `dist/TrendClient_opt.exe` (2026-03-12 09:18:07)
      - `dist/TrendRecorder_opt.exe` (2026-03-12 09:19:08)

- 2026-03-12 (multi-source recorder: several Modbus devices in one recorder profile):
  - `models.py`:
    - `RecorderSourceConfig` extended with `source_kind` (`remote_recorder` | `modbus_tcp`),
    - per-source Modbus settings added: `unit_id`, `timeout_s`, `retries`, `address_offset`,
    - backward compatibility kept for old profiles (default kind is `remote_recorder`).
  - `recorder_service.py`:
    - recorder poll loop switched from single Modbus client to grouped multi-source polling,
    - local recorder now polls:
      - profile-local source (`source_id=local`),
      - every enabled `recorder_sources[*]` with `source_kind=modbus_tcp`,
    - remote-recorder sources are excluded from local polling,
    - archive writing remains unified and uses aggregated samples from all local/modbus groups.
  - `ui.py`:
    - sources table redesigned: added `Тип` and `Unit ID` columns,
    - source type routing implemented:
      - `remote_recorder` -> API `/v1/*`,
      - `modbus_tcp` -> direct Modbus connect/read/write,
    - tags window (`Регистры Modbus`) now supports direct batch read/write for `modbus_tcp` sources,
    - remote live/history/profile-sync logic now strictly uses only `remote_recorder` sources,
    - signal source normalization updated:
      - `remote_tag_id` is required only for `remote_recorder`,
      - for `modbus_tcp` it is cleared automatically.
  - tests:
    - `tests/test_models_profile.py`: added coverage for new source fields + legacy compatibility,
    - `tests/test_recorder_service.py`: updated expectations (local filter now includes `modbus_tcp` signals).
  - verification:
    - `.venv` tests:
      - `python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK,
    - syntax check:
      - `python -m py_compile trend_analyzer/models.py trend_analyzer/recorder_service.py trend_analyzer/ui.py` -> OK.

- 2026-03-12 (zoom/detail + stutter fix):
  - `chart.py`:
    - adaptive render detail by visible time span:
      - ~1-5 min -> high detail,
      - ~10-30 min -> medium detail,
      - ~1-2 h and wider -> lower detail for performance.
    - increased live buffer cap per signal (`_max_points`) from 4000 to 8000.
  - `ui.py`:
    - visible history reload now targets active signals first (instead of always all profile signals),
    - `_target_history_points(...)` made adaptive by current span,
    - raw history branch now returns actual bucket granularity, reducing unnecessary repeated reloads,
    - render queue flush split into chunks (`_max_render_flush_batches`) to reduce visible jumps when backlog is large.
    - recent online history query defaults to active-signal filter when explicit filter is not provided.
  - verification:
    - `python -m py_compile trend_analyzer/chart.py trend_analyzer/ui.py` -> OK
    - `python -m unittest tests.test_models_profile tests.test_recorder_service` -> OK
    - `tests.test_ui_recorder_command` skipped in this environment (no `PySide6` installed for CLI tests).
  - build:
    - `build_roles_windows.ps1` -> OK
    - `dist/TrendClient.exe` -> `2026-03-12 10:08:18`
    - `dist/TrendRecorder.exe` -> `2026-03-12 10:09:41`

- 2026-03-12 (UI alignment for multi-source concept):
  - `ui.py` -> `Настройки подключения`:
    - added source summary block (local Modbus + counts of active sources by type),
    - added direct button `Источники данных...`,
    - clarified field names for local/default role:
      - `Локальный IP (Modbus)`, `Локальный порт`, `Локальный Unit ID`,
      - `Таймаут/Повторы/Смещение (лок./по умолч.)`.
  - `ui.py` -> `Сигналы графика`:
    - added explicit source selector for new signals/ranges (`Источник` combobox),
    - added quick button `Источники...`,
    - added `Все источники` tab for cross-source overview.
  - data consistency / save logic:
    - when active tab is `Все источники`, saving now replaces full signal list (prevents duplication on save),
    - clear/remove flows handle `Все источники` correctly (including archive cleanup id set),
    - runtime remote-tag repair updates visible table also in `Все источники` tab.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_ui_recorder_command tests.test_models_profile tests.test_recorder_service` -> OK
  - build:
    - canonical build blocked by locked `dist/TrendRecorder.exe`,
    - side-by-side binaries built:
      - `dist/TrendClient_uiopt.exe` -> `2026-03-12 10:26:15`
      - `dist/TrendRecorder_uiopt.exe` -> `2026-03-12 10:27:19`

- 2026-03-12 (shutdown fix: process stayed in Task Manager after window close):
  - root cause:
    - when close behavior was `tray`, but tray was unavailable/not visible, close flow ignored window close and process remained running.
  - `ui.py` fixes:
    - added `_can_minimize_to_tray()` check,
    - `_minimize_to_tray()` now returns `bool` success and no longer falls back to simple minimize,
    - in `closeEvent(...)`: if `tray` requested but unavailable -> forced fallback to normal exit,
    - in `ask` mode, `В трей` button shown only when tray is реально available,
    - `_sync_close_behavior_actions()` disables tray-related actions when tray unavailable and auto-falls back from `tray` to `exit`.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_ui_recorder_command tests.test_models_profile tests.test_recorder_service` -> OK

- 2026-03-12 (source scan: local host no longer appears as remote):
  - root cause:
    - subnet scan probed every host in range, including local machine IP where local Recorder API listens;
    - local machine was added as a "remote recorder source", which looked misleading in UI.
  - `ui.py` fixes:
    - added local IPv4 detection helper (`_local_ipv4_candidates`) based on hostname/FQDN, UDP local socket and NIC addresses;
    - `_scan_subnet_for_sources(...)` now skips probe targets that match local IPv4 addresses;
    - scan status now shows skipped local-address count;
    - corrected local-IP candidate logic: removed `profile.ip` from local candidates (this field can point to external Modbus device, not local host).
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_ui_recorder_command tests.test_models_profile tests.test_recorder_service` -> OK

- 2026-03-12 (graph smoothing option to reduce "stair-step" look):
  - `models.py`:
    - added profile fields:
      - `plot_smoothing_enabled` (bool),
      - `plot_smoothing_window` (odd points, normalized to `3..31`).
  - `chart.py`:
    - added visual-only smoothing pipeline (`set_curve_smoothing(...)`),
    - smoothing uses centered moving average on rendered points,
    - BOOL signals are excluded from smoothing to preserve discrete shape,
    - smoothing does not modify archive/raw buffers (only curve drawing).
  - `ui.py`:
    - `Настройки графика` extended with:
      - `Сглаживание кривой` checkbox,
      - `Окно сглаживания` spin (`3..31`),
      - hint label that smoothing affects only visualization,
    - profile load/apply now pushes smoothing settings into chart runtime,
    - print export uses same smoothing mode as current profile.
  - tests:
    - `tests/test_models_profile.py` added roundtrip/normalization test for smoothing fields.
  - verification:
    - `python -m py_compile trend_analyzer/chart.py trend_analyzer/models.py trend_analyzer/ui.py tests/test_models_profile.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (themes + archive write toggle fix + retention by DB size):
  - UI theming:
    - `models.py`: added `ui_theme_preset` to profile.
    - `ui.py`:
      - added global UI theme engine with presets (`Темная`, `Светлая`, `Графит`, `Песочная`),
      - `Настройки графика` now includes `Тема интерфейса`,
      - theme is applied runtime-wide (main window + dialogs + controls + menu/status colors),
      - theme persists per profile and is restored on profile load.
  - Archive write toggle bug:
    - `recorder_service.py`: fixed critical issue where recorder still wrote sample rows while `archive_to_db=False`,
    - `recorder_service.py`: connection events are also skipped when archive writing is disabled,
    - `ui.py`: `Архив и экспорт -> Писать в БД` now pushes config to local recorder runtime immediately (no extra save/apply step).
  - Retention by DB size:
    - `models.py`: added retention fields:
      - `archive_retention_mode` (`days|size`),
      - `archive_max_size_value`,
      - `archive_max_size_unit` (`MB|GB`).
    - `ui.py` (`Настройки подключения`):
      - added retention mode selector (`По времени (дни)` / `По размеру БД`),
      - added size limit controls (`value + MB/GB`),
      - controls are enabled/disabled by selected retention mode.
    - `storage.py`:
      - added `db_size_bytes()`,
      - added `prune_to_max_size(...)` with chunk cleanup and optional vacuum/checkpoint.
    - `ui.py` and `recorder_service.py`:
      - retention policy now supports both day-based and size-based cleanup paths.
  - tests:
    - `tests/test_models_profile.py`: added coverage for retention-size + theme fields.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py trend_analyzer/models.py trend_analyzer/storage.py trend_analyzer/recorder_service.py tests/test_models_profile.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (light themes visibility + unified theme for windows and chart):
  - `trend_analyzer/ui.py`:
    - fixed control icons contrast on light themes:
      - values-panel control icons now use theme-aware color (`icon`/`text`) instead of hardcoded light stroke;
      - icon set is refreshed each time runtime theme is applied.
    - added 2 extra light-but-darker presets:
      - `Светлая мягкая` (`light_soft`),
      - `Светлая теплая` (`light_warm`).
    - extended theme presets with chart defaults:
      - `chart_bg`, `chart_grid`, `chart_grid_alpha`.
    - linked theme selection with chart settings:
      - graph settings combo renamed to `Тема интерфейса и графика`,
      - selecting a theme now preloads chart colors/grid alpha from same preset and applies them runtime.
  - behavior:
    - visual profile now affects both window chrome/widgets and chart appearance in one flow.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (spin/combo arrows usability + Enter key profile-creation fix):
  - `trend_analyzer/ui.py`:
    - improved click targets for editors in themed stylesheet:
      - larger `QSpinBox/QDoubleSpinBox` up/down button zones,
      - larger `QComboBox` dropdown zone and arrow size,
      - explicit hover/pressed states for these controls.
    - fixed accidental profile creation on `Enter` in connection fields:
      - disabled `autoDefault/default` for profile toolbar buttons (`Новый/Клон/Удалить/Сохранить`)
      - now `Enter` in `IP`/other fields no longer triggers `Новый`.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (light theme checkbox visibility):
  - `trend_analyzer/ui.py`:
    - improved checkbox indicator contrast in themed stylesheet for both widget and table/tree/list delegates:
      - `QCheckBox::indicator`,
      - `QTableView::indicator`,
      - `QTreeView::indicator`,
      - `QListView::indicator`.
    - added explicit states for unchecked/hover/checked/disabled to avoid blending with light backgrounds.
  - result:
    - checkboxes in light themes are clearly visible in both "empty" and "checked" states.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK

- 2026-03-12 (menu spacing + non-blocking archive retention trim on limit changes):
  - `trend_analyzer/ui.py`:
    - improved menu item spacing in themed stylesheet:
      - `QMenuBar::item` now has explicit horizontal padding/margins,
      - `QMenu::item` now has explicit left/right padding and small margins.
    - implemented asynchronous archive retention trim workflow:
      - added dedicated single-thread maintenance executor + polling timer,
      - on `Применить` / `Сохранить` (when retention settings changed), app starts background prune task,
      - status bar progress indicator (`busy_progress`) is used while prune is running,
      - UI remains responsive; if user changes settings again during prune, new request is queued.
    - added graceful shutdown for maintenance executor in `closeEvent`.
    - `save_config` now supports `trigger_retention_prune` flag (disabled on app close save path).
  - `trend_analyzer/storage.py`:
    - added `ArchiveStore.vacuum()` helper for explicit DB compaction after retention cleanup.
  - behavior:
    - when archive limit is reduced (e.g. 1000 MB -> 100 MB, or stricter day retention), old rows are trimmed in background without freezing UI.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py trend_analyzer/storage.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (flat buttons + stronger arrow/dropdown controls):
  - `trend_analyzer/ui.py`:
    - switched buttons to flat-corner style (no rounding):
      - global `QPushButton/QToolButton` in theme stylesheet,
      - values panel control buttons,
      - color swatch buttons in values table.
    - improved visibility/target size of spin/combo controls:
      - wider `QSpinBox/QDoubleSpinBox` up/down button area (`24px`),
      - larger up/down arrows (`13x13`),
      - wider `QComboBox` drop-down area (`24px`),
      - stronger drop-down separator (`border-left: 2px`),
      - larger combo arrow (`13x13`).
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (checkbox visual cleanup):
  - `trend_analyzer/ui.py`:
    - refined themed checkbox indicator look in light UI:
      - removed filled-accent square style on checked state,
      - switched to clean outlined box + native check mark visibility,
      - kept explicit contrast for unchecked/hover/disabled states,
      - indicator corners aligned with flat visual style (`radius=0`).
  - result:
    - checkboxes no longer look like solid blue blocks; checked state remains clear and cleaner.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (values-table checkbox alignment fix):
  - `trend_analyzer/ui.py`:
    - fixed `Вид` column checkbox sticking to left edge in bottom values table.
    - checkbox is now placed inside centered cell container (`QWidget + QHBoxLayout(AlignCenter)`), keeping stable layout on all themes.
    - context-menu/group selection paths updated to resolve checkbox from wrapped cell widget.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (checkbox checkmark visibility restore):
  - `trend_analyzer/ui.py`:
    - reverted over-styled checkbox indicator states that suppressed native checkmark rendering.
    - kept only safe indicator sizing (`14x14`) + checkbox text spacing.
    - result: native checkbox glyph (галочка) is visible again across light/dark themes.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (global arrow indicators for spin/combo editors, including table windows):
  - `trend_analyzer/ui.py`:
    - replaced fragile native-arrow reliance with explicit themed arrow indicators in stylesheet:
      - `QSpinBox/QDoubleSpinBox` up/down arrows now rendered as clear triangles,
      - `QComboBox` down-arrow rendered as clear triangle,
      - disabled state arrows use muted color but remain visible.
    - added global normalization for spin controls:
      - `_enforce_spin_arrow_mode(...)` sets `UpDownArrows` symbols and acceleration,
      - `_enforce_spin_controls_globally(...)` is called from runtime theme apply.
    - applied spin-arrow normalization on dynamically created table editors:
      - signals table (`bit`, `axis`),
      - Modbus registers table (`address`, `bit`, `value`).
  - result:
    - arrow indicators are now consistent and visible across all windows, not only connection settings.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (reworked arrow rendering: two proper theme variants, no square artifacts):
  - issue:
    - previous stylesheet-only arrow hack produced broken/square markers in several windows.
  - `trend_analyzer/ui.py`:
    - added `ThemeArrowProxyStyle(QProxyStyle)`:
      - draws clean vector triangles for:
        - `PE_IndicatorSpinUp`,
        - `PE_IndicatorSpinDown`,
        - `PE_IndicatorArrowDown`.
      - uses theme colors (`text` for enabled, `muted_text` for disabled).
    - added runtime hook `_apply_theme_arrow_style(theme_id)` and integrated it into `_apply_ui_theme_runtime(...)`.
    - simplified stylesheet arrow blocks to sizing only (removed fragile border-triangle CSS).
  - result:
    - arrows are now consistently visible and normal-looking on both dark and light themes across all windows.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (arrow hotfix after user validation):
  - issue:
    - arrows still not visible in runtime due stylesheet subcontrol overrides.
  - `trend_analyzer/ui.py`:
    - removed stylesheet rules that overrode `QSpinBox/QComboBox` arrow subcontrols.
    - expanded proxy-style arrow painting to handle both:
      - spin primitives (`PE_IndicatorSpinUp/Down`),
      - generic arrow primitives (`PE_IndicatorArrowUp/Down`).
    - adjusted theme-apply order: arrow proxy style is applied before stylesheet.
    - improved base style unwrap in `_apply_theme_arrow_style` to avoid nested stylesheet-style wrappers.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (final arrow fix via bundled SVG assets):
  - `assets/`:
    - added explicit arrow icons:
      - `arrow_up_light.svg`,
      - `arrow_down_light.svg`,
      - `arrow_up_dark.svg`,
      - `arrow_down_dark.svg`.
  - `trend_analyzer/ui.py`:
    - removed proxy-style arrow rendering path.
    - added theme-aware asset resolver for arrow icons in stylesheet.
    - `QSpinBox/QDoubleSpinBox/QComboBox` now use direct SVG icons in QSS instead of relying on native or CSS-triangle drawing.
    - theme chooses dark or light arrow asset variant based on button background luminance.
  - note:
    - existing built `.exe` must be rebuilt to include newly added SVG assets; source run only needs full restart.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (guaranteed arrow rendering via custom widget paint):
  - issue:
    - even with SVGs in QSS, runtime still showed empty arrow areas in combo/spin controls.
  - `trend_analyzer/ui.py`:
    - aliased base Qt widgets (`QtQComboBox`, `QtQSpinBox`, `QtQDoubleSpinBox`).
    - added `_ThemedArrowMixin`, `ThemedComboBox`, `ThemedSpinBox`, `ThemedDoubleSpinBox`.
    - controls now paint real triangle arrows in `paintEvent(...)` on top of the native control using palette-aware colors.
    - rebound module-level `QComboBox/QSpinBox/QDoubleSpinBox` to these themed subclasses so the change applies across the app, including dynamic table editors.
    - increased explicit spacing between `Авто X` and `Курсор` in the values-panel header.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (combo arrows refined):
  - `trend_analyzer/ui.py`:
    - extended `_paint_triangle(...)` with optional `max_size` and `vertical_offset`.
    - reduced `QComboBox` arrow size and tightened its paint rect so drop-down arrows look smaller and cleaner.
    - spin-box arrows left unchanged.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK

- 2026-03-12 (signal table checkboxes centered):
  - issue:
    - signal-table `Вкл` column still used item checkboxes, so indicators stayed glued to the left edge.
  - `trend_analyzer/ui.py`:
    - signal enabled column moved from `QTableWidgetItem(checkState)` to a centered `cellWidget` container with `QCheckBox`.
    - added helpers:
      - `_signal_enabled_checkbox(...)`
      - `_create_signal_enabled_cell(...)`
      - `_on_signal_enabled_toggled(...)`
    - updated sync/save paths to read and write the centered checkbox widget instead of item check state.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (build slimming + menu run-state indicator):
  - `trend_analyzer/ui.py`:
    - `Рабочий процесс -> Старт/Стоп` converted to exclusive checkable actions.
    - added `_sync_run_state_actions()` and wired it into startup/mode/start/stop state refresh paths.
    - current run state is now visible in the menu via the same checkmark style used for mode selection.
  - build config:
    - `TrendClient.spec`, `TrendClient_opt.spec`, `TrendClient_uiopt.spec`:
      - replaced `collect_all('pyqtgraph')` with filtered `collect_submodules('pyqtgraph')`,
      - removed unused client hidden imports `PySide6.QtSvg` and `PySide6.QtOpenGLWidgets`,
      - added excludes for `PySide2`, `PyQt5`, `PyQt6`, `tkinter`, `matplotlib`, `IPython`,
      - set `optimize=1`.
    - `build_roles_windows.ps1`:
      - TrendClient build switched to `PyInstaller TrendClient.spec`.
    - `build_portable.ps1`:
      - replaced `--collect-all pyqtgraph` with `--collect-submodules pyqtgraph`,
      - excluded `pyqtgraph.examples`, `pyqtgraph.tests` and unused GUI/toolkit modules.
  - note:
    - exe size reduction will be visible only after next rebuild; this turn did not rebuild binaries.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - spec files compile via `compile(..., 'exec')` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (TrendRecorder exit now stops background recorder too):
  - issue:
    - exiting `TrendRecorder` hid the tray icon, but background `TrendRecorder.exe` could remain in Task Manager because the recorder service process was not stopped on tray exit.
  - `trend_analyzer/recorder_tray.py`:
    - tray controller now tracks shutdown state and hooks `QApplication.aboutToQuit`.
    - added `_terminate_recorder_pid(...)` and upgraded `_stop_recorder(...)` to return success/failure and support silent shutdown.
    - `_exit_tray()` now performs coordinated shutdown:
      - stop status timer,
      - stop recorder service,
      - hide/delete tray icon,
      - then quit Qt app.
    - after `app.exec()` returns, the tray instance lock is explicitly released in `finally`.
  - verification:
    - `python -m py_compile trend_analyzer/recorder_tray.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (startup crash fix after run-state menu indicator):
  - issue:
    - app startup crashed with `AttributeError: 'MainWindow' object has no attribute 'mode_combo'`.
    - root cause: `_sync_run_state_actions()` was called from `_build_menu_bar()` before `mode_combo` had been created.
  - `trend_analyzer/ui.py`:
    - hardened `_sync_run_state_actions()`:
      - returns safely if start/stop actions do not exist yet,
      - uses `mode_combo` only if it already exists,
      - otherwise falls back to `current_profile.work_mode` or `"online"`.
  - note:
    - this fixes the crash; exe size optimization should be evaluated separately with rebuild + size measurement.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (scales window checkbox fix + values header spacing):
  - issue:
    - `Настройка шкал` looked broken in `Авто Y` column.
    - values-panel header still looked cramped around `Курсор`.
  - `trend_analyzer/ui.py`:
    - increased spacing around the `Курсор` checkbox in the values header.
    - converted `scales_table` column `Авто Y` from item checkbox rendering to centered `cellWidget` checkbox rendering.
    - added helpers:
      - `_scale_auto_y_checkbox(...)`
      - `_create_scale_auto_y_cell(...)`
      - `_on_scale_auto_y_toggled(...)`
    - min/max editability now syncs from the centered checkbox widget.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (quick access `Авто Y` added near `Авто X`):
  - `trend_analyzer/ui.py`:
    - added `values_auto_y_checkbox` to the lower values-panel header next to `Авто X`.
    - added `_on_values_auto_y_toggled(...)` to forward the quick toggle into `chart.set_auto_y(...)`.
    - extended `_on_chart_auto_mode_changed(...)` to sync the new checkbox from chart state.
    - runtime view apply now initializes the checkbox state during profile/view restore.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (signal axis change now applies from signals window save):
  - issue:
    - changing signal `Шкала` in `Сигналы графика` and pressing `Сохранить конфигурацию` persisted config but did not update runtime chart meta / lower values table.
  - `trend_analyzer/ui.py`:
    - `_save_from_signals_window()` now first calls `_apply_current_profile(restart_live=False)` and only then `_save_config()`.
    - result: signal edits from the signals window are applied to the active chart model immediately before persistence.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK

- 2026-03-12 (live graph now works with `Писать в БД` disabled):
  - issue:
    - when `Архив и экспорт -> Писать в БД` was unchecked, live graph stopped updating because local live path depended on reading new samples from SQLite.
  - `trend_analyzer/recorder_service.py`:
    - added in-memory live ring buffers for samples and connection events.
    - recorder now records live data into memory regardless of archive-to-db state.
    - profile switch resets these in-memory live buffers.
  - `trend_analyzer/recorder_api.py`:
    - `/v1/live` now serves in-memory live data when `archive_to_db == False`.
    - `/v1/history` now serves current-session in-memory history when `archive_to_db == False`.
  - `trend_analyzer/ui.py`:
    - local online mode now switches to local recorder API live polling when archive writing is disabled.
    - added bootstrap/history/sample parsing for local API live path.
    - startup status text now distinguishes `live-просмотр без архива`.
  - result:
    - graph keeps updating live with `Писать в БД = выкл`,
    - after app/recoreder restart there is no persisted archive history, matching the requested behavior.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py trend_analyzer/recorder_service.py trend_analyzer/recorder_api.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK
- 2026-03-12 (TrendRecorder tray exit now cleans residual recorder/bootstrap processes):
  - issue:
    - user still observed `TrendRecorder.exe` hanging in Task Manager after tray exit.
    - likely frozen onefile scenario: graceful stop may leave related `TrendRecorder.exe --recorder` / bootstrap processes alive.
  - `trend_analyzer/recorder_tray.py`:
    - added residual recorder process discovery via `psutil` for sibling `--recorder` processes of the same executable.
    - added helper to terminate PID groups with wait/recheck.
    - `_stop_recorder()` now additionally cleans residual recorder processes after graceful and forced stop paths.
    - tray hard-exit fallback now also kills:
      - residual `--recorder` processes,
      - same-exe bootstrap parent processes from the current parent chain,
      - then forces `os._exit(0)`.
  - verification:
    - `python -m py_compile trend_analyzer/recorder_tray.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command` -> OK
- 2026-03-12 (Auto X no longer freezes live chart when toggled off):
  - issue:
    - when `Авто X` was turned off in online mode, the graph could appear frozen immediately.
    - when turned back on, the recent interval could look partially lost because UI replaced live buffers with archived history during the toggle path.
  - `trend_analyzer/chart.py`:
    - added soft latest-edge follow while `Авто X` is off until the user performs a real manual X-range change.
    - manual X-range changes now disable that soft follow.
    - live append path and cursor placement now respect `follows_latest_x()` instead of only raw `auto_x`.
  - `trend_analyzer/ui.py`:
    - removed forced archive/history replacement on plain Auto X toggle.
    - visible-history reload is now triggered only after actual manual X navigation, not while chart is still effectively following the latest edge.
    - UI heartbeat now also works while chart is in soft latest-follow mode.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py trend_analyzer/chart.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore` -> OK
- 2026-03-12 (TrendRecorder exit now uses external cleanup helper for lingering onefile processes):
  - issue:
    - even after strengthened in-process shutdown, user still observed `TrendRecorder.exe` lingering in Task Manager after exit.
    - likely root cause: PyInstaller onefile parent/bootloader chain can survive in-process cleanup attempts.
  - `trend_analyzer/recorder_tray.py`:
    - added hidden detached Windows cleanup helper launched via PowerShell.
    - helper waits briefly, then force-kills:
      - same-executable `TrendRecorder.exe --recorder` leftovers after stop,
      - on tray exit: any same-executable `TrendRecorder.exe` remnants for the current build path.
    - helper is filtered by exact `ExecutablePath`, so cleanup is scoped to the current binary path.
  - verification:
    - `python -m py_compile trend_analyzer/recorder_tray.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore` -> OK
- 2026-03-12 (manual X pan no longer clears chart on empty history window):
  - issue:
    - when user started dragging X axis, chart data could disappear immediately.
    - root cause: manual history reload could call `set_archive_data(...)` even when the queried visible window contained no points, which cleared current signal buffers.
  - `trend_analyzer/ui.py`:
    - added `_samples_payload_has_points(...)`.
    - `_load_history_window_from_db(...)` now aborts without touching chart buffers when the requested history window returns no actual points.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore` -> OK
- 2026-03-12 (chart buffers split into live/history merge; client shutdown strengthened):
  - issue:
    - online chart still behaved incorrectly during manual X browsing because history loading replaced the single chart buffer.
    - `TrendClient.exe` could also linger after window close due to unfinished executor threads / onefile process tail.
  - `trend_analyzer/chart.py`:
    - introduced separate internal buffers:
      - `_live_buffers`
      - `_history_buffers`
      - merged `_buffers` for rendering/cursor/stats.
    - live append now updates only live buffers and refreshes merged series.
    - `set_archive_data(...)` now updates only history buffers and merges with live instead of replacing it.
    - added merge helper and recomputation of merged latest timestamp.
  - `trend_analyzer/ui.py`:
    - `_shutdown_maintenance_executor()` now uses `executor.shutdown(wait=True, cancel_futures=True)` so non-daemon executor threads do not keep the client process alive.
    - added frozen-Windows self-cleanup helper to kill lingering same-executable client process tails after exit.
    - `run_app()` now releases `SingleInstanceLock` in `finally`.
  - `trend_analyzer/recorder_tray.py`:
    - restricted recorder cleanup helper to frozen builds only, avoiding accidental cleanup of generic `python.exe` in source runs.
  - tests:
    - added `tests/test_chart_history_merge.py` for chart merge helper and empty-history guard helper.
  - verification:
    - `python -m py_compile trend_analyzer/chart.py trend_analyzer/ui.py trend_analyzer/recorder_tray.py tests/test_chart_history_merge.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_instance_lock tests.test_recorder_shared` -> OK
- 2026-03-12 (statistics window now disables Auto X without hidden soft-follow):
  - issue:
    - opening `Анализ участка графика` disabled the visible `Авто X` checkbox, but chart still continued X autoscroll because internal soft-follow remained active.
  - `trend_analyzer/chart.py`:
    - added `force_manual_x()` to disable both:
      - visible `auto_x`,
      - internal `_soft_follow_latest_x`.
  - `trend_analyzer/ui.py`:
    - `_disable_auto_x_for_stats()` now uses `chart.force_manual_x()` instead of plain `set_auto_x(False)`.
  - verification:
    - `python -m py_compile trend_analyzer/chart.py trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_instance_lock tests.test_recorder_shared` -> OK
- 2026-03-12 (exit cleanup switched to direct taskkill by image name for frozen binaries):
  - issue:
    - even path-filtered external cleanup still could miss lingering `TrendRecorder.exe` / `TrendClient.exe` tails in user environment.
    - user explicitly requested simple "kill all processes with this name" semantics.
  - `trend_analyzer/recorder_tray.py`:
    - for full tray exit cleanup helper now uses delayed `taskkill /IM TrendRecorder.exe /F` (resolved from current executable basename).
    - recorder-only stop path remains more selective and still avoids killing tray itself.
  - `trend_analyzer/ui.py`:
    - frozen client exit helper now uses delayed `taskkill /IM <current exe name> /F`.
  - verification:
    - `python -m py_compile trend_analyzer/recorder_tray.py trend_analyzer/ui.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_instance_lock tests.test_recorder_shared` -> OK
- 2026-03-12 (exit cleanup helper switched from PowerShell to cmd.exe):
  - issue:
    - previous delayed cleanup still could fail in user environment; one likely weak point was launching cleanup via PowerShell path/policy branch.
  - `trend_analyzer/ui.py`:
    - frozen client cleanup helper now uses:
      - `cmd.exe /d /c "timeout /t 3 /nobreak >nul & taskkill /IM <exe> /F"`
  - `trend_analyzer/recorder_tray.py`:
    - full tray-exit cleanup helper now also uses `cmd.exe` delayed `taskkill /IM`.
    - selective recorder-only stop path still keeps PowerShell-based PID filtering, but is now launched through `cmd.exe /c`.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py trend_analyzer/recorder_tray.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_models_profile tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_instance_lock tests.test_recorder_shared` -> OK
- 2026-03-12 (fixed mojibake in Modbus register write/read statuses):
  - issue:
    - the `Регистры Modbus` window still showed broken CP1251/UTF-8 mojibake when writing values, reading batches, or reporting connection/write errors.
    - root cause was not Qt rendering; several status strings in `trend_analyzer/ui.py` were already saved in corrupted form directly in the source.
  - `trend_analyzer/ui.py`:
    - replaced corrupted literals in the Modbus register window flow:
      - single-row write status/error
      - add-range summary
      - local connect failure text
      - BOOL pre-write read error
      - remote single-row "written" message
      - batch read summary
      - batch write error + final summary
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
- 2026-03-12 (auto-x is forced off when no visible signals remain):
  - issue:
    - if the bottom values table had no visible signals, `Авто X` could remain active and the empty chart kept scrolling in time.
  - `trend_analyzer/ui.py`:
    - added `_disable_auto_x_when_no_visible_signals(...)`
    - `_update_values_table(...)` now forces manual X mode when the active visible rows count becomes zero
    - implementation uses `chart.force_manual_x()` and syncs both the menu action and the bottom `Авто X` checkbox to unchecked
    - no automatic re-enable is performed when signals appear again
  - verification:
    - `python -m py_compile trend_analyzer/ui.py trend_analyzer/chart.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_ui_history_restore tests.test_ui_recorder_command` -> OK
- 2026-03-12 (added Auto Y to View menu and Auto X column to scales window):
  - issue:
    - UI was inconsistent: bottom values bar already had `Авто Y`, but the `Вид` menu only exposed `Авто X`, and `Настройка шкал` exposed only `Авто Y`.
  - `trend_analyzer/ui.py`:
    - added `Вид -> Авто Y` action and synchronized it with the bottom `Авто Y` checkbox
    - expanded `Настройка шкал` from 5 to 6 columns:
      - `Шкала | Авто X | Авто Y | Мин | Макс | Сигналы`
    - added scale-table helpers for the new global `Авто X` checkbox column
    - row editing logic for `Мин/Макс` moved to shifted columns
  - `trend_analyzer/chart.py`:
    - `set_auto_x()` and `force_manual_x()` now emit `scales_changed`
    - scales payload now includes `auto_x` so the scales window stays in sync
  - verification:
    - `python -m py_compile trend_analyzer/ui.py trend_analyzer/chart.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_chart_history_merge tests.test_ui_history_restore tests.test_ui_recorder_command tests.test_models_profile` -> OK
- 2026-03-12 (archive DB normalized to integer refs):
  - issue:
    - `samples` stored full text `profile_id` + full text `signal_id` in every row, and indexes repeated the same long UUID strings.
    - this wasted a large amount of DB space, especially with frequent sampling.
  - new schema:
    - physical tables:
      - `profiles_meta(id INTEGER, profile_id TEXT UNIQUE, ...)`
      - `signal_catalog(id INTEGER, profile_ref INTEGER, signal_id TEXT, signal_name TEXT, ...)`
      - `sample_rows(id INTEGER, profile_ref INTEGER, signal_ref INTEGER, ts REAL, value REAL)`
      - `connection_event_rows(id INTEGER, profile_ref INTEGER, ts REAL, is_connected INTEGER)`
    - compatibility views kept for read-side SQL:
      - `samples`
      - `signals_meta`
      - `connection_events`
  - implementation:
    - `ArchiveStore.insert_batch(...)` now resolves integer refs and writes only compact integer foreign keys into `sample_rows`
    - `insert_connection_event`, retention pruning, size pruning, `delete_signals`, and `min_sample_ts` were updated to operate on normalized tables
    - legacy/old schema is dropped and recreated without migration (acceptable for current phase because user has no important archives yet)
    - archive clear action in UI now wipes physical normalized tables and resets corresponding sqlite sequences
  - tests:
    - updated `tests/test_storage_archive.py` to validate the new normalized physical schema while preserving compatibility reads through `samples/signals_meta`
  - verification:
    - `python -m py_compile trend_analyzer/storage.py trend_analyzer/ui.py tests/test_storage_archive.py` -> OK
    - `.venv\\Scripts\\python -m unittest tests.test_storage_archive tests.test_chart_history_merge tests.test_ui_history_restore tests.test_ui_recorder_command tests.test_recorder_service tests.test_models_profile` -> OK
- 2026-03-13 (Enter in connection settings no longer opens data-sources window):
  - issue:
    - in `Настройки подключения`, pressing `Enter` while editing IP/other fields could trigger `Источники данных...` instead of just confirming field editing.
  - `trend_analyzer/ui.py`:
    - disabled `autoDefault/default` on:
      - `connection_manage_sources_btn`
      - `apply_btn`
      - `clear_archive_db_btn`
    - profile-management buttons were already protected earlier; now the remaining connection-window actions are protected too.
  - verification:
    - `python -m py_compile trend_analyzer/ui.py` -> OK
- 2026-03-12 (v1.5.19, live-start stabilization + full mojibake cleanup in ui.py):
  - scope:
    - this pass does **not** change archive schema logic (`storage.py` untouched),
    - normalized DB structure from previous step remains active (`profiles_meta`, `signal_catalog`, `sample_rows`, `connection_event_rows` + compatibility views).
  - `trend_analyzer/ui.py`:
    - added lightweight live-start history policy:
      - `_live_startup_history_cap_s()`
      - `_lightweight_live_history_span_s(...)`
    - `_start_worker(...)` now clamps startup restore span to avoid heavy freezes on launch with many active tags.
    - `_on_render_chart_toggled(...)` in online mode now restores history with the same lightweight span.
    - `_load_recent_online_history_from_db(...)` reworked:
      - uses adaptive `_query_samples_for_window(...)` (point-budget per signal),
      - fallback to `_query_latest_samples_snapshot(...)` when recent window has no points,
      - keeps `_history_loaded_bucket_s` from actual query bucket.
    - cleaned remaining mojibake literals in status/error/export/import/print messages (source strings now UTF-8 readable in code).
  - versioning:
    - `trend_analyzer/version.py`: `1.5.18` -> `1.5.19`
    - `CHANGELOG.md` updated with `1.5.19` entry.
    - version headers refreshed in `README.md`, `DEVELOPMENT_LOG_RU.md`.
  - build:
    - rebuilt role executables:
      - `dist/TrendClient.exe` (2026-03-12 19:07:32)
      - `dist/TrendRecorder.exe` (2026-03-12 19:08:37)
  - verification:
    - `.venv\\Scripts\\python -m py_compile trend_analyzer\\ui.py trend_analyzer\\chart.py trend_analyzer\\history_restore.py` -> OK
    - `.venv\\Scripts\\python -m unittest discover -s tests` -> OK (`Ran 43 tests`)

## 2026-03-13 - live-отрисовка не должна зависеть от частоты архивации

- Проблема:
  - при включенном локальном `TrendRecorder` и включенной записи в БД live-график в `TrendClient` читал локальные точки из таблицы `samples`, а не из `/v1/live`.
  - из-за этого фактическая частота обновления экрана была привязана к `Частота архивации`, а не к `Частота опроса` + `Интервал отрисовки`.
  - симптом: пользователь видел ступени и редкие обновления даже при `poll=100 ms`, `render=200 ms`.

- Исправление:
  - `trend_analyzer/ui.py`
    - `_start_worker(...)` теперь предпочитает локальный recorder API для live-потока всегда, если API доступен.
    - доступ к локальной БД оставлен как fallback только когда recorder API недоступен, но архив в БД включен.
    - стартовый preload/live-status приведены к новой логике (`local_api_live_enabled` приоритетнее `local_db_live_enabled`).
    - `_poll_db_live_stream(...)` теперь сначала берет локальный live через `/v1/live`, и только при отсутствии API использует DB-tail.

- Ожидаемое поведение после фикса:
  - значения на экран должны попадать с частотой, ограниченной в основном `max(Частота опроса, Интервал отрисовки)`.
  - `Частота архивации` влияет только на запись в БД, но не должна тормозить live-отрисовку.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_recorder_service tests.test_ui_history_restore` -> OK

## 2026-03-13 - упрощение окна "Настройки подключения"

- `trend_analyzer/ui.py`
  - окно настроек подключения переразложено на три логических блока:
    - `Основное`
    - `Архив`
    - `Дополнительно`
  - архивные фильтры (`deadband`, `keepalive`) вынесены в отдельный подблок и показываются только если включено `Архив: только изменения`.
  - технические параметры (`Таймаут`, `Повторы`, `Смещение адреса`, `API host/port/token`) спрятаны под переключатель `Показать доп. настройки`.
  - параметры `API host/port/token` дополнительно скрываются, если выключен `API локального recorder`.
  - логика самих настроек не менялась, изменена только подача/видимость.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_models_profile` -> OK

## 2026-03-13 - жёсткое выключение Auto X по птичке

- `trend_analyzer/chart.py`
  - `set_auto_x(False)` больше не включает скрытый `soft follow`.
  - теперь пользовательское выключение `Авто X` через меню, нижнюю панель, окно шкал и контекстное меню графика должно сразу останавливать автопрокрутку по X.
  - раньше viewport продолжал ехать до первого ручного движения мышью, из-за чего птичка выглядела "нерабочей".

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\chart.py D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_chart_history_merge tests.test_ui_history_restore tests.test_ui_recorder_command` -> OK

## 2026-03-13 - `/v1/live` отвязан от архивной таблицы

- Диагностика:
  - при `poll_interval_ms=100` и `render_interval_ms=200` пользователь всё равно видел ступени порядка 1-2 секунд.
  - проверка реального `recorder_status.json` показала:
    - `cycles_total` соответствует циклу ~100 мс,
    - `samples_read_total` растёт как ~10 сигналов на цикл,
    - но `/v1/live` возвращал точки с шагом ~1.0-1.1 с.
  - значит узкое место было не в Modbus polling и не в render timer, а в самом recorder API.

- Причина:
  - `trend_analyzer/recorder_api.py`
    - endpoint `/v1/live` при `archive_to_db=True` читал данные из `samples`/`connection_events`,
    - то есть live endpoint фактически отдавал архивную частоту (`archive_interval_ms`), а не живой буфер recorder service.

- Исправление:
  - `/v1/live` теперь всегда использует `RecorderService.get_live_stream_payload(...)`,
    независимо от того, включена запись в БД или нет.
  - история (`/v1/history`) по-прежнему может идти из БД.

- Ожидаемый эффект:
  - live-график должен следовать частоте опроса/отрисовки,
  - `archive_interval_ms` больше не должен диктовать видимую дискретность realtime-графика.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\recorder_api.py D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_recorder_service tests.test_ui_recorder_command tests.test_ui_history_restore` -> OK

## 0.66 - 2026-03-13 - direct Modbus в UI синхронизирован с текущими полями окна подключения

- Проблема:
  - local/direct Modbus в нескольких местах UI использовал `current_profile.ip/port/unit_id`, даже если пользователь уже изменил эти поля в окне `Настройки подключения`, но ещё не успел прожать `Применить/Сохранить`.
  - из-за этого возникали рассинхроны:
    - summary мог показывать старый `127.0.0.1`,
    - подписи локального источника могли расходиться,
    - окно `Регистры Modbus -> Локальный (прямой Modbus)` мог пытаться читать/писать не туда, куда пользователь сейчас смотрит в UI.

- Исправление:
  - `trend_analyzer/ui.py`
    - добавлен `_effective_local_modbus_settings()`;
    - helper берёт `host/port/unit_id/timeout/address_offset/retries` из текущих виджетов окна подключения и только потом падает обратно на `current_profile`;
    - на него переведены:
      - `_source_modbus_settings(None)`,
      - `_open_tags_client()`,
      - summary окна подключения,
      - локальные source labels в combo/tab.

- Эффект:
  - локальный direct Modbus и все связанные локальные подписи теперь должны жить по одним и тем же фактическим параметрам;
  - убран класс багов, когда UI уже показывает рабочий PLC address, а runtime direct Modbus ещё ходит по старому адресу.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_models_profile` -> OK

## 0.67 - 2026-03-13 - исправлен NameError после refactor local Modbus settings

- Проблема:
  - после правки `_signal_source_edit_items()` приложение падало на старте окна из-за `NameError: name 'profile' is not defined`.
  - причина: helper был переведён на `_effective_local_modbus_settings()`, но fallback-ветка для `recorder_sources` всё ещё ссылалась на локальную переменную `profile`, которую больше не объявляли.

- Исправление:
  - в `trend_analyzer/ui.py` в `_signal_source_edit_items()` возвращено явное объявление `profile = getattr(self, "current_profile", None)`.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_models_profile` -> OK

## 0.68 - 2026-03-13 - local direct Modbus добит до конца: не только host/port, но и unit/offset/retries

- Что нашли:
  - после фикса `host/port` direct Modbus в окне `Регистры Modbus` всё ещё мог частично жить по устаревшему `current_profile`, потому что:
    - `_read_single_tag()`
    - `_write_single_tag()`
    - `_read_tags_many_with_client()`
    по умолчанию брали `unit_id/address_offset/retries` из `current_profile`, а не из текущих effective-параметров UI.

- Исправление:
  - `trend_analyzer/ui.py`
    - перечисленные helper'ы переведены на `_effective_local_modbus_settings()`;
    - теперь local direct Modbus использует один цельный набор параметров:
      - `host`
      - `port`
      - `unit_id`
      - `timeout`
      - `address_offset`
      - `retries`
    независимо от того, идёт чтение одной строки, batch-read, запись или pulse.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_models_profile tests.test_recorder_service` -> OK

## 0.71 - 2026-03-13 - зафиксирована рабочая версия + безопасная оптимизация live-графика без смены библиотеки

- Рабочая база, от которой продолжаем:
  - окно `Регистры Modbus` для локального источника переведено на local recorder API-прокси, чтобы не открывать второй прямой Modbus TCP-сеанс параллельно recorder;
  - `Импульс` удерживает `1`, пока кнопка нажата, и гарантированно возвращает `0` при отпускании;
  - ложное окно `Есть несохранённые изменения` после явного сохранения убрано;
  - первый `Рабочий процесс -> Старт` стабилизирован: UI дожидается подъёма recorder/API и больше не требует второй клик.

- Безопасные оптимизации производительности:
  - `trend_analyzer/ui.py`
    - local live polling (`recorder API` и fallback-чтение live из SQLite) переведён в неблокирующий executor-путь;
    - `_poll_db_live_stream()` больше не ждёт сеть/SQLite синхронно в UI-потоке;
    - добавлен явный выбор local live transport (`api` или `db`) на старте online-сеанса, чтобы runtime не метался между путями;
    - сохранена жёсткая синхронность `график <-> таблица значений <-> таблица шкал`: частоты обновления не разделялись.
  - `trend_analyzer/chart.py`
    - добавлено инкрементальное обновление live-buffer без полного merge на каждую новую точку;
    - в live follow ужесточены caps на число отрисовываемых точек и downsampling;
    - вне полного rebuild график теперь перерисовывает только изменившиеся/видимые кривые;
    - убрана лишняя повторная перерисовка при программном сдвиге X-окна.

- Проверка:
  - `.venv\\Scripts\\python.exe -m py_compile trend_analyzer\\ui.py trend_analyzer\\chart.py tests\\test_ui_recorder_command.py` -> OK
  - `.venv\\Scripts\\python.exe -m unittest discover -s tests` -> `60 tests OK`
  - `powershell -NoProfile -ExecutionPolicy Bypass -File .\\build_roles_windows.ps1` -> OK

- Сборка:
  - обновлены `dist\\TrendClient.exe` и `dist\\TrendRecorder.exe`;
  - сборка прошла clean, без запущенных `TrendClient/TrendRecorder`;
  - осталось известное нефатальное предупреждение PyInstaller про `pyqtgraph.opengl` / отсутствие `OpenGL`.

## 0.72 - 2026-03-13 - fix: online history/gaps no longer mix stale SQLite archive when archive recording is disabled

- Проблема:
  - после performance-оптимизации в online-режиме при `archive_to_db = false` клиент местами продолжал подгружать историю из старой `archive.db`;
  - из-за этого график мог смешивать старую архивную линию/старые разрывы связи с текущим live-сеансом recorder API.

- Исправление:
  - `trend_analyzer/ui.py`
    - `_load_recent_online_history_from_db(...)` теперь жёстко отключён для online-сценария с `archive_to_db = false`;
    - для online history reload при выключенном архиве добавлен маршрут через local recorder API `/v1/history`, а не через SQLite;
    - перед новым `Рабочий процесс -> Старт` live-график и overlay связи очищаются, чтобы новый live-сеанс не наслаивался на старую картинку.

- Проверка:
  - `.venv\\Scripts\\python.exe -m unittest discover -s tests` -> `64 tests OK`
  - `build_roles_windows.ps1` -> OK

## 0.73 - 2026-03-13 - fix: live smoothing no longer behaves like "jelly"

- Проблема:
  - в режиме live сглаживание использовало центрированное moving average;
  - из-за этого уже показанные точки зависели от будущих значений и начинали "перетекать" назад при приходе новых samples.

- Исправление:
  - `trend_analyzer/chart.py`
    - для live follow сглаживание переведено на causal/trailing average;
    - для статического/history-view оставлено центрированное сглаживание;
    - итог: live-кривая больше не должна "ехать как желе" при включённом сглаживании.

- Проверка:
  - `.venv\\Scripts\\python.exe -m unittest discover -s tests` -> `66 tests OK`
  - `build_roles_windows.ps1` -> OK

## 0.71 - 2026-03-13 - local direct Modbus window reverted to true direct path

- Проблема:
  - окно `Регистры Modbus` после последних фиксов стало смешивать два разных пути для локального источника: `Локальный (прямой Modbus)` местами шёл напрямую в PLC, а местами пытался читать/писать через local recorder API proxy;
  - это давало нестабильное поведение: `Ошибка чтения`, залипание `Импульс=1` и труднодиагностируемые конфликты.

- Исправление:
  - `trend_analyzer/ui.py`
    - для локального источника окна `Регистры Modbus` убрана скрытая переадресация через `_local_modbus_proxy_source()`;
    - локальные `single write`, `pulse`, `read once`, `write marked` снова используют только прямой `ModbusTcpClient`;
    - в `_read_tags_once()` одновременно убран хвост с потенциально неинициализированными `unit_id/address_offset` в fallback-цепочке.

- Зачем:
  - окно называется `Локальный (прямой Modbus)` и должно ходить в устройство ровно одним понятным путём;
  - для диагностического окна предсказуемость важнее, чем попытка "умно" подменить транспорт.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_models_profile tests.test_recorder_service` -> OK

## 0.72 - 2026-03-13 - pulse path hardened in `Регистры Modbus`

- Проблема:
  - пользователь продолжал получать `Ошибка записи`/`Импульс=1` в окне `Регистры Modbus`, хотя low-level direct Modbus до тех же адресов `36/38` и тот же код записи вне UI проходили успешно;
  - значит ломался именно UI-сценарий `интервальное чтение + кнопка Импульс + отпускание`.

- Исправление:
  - `trend_analyzer/ui.py`
    - запись `Импульс` для local/modbus source теперь делает повторные попытки (`retries + 1`) с короткой паузой между ними;
    - добавлен `_active_pulse_tag_id_for_row(...)`, чтобы release-path мог корректно завершить импульс даже если `tag_id` не удалось заново собрать из строки;
    - на время активного импульса окно приостанавливает `Старт`-таймер чтения и автоматически возобновляет его после release;
    - добавлен fail-safe timer на 1200 ms: если release-сигнал потеряется, активный импульс всё равно принудительно сбрасывается в `0`.

- Зачем:
  - убрать залипание `Импульс=1`;
  - убрать ложные `Ошибка записи` от transient-сбоев/рваной UI-цепочки release;
  - изолировать pulse-операцию от фонового polling-а окна регистров.

- Доп.проверка:
  - low-level direct write/read до `192.168.4.218:502`, Unit 1, адресов `36` и `38`, bit 1 -> OK;
  - scripted UI-scenario `polling on -> mousePress/mouseRelease on pulse button` для обеих строк -> status `Импульс=0`, `active_ids=[]`, polling resumed.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_models_profile tests.test_recorder_service` -> OK

## 0.69 - 2026-03-13 - окно "Регистры Modbus" при работающем recorder читает local PLC через local recorder API-прокси

- Наблюдение:
  - direct low-level read до PLC и batched read на тех же адресах проходят успешно;
  - значит `Ошибка чтения` в окне `Регистры Modbus` при нажатии `Старт` была связана не с самим PLC и не с декодером, а с runtime-конфликтом доступа.
  - наиболее вероятный сценарий: локальный recorder уже держит Modbus polling, а окно регистров открывает второй прямой Modbus-клиент в тот же PLC.

- Исправление:
  - `trend_analyzer/ui.py`
    - добавлен `_local_modbus_proxy_source()`:
      - если локальный recorder API доступен и recorder запущен, окно регистров для локального источника использует не прямой второй TCP client, а local recorder API.
    - добавлен `_source_modbus_request_overrides(...)`:
      - local recorder API теперь получает явные overrides `host/port/unit_id/timeout_s/address_offset`,
      - для batch-read также `read_attempts`.
    - на local recorder API-прокси переведены:
      - `_read_tags_once()` для `source is None`,
      - `_on_write_tags_clicked()` для batch-write локального источника,
      - `_on_write_single_tag_row_clicked()` для одиночной записи,
      - `_write_tag_row_forced_value()` для pulse,
      - а также payloads `/v1/modbus/read`, `/v1/modbus/write`, `/v1/modbus/read_many`.

- Эффект:
  - при работающем local recorder окно `Регистры Modbus` не должно больше конфликтовать со вторым прямым Modbus-соединением;
  - `Старт` / `Интервал чтения` для локального источника должны идти через local recorder API-прокси с текущими effective-настройками подключения.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py D:\\TrendAnalyzer\\trend_analyzer\\recorder_api.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_models_profile tests.test_recorder_service` -> OK

## 0.70 - 2026-03-13 - Modbus register browser: fallback с batch-read на single-read + явный текст первой ошибки

- Проблема:
  - даже после выравнивания local Modbus settings и local recorder API proxy пользователь всё ещё видел `Ошибка чтения` в окне `Регистры Modbus`.
  - низкоуровневые прямые чтения тех же адресов в dev-среде проходят, поэтому нужен более надёжный failover прямо в UI-цепочке окна регистров.

- Исправление:
  - `trend_analyzer/ui.py`
    - добавлен `_read_tags_individually_with_client(...)`;
    - если grouped/batch read не вернул значения для части строк local/modbus source, окно автоматически перечитывает отсутствующие строки по одной;
    - в итоговый статус чтения теперь прокидывается текст первой реальной ошибки, а не только общий счётчик `ошибок N`.

- Зачем:
  - для окна диагностики регистров надёжность важнее микрооптимизации;
  - если проблема именно в batch/grouped chain, single-read fallback должен спасти чтение;
  - если чтение всё ещё не проходит, статус теперь покажет первичную причину, а не только общий `Ошибка чтения`.

- Проверка:
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m py_compile D:\\TrendAnalyzer\\trend_analyzer\\ui.py` -> OK
  - `D:\\TrendAnalyzer\\.venv\\Scripts\\python.exe -m unittest tests.test_ui_recorder_command tests.test_ui_history_restore tests.test_models_profile tests.test_recorder_service` -> OK
