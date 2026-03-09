# Changelog
Все значимые изменения проекта фиксируются в этом файле.

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



















