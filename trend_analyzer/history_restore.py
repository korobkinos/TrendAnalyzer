from __future__ import annotations

import math


def compute_live_history_span_s(
    poll_interval_ms: int,
    archive_interval_ms: int,
    archive_on_change_only: bool,
    archive_keepalive_s: int,
    span_hint_s: float | None = None,
    current_span_s: float | None = None,
) -> float:
    candidates: list[float] = []
    for raw_value in (span_hint_s, current_span_s):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0.0:
            candidates.append(value)

    poll_span_s = max(60.0, max(50, int(poll_interval_ms)) / 1000.0 * 120.0)
    archive_span_s = max(60.0, max(50, int(archive_interval_ms)) / 1000.0 * 120.0)
    candidates.extend((poll_span_s, archive_span_s))

    if bool(archive_on_change_only):
        keepalive_s = max(0.0, float(archive_keepalive_s))
        if keepalive_s > 0.0:
            candidates.append(keepalive_s * 3.0)
        else:
            candidates.append(600.0)

    if not candidates:
        return 120.0
    return max(10.0, min(max(candidates), 7.0 * 24.0 * 3600.0))
