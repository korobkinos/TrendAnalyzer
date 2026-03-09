from __future__ import annotations


def min_column_width_for_table(table_key: str, col: int) -> int:
    if str(table_key) == "tags_table" and int(col) == 9:
        # "Статус" should be readable without manual widening every time.
        return 180
    return 1
