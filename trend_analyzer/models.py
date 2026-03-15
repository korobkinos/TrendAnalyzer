from __future__ import annotations

from dataclasses import dataclass, field
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class SignalConfig:
    id: str = field(default_factory=_uuid)
    name: str = "Signal"
    address: int = 0
    register_type: str = "holding"  # holding | input
    data_type: str = "int16"  # int16 | uint16 | float32 | bool
    bit_index: int = 0
    axis_index: int = 1
    float_order: str = "ABCD"  # ABCD | BADC | CDAB | DCBA
    scale: float = 1.0
    unit: str = ""
    color: str = "#1f77b4"
    enabled: bool = True
    source_id: str = "local"
    remote_tag_id: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "register_type": self.register_type,
            "data_type": self.data_type,
            "bit_index": self.bit_index,
            "axis_index": self.axis_index,
            "float_order": self.float_order,
            "scale": self.scale,
            "unit": self.unit,
            "color": self.color,
            "enabled": self.enabled,
            "source_id": self.source_id,
            "remote_tag_id": self.remote_tag_id,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "SignalConfig":
        return cls(
            id=str(payload.get("id") or _uuid()),
            name=str(payload.get("name") or "Signal"),
            address=int(payload.get("address") or 0),
            register_type=str(payload.get("register_type") or "holding"),
            data_type=str(payload.get("data_type") or "int16"),
            bit_index=max(0, min(15, int(payload.get("bit_index") or 0))),
            axis_index=max(1, int(payload.get("axis_index") or 1)),
            float_order=str(payload.get("float_order") or "ABCD"),
            scale=float(payload.get("scale") or 1.0),
            unit=str(payload.get("unit") or ""),
            color=str(payload.get("color") or "#1f77b4"),
            enabled=bool(payload.get("enabled", True)),
            source_id=str(payload.get("source_id") or "local"),
            remote_tag_id=str(payload.get("remote_tag_id") or ""),
        )


@dataclass
class RecorderSourceConfig:
    id: str = field(default_factory=_uuid)
    name: str = "Recorder"
    source_kind: str = "remote_recorder"  # remote_recorder | modbus_tcp
    host: str = "127.0.0.1"
    port: int = 18777
    unit_id: int = 1
    timeout_s: float = 1.0
    retries: int = 1
    address_offset: int = 0
    token: str = ""
    enabled: bool = True
    recorder_id: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "source_kind": self.source_kind,
            "host": self.host,
            "port": self.port,
            "unit_id": self.unit_id,
            "timeout_s": self.timeout_s,
            "retries": self.retries,
            "address_offset": self.address_offset,
            "token": self.token,
            "enabled": self.enabled,
            "recorder_id": self.recorder_id,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "RecorderSourceConfig":
        source_kind_raw = str(payload.get("source_kind") or "").strip().lower()
        source_kind = source_kind_raw if source_kind_raw in {"remote_recorder", "modbus_tcp"} else "remote_recorder"
        default_port = 502 if source_kind == "modbus_tcp" else 18777
        try:
            port = int(payload.get("port") or default_port)
        except (TypeError, ValueError):
            port = default_port
        try:
            unit_id = int(payload.get("unit_id") or 1)
        except (TypeError, ValueError):
            unit_id = 1
        try:
            timeout_s = float(payload.get("timeout_s") or 1.0)
        except (TypeError, ValueError):
            timeout_s = 1.0
        try:
            retries = int(payload.get("retries") or 1)
        except (TypeError, ValueError):
            retries = 1
        try:
            address_offset = int(payload.get("address_offset") or 0)
        except (TypeError, ValueError):
            address_offset = 0
        return cls(
            id=str(payload.get("id") or _uuid()),
            name=str(payload.get("name") or "Recorder"),
            source_kind=source_kind,
            host=str(payload.get("host") or "127.0.0.1"),
            port=max(1, min(65535, port)),
            unit_id=max(1, min(247, unit_id)),
            timeout_s=max(0.1, timeout_s),
            retries=max(0, retries),
            address_offset=int(address_offset),
            token=str(payload.get("token") or ""),
            enabled=bool(payload.get("enabled", True)),
            recorder_id=str(payload.get("recorder_id") or ""),
        )


@dataclass
class TagConfig:
    id: str = field(default_factory=_uuid)
    name: str = "Tag"
    address: int = 0
    register_type: str = "holding"  # holding | input
    data_type: str = "int16"  # int16 | uint16 | float32 | bool
    bit_index: int = 0
    float_order: str = "ABCD"  # ABCD | BADC | CDAB | DCBA
    read_enabled: bool = True
    value: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "address": self.address,
            "register_type": self.register_type,
            "data_type": self.data_type,
            "bit_index": self.bit_index,
            "float_order": self.float_order,
            "read_enabled": self.read_enabled,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TagConfig":
        return cls(
            id=str(payload.get("id") or _uuid()),
            name=str(payload.get("name") or "Tag"),
            address=max(0, int(payload.get("address") or 0)),
            register_type=str(payload.get("register_type") or "holding"),
            data_type=str(payload.get("data_type") or "int16"),
            bit_index=max(0, min(15, int(payload.get("bit_index") or 0))),
            float_order=str(payload.get("float_order") or "ABCD"),
            read_enabled=bool(payload.get("read_enabled", True)),
            value=float(payload.get("value") or 0.0),
        )


@dataclass
class TagTabConfig:
    id: str = field(default_factory=_uuid)
    name: str = "Вкладка 1"
    tags: list[TagConfig] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tags": [item.to_dict() for item in self.tags],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TagTabConfig":
        tags_raw = payload.get("tags") or []
        tags = [TagConfig.from_dict(item) for item in tags_raw if isinstance(item, dict)]
        return cls(
            id=str(payload.get("id") or _uuid()),
            name=str(payload.get("name") or "Вкладка"),
            tags=tags,
        )


@dataclass
class ProfileConfig:
    id: str = field(default_factory=_uuid)
    name: str = "Default"
    ip: str = "127.0.0.1"
    port: int = 502
    unit_id: int = 1
    poll_interval_ms: int = 500
    render_interval_ms: int = 200
    render_chart_enabled: bool = True
    archive_interval_ms: int = 1000
    archive_on_change_only: bool = False
    archive_deadband: float = 0.0
    archive_keepalive_s: int = 60
    archive_retention_days: int = 7  # 0 = unlimited
    archive_retention_mode: str = "days"  # days | size
    archive_max_size_value: int = 1024
    archive_max_size_unit: str = "MB"  # MB | GB
    archive_to_db: bool = True
    work_mode: str = "online"  # online | offline
    timeout_s: float = 1.0
    retries: int = 1
    address_offset: int = 0
    plot_background_color: str = "#000000"
    plot_grid_color: str = "#2f4f6f"
    plot_grid_alpha: int = 25
    plot_grid_x: bool = True
    plot_grid_y: bool = True
    plot_smoothing_enabled: bool = False
    plot_smoothing_window: int = 5
    ui_theme_preset: str = "dark"
    tags_bulk_start_address: int = 0
    tags_bulk_count: int = 10
    tags_bulk_step: int = 1
    tags_bulk_register_type: str = "holding"
    tags_bulk_data_type: str = "int16"
    tags_bulk_float_order: str = "ABCD"
    tags_poll_interval_ms: int = 1000
    recorder_api_enabled: bool = True
    recorder_api_host: str = "0.0.0.0"
    recorder_api_port: int = 18777
    recorder_api_token: str = ""
    db_path: str = ""
    signals: list[SignalConfig] = field(default_factory=list)
    tags: list[TagConfig] = field(default_factory=list)
    tag_tabs: list[TagTabConfig] = field(default_factory=lambda: [TagTabConfig(name="Вкладка 1", tags=[])])
    recorder_sources: list[RecorderSourceConfig] = field(default_factory=list)
    ui_state: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "unit_id": self.unit_id,
            "poll_interval_ms": self.poll_interval_ms,
            "render_interval_ms": self.render_interval_ms,
            "render_chart_enabled": self.render_chart_enabled,
            "archive_interval_ms": self.archive_interval_ms,
            "archive_on_change_only": self.archive_on_change_only,
            "archive_deadband": self.archive_deadband,
            "archive_keepalive_s": self.archive_keepalive_s,
            "archive_retention_days": self.archive_retention_days,
            "archive_retention_mode": self.archive_retention_mode,
            "archive_max_size_value": self.archive_max_size_value,
            "archive_max_size_unit": self.archive_max_size_unit,
            "archive_to_db": self.archive_to_db,
            "work_mode": self.work_mode,
            "timeout_s": self.timeout_s,
            "retries": self.retries,
            "address_offset": self.address_offset,
            "plot_background_color": self.plot_background_color,
            "plot_grid_color": self.plot_grid_color,
            "plot_grid_alpha": self.plot_grid_alpha,
            "plot_grid_x": self.plot_grid_x,
            "plot_grid_y": self.plot_grid_y,
            "plot_smoothing_enabled": self.plot_smoothing_enabled,
            "plot_smoothing_window": self.plot_smoothing_window,
            "ui_theme_preset": self.ui_theme_preset,
            "tags_bulk_start_address": self.tags_bulk_start_address,
            "tags_bulk_count": self.tags_bulk_count,
            "tags_bulk_step": self.tags_bulk_step,
            "tags_bulk_register_type": self.tags_bulk_register_type,
            "tags_bulk_data_type": self.tags_bulk_data_type,
            "tags_bulk_float_order": self.tags_bulk_float_order,
            "tags_poll_interval_ms": self.tags_poll_interval_ms,
            "recorder_api_enabled": self.recorder_api_enabled,
            "recorder_api_host": self.recorder_api_host,
            "recorder_api_port": self.recorder_api_port,
            "recorder_api_token": self.recorder_api_token,
            "db_path": self.db_path,
            "signals": [item.to_dict() for item in self.signals],
            "tags": [item.to_dict() for item in self.tags],
            "tag_tabs": [item.to_dict() for item in self.tag_tabs],
            "recorder_sources": [item.to_dict() for item in self.recorder_sources],
            "ui_state": self.ui_state,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ProfileConfig":
        signals_raw = payload.get("signals")
        signals = [SignalConfig.from_dict(item) for item in signals_raw] if isinstance(signals_raw, list) else []
        tags_raw = payload.get("tags") or []
        tags = [TagConfig.from_dict(item) for item in tags_raw]
        tag_tabs_raw = payload.get("tag_tabs") or []
        tag_tabs = [TagTabConfig.from_dict(item) for item in tag_tabs_raw if isinstance(item, dict)]
        recorder_sources_raw = payload.get("recorder_sources") or []
        recorder_sources = [
            RecorderSourceConfig.from_dict(item) for item in recorder_sources_raw if isinstance(item, dict)
        ]
        if not tag_tabs:
            tag_tabs = [TagTabConfig(name="Вкладка 1", tags=tags)]
        if not tag_tabs:
            tag_tabs = [TagTabConfig(name="Вкладка 1", tags=[])]
        legacy_tags = [TagConfig.from_dict(item.to_dict()) for item in tag_tabs[0].tags] if tag_tabs else tags
        if signals_raw is None and not signals:
            signals = [SignalConfig(name="Signal 1", address=0)]
        work_mode = str(payload.get("work_mode") or "online")
        if work_mode not in {"online", "offline"}:
            work_mode = "online"
        try:
            recorder_api_port = int(payload.get("recorder_api_port") or 18777)
        except (TypeError, ValueError):
            recorder_api_port = 18777
        archive_retention_mode = str(payload.get("archive_retention_mode") or "days").strip().lower()
        if archive_retention_mode not in {"days", "size"}:
            archive_retention_mode = "days"
        try:
            archive_max_size_value = int(payload.get("archive_max_size_value", 1024))
        except (TypeError, ValueError):
            archive_max_size_value = 1024
        archive_max_size_value = max(1, min(1024 * 1024, archive_max_size_value))
        archive_max_size_unit = str(payload.get("archive_max_size_unit") or "MB").strip().upper()
        if archive_max_size_unit not in {"MB", "GB"}:
            archive_max_size_unit = "MB"
        try:
            plot_smoothing_window = int(payload.get("plot_smoothing_window") or 5)
        except (TypeError, ValueError):
            plot_smoothing_window = 5
        plot_smoothing_window = max(3, min(31, plot_smoothing_window))
        if plot_smoothing_window % 2 == 0:
            plot_smoothing_window = plot_smoothing_window + 1 if plot_smoothing_window < 31 else plot_smoothing_window - 1
        ui_theme_preset = str(payload.get("ui_theme_preset") or "dark").strip() or "dark"

        return cls(
            id=str(payload.get("id") or _uuid()),
            name=str(payload.get("name") or "Profile"),
            ip=str(payload.get("ip") or "127.0.0.1"),
            port=int(payload.get("port") or 502),
            unit_id=int(payload.get("unit_id") or 1),
            poll_interval_ms=max(50, int(payload.get("poll_interval_ms") or 500)),
            render_interval_ms=max(50, int(payload.get("render_interval_ms") or 200)),
            render_chart_enabled=bool(payload.get("render_chart_enabled", True)),
            archive_interval_ms=max(50, int(payload.get("poll_interval_ms") or 500)),  # always equal to poll
            archive_on_change_only=bool(payload.get("archive_on_change_only", False)),
            archive_deadband=max(0.0, float(payload.get("archive_deadband", 0.0))),
            archive_keepalive_s=max(0, int(payload.get("archive_keepalive_s", 60))),
            archive_retention_days=max(0, int(payload.get("archive_retention_days", 7))),
            archive_retention_mode=archive_retention_mode,
            archive_max_size_value=archive_max_size_value,
            archive_max_size_unit=archive_max_size_unit,
            archive_to_db=bool(payload.get("archive_to_db", True)),
            work_mode=work_mode,
            timeout_s=max(0.1, float(payload.get("timeout_s") or 1.0)),
            retries=max(0, int(payload.get("retries") or 1)),
            address_offset=int(payload.get("address_offset") or 0),
            plot_background_color=str(payload.get("plot_background_color") or "#000000"),
            plot_grid_color=str(payload.get("plot_grid_color") or "#2f4f6f"),
            plot_grid_alpha=max(0, min(100, int(payload.get("plot_grid_alpha", 25)))),
            plot_grid_x=bool(payload.get("plot_grid_x", True)),
            plot_grid_y=bool(payload.get("plot_grid_y", True)),
            plot_smoothing_enabled=bool(payload.get("plot_smoothing_enabled", False)),
            plot_smoothing_window=plot_smoothing_window,
            ui_theme_preset=ui_theme_preset,
            tags_bulk_start_address=max(0, int(payload.get("tags_bulk_start_address") or 0)),
            tags_bulk_count=max(1, int(payload.get("tags_bulk_count") or 10)),
            tags_bulk_step=max(1, int(payload.get("tags_bulk_step") or 1)),
            tags_bulk_register_type=str(payload.get("tags_bulk_register_type") or "holding"),
            tags_bulk_data_type=str(payload.get("tags_bulk_data_type") or "int16"),
            tags_bulk_float_order=str(payload.get("tags_bulk_float_order") or "ABCD"),
            tags_poll_interval_ms=max(100, int(payload.get("tags_poll_interval_ms") or 1000)),
            recorder_api_enabled=bool(payload.get("recorder_api_enabled", True)),
            recorder_api_host=str(payload.get("recorder_api_host") or "0.0.0.0"),
            recorder_api_port=max(1, min(65535, recorder_api_port)),
            recorder_api_token=str(payload.get("recorder_api_token") or ""),
            db_path=str(payload.get("db_path") or ""),
            signals=signals,
            tags=legacy_tags,
            tag_tabs=tag_tabs,
            recorder_sources=recorder_sources,
            ui_state=payload.get("ui_state") if isinstance(payload.get("ui_state"), dict) else {},
        )


@dataclass
class AppConfig:
    profiles: list[ProfileConfig]
    active_profile_id: str
    close_behavior: str = "ask"  # ask | tray | exit
    auto_start_windows: bool = False
    auto_connect_on_launch: bool = False

    def to_dict(self) -> dict:
        return {
            "profiles": [item.to_dict() for item in self.profiles],
            "active_profile_id": self.active_profile_id,
            "close_behavior": self.close_behavior,
            "auto_start_windows": self.auto_start_windows,
            "auto_connect_on_launch": self.auto_connect_on_launch,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "AppConfig":
        profiles_raw = payload.get("profiles") or []
        profiles = [ProfileConfig.from_dict(item) for item in profiles_raw]

        if not profiles:
            profiles = [
                ProfileConfig(
                    name="Default",
                    signals=[SignalConfig(name="Signal 1", address=0)],
                )
            ]

        active_profile_id = str(payload.get("active_profile_id") or profiles[0].id)
        profile_ids = {item.id for item in profiles}
        if active_profile_id not in profile_ids:
            active_profile_id = profiles[0].id

        close_behavior = str(payload.get("close_behavior") or "ask")
        if close_behavior not in {"ask", "tray", "exit"}:
            close_behavior = "ask"

        return cls(
            profiles=profiles,
            active_profile_id=active_profile_id,
            close_behavior=close_behavior,
            auto_start_windows=bool(payload.get("auto_start_windows", False)),
            auto_connect_on_launch=bool(payload.get("auto_connect_on_launch", False)),
        )