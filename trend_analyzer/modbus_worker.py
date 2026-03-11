from __future__ import annotations

import inspect
import struct
import time
from typing import Any

from PySide6.QtCore import QThread, Signal
from pymodbus.client import ModbusTcpClient

from .models import ProfileConfig, SignalConfig


class ModbusWorker(QThread):
    samples_ready = Signal(float, object)  # ts, dict[signal_id, tuple[name, value]]
    connection_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, profile: ProfileConfig):
        super().__init__()
        self.profile = profile
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        client = ModbusTcpClient(
            host=self.profile.ip,
            port=self.profile.port,
            timeout=self.profile.timeout_s,
        )

        is_connected = False
        reconnect_delay_s = 0.5
        reconnect_delay_max_s = 15.0
        read_attempts = max(1, int(self.profile.retries) + 1)
        while self._running:
            started = time.monotonic()

            if not client.connected:
                try:
                    connected_now = bool(client.connect())
                except Exception as exc:
                    connected_now = False
                    self.error.emit(f"Ошибка подключения: {exc}")

                if connected_now != is_connected:
                    is_connected = connected_now
                    self.connection_changed.emit(is_connected)
                if not connected_now:
                    time.sleep(reconnect_delay_s)
                    reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)
                    continue
                reconnect_delay_s = 0.5

            specs = ModbusWorker._build_read_specs(
                self.profile.signals,
                address_offset=self.profile.address_offset,
                default_scale=1.0,
            )
            samples, read_errors, comm_error = ModbusWorker._read_specs_grouped(
                client,
                specs,
                self.profile.unit_id,
                read_attempts=read_attempts,
            )
            if not is_connected:
                is_connected = True
                self.connection_changed.emit(True)
            for spec, exc in read_errors:
                address = int(spec.get("address", 0))
                data_type = str(spec.get("data_type", "int16"))
                bit_index = int(spec.get("bit_index", 0))
                addr_text = f"{address}.{bit_index}" if data_type == "bool" else str(address)
                self.error.emit(
                    f"{spec.get('name', '?')} addr={addr_text} {data_type}/{spec.get('float_order', 'ABCD')}: {exc}"
                )

            if comm_error is not None:
                spec, exc = comm_error
                address = int(spec.get("address", 0))
                data_type = str(spec.get("data_type", "int16"))
                bit_index = int(spec.get("bit_index", 0))
                addr_text = f"{address}.{bit_index}" if data_type == "bool" else str(address)
                self.error.emit(
                    f"{spec.get('name', '?')} addr={addr_text} {data_type}/{spec.get('float_order', 'ABCD')}: {exc}"
                )
                try:
                    client.close()
                except Exception:
                    pass
                if is_connected:
                    is_connected = False
                    self.connection_changed.emit(False)
                time.sleep(reconnect_delay_s)
                reconnect_delay_s = min(reconnect_delay_max_s, reconnect_delay_s * 2.0)
                continue
            reconnect_delay_s = 0.5

            ts = time.time()
            if samples:
                self.samples_ready.emit(ts, samples)

            elapsed = (time.monotonic() - started) * 1000.0
            sleep_ms = max(0, self.profile.poll_interval_ms - int(elapsed))
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)

        try:
            client.close()
        finally:
            if is_connected:
                self.connection_changed.emit(False)

    @staticmethod
    def _read_signal(
        client: ModbusTcpClient,
        signal: SignalConfig,
        unit_id: int,
        address_offset: int,
    ) -> float:
        count = 2 if signal.data_type == "float32" else 1
        address = max(0, signal.address + address_offset)

        if signal.register_type == "input":
            response = ModbusWorker._read_input_registers(client, address, count, unit_id)
        else:
            response = ModbusWorker._read_holding_registers(client, address, count, unit_id)

        if response.isError():
            raise RuntimeError(str(response))

        registers = list(response.registers)
        value = ModbusWorker._decode_registers(signal.data_type, registers, signal.float_order, signal.bit_index)
        return value * signal.scale

    @staticmethod
    def _normalize_register_type(register_type: str) -> str:
        return "input" if str(register_type or "").lower() == "input" else "holding"

    @staticmethod
    def _signal_word_count(data_type: str) -> int:
        return 2 if str(data_type or "").lower() == "float32" else 1

    @staticmethod
    def _build_read_specs(signals: list[Any], address_offset: int = 0, default_scale: float = 1.0) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for index, signal in enumerate(signals):
            try:
                signal_id = str(getattr(signal, "id", "") or f"sig_{index}")
                name = str(getattr(signal, "name", "") or signal_id)
                register_type = ModbusWorker._normalize_register_type(str(getattr(signal, "register_type", "holding")))
                data_type = str(getattr(signal, "data_type", "int16") or "int16").lower()
                if data_type not in {"int16", "uint16", "float32", "bool"}:
                    data_type = "int16"
                float_order = str(getattr(signal, "float_order", "ABCD") or "ABCD")
                bit_index = max(0, min(15, int(getattr(signal, "bit_index", 0) or 0)))
                scale = float(getattr(signal, "scale", default_scale) or default_scale)
                address = max(0, int(getattr(signal, "address", 0) or 0) + int(address_offset))
            except Exception:
                continue
            word_count = ModbusWorker._signal_word_count(data_type)
            specs.append(
                {
                    "id": signal_id,
                    "name": name,
                    "register_type": register_type,
                    "data_type": data_type,
                    "float_order": float_order,
                    "bit_index": bit_index,
                    "scale": scale,
                    "address": address,
                    "word_count": int(word_count),
                    "end_address": int(address + word_count - 1),
                }
            )
        return specs

    @staticmethod
    def _build_read_blocks(specs: list[dict[str, Any]], max_words: int = 125) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for register_type in ("holding", "input"):
            type_specs = sorted(
                [spec for spec in specs if str(spec.get("register_type")) == register_type],
                key=lambda item: (int(item.get("address", 0)), int(item.get("word_count", 1))),
            )
            if not type_specs:
                continue
            current_specs: list[dict[str, Any]] = []
            block_start = 0
            block_end = -1
            for spec in type_specs:
                start = int(spec.get("address", 0))
                end = int(spec.get("end_address", start))
                if not current_specs:
                    current_specs = [spec]
                    block_start = start
                    block_end = end
                    continue
                new_end = max(block_end, end)
                fits = (new_end - block_start + 1) <= int(max_words)
                contiguous = start <= (block_end + 1)
                if contiguous and fits:
                    current_specs.append(spec)
                    block_end = new_end
                else:
                    blocks.append(
                        {
                            "register_type": register_type,
                            "start": int(block_start),
                            "count": int(block_end - block_start + 1),
                            "specs": current_specs,
                        }
                    )
                    current_specs = [spec]
                    block_start = start
                    block_end = end
            if current_specs:
                blocks.append(
                    {
                        "register_type": register_type,
                        "start": int(block_start),
                        "count": int(block_end - block_start + 1),
                        "specs": current_specs,
                    }
                )
        return blocks

    @staticmethod
    def _read_block_registers(
        client: ModbusTcpClient,
        register_type: str,
        start: int,
        count: int,
        unit_id: int,
    ) -> list[int]:
        if register_type == "input":
            response = ModbusWorker._read_input_registers(client, start, count, unit_id)
        else:
            response = ModbusWorker._read_holding_registers(client, start, count, unit_id)
        if response.isError():
            raise RuntimeError(str(response))
        return [int(value) for value in list(getattr(response, "registers", []) or [])]

    @staticmethod
    def _read_specs_grouped(
        client: ModbusTcpClient,
        specs: list[dict[str, Any]],
        unit_id: int,
        read_attempts: int = 1,
    ) -> tuple[
        dict[str, tuple[str, float]],
        list[tuple[dict[str, Any], Exception]],
        tuple[dict[str, Any], Exception] | None,
    ]:
        values: dict[str, tuple[str, float]] = {}
        errors: list[tuple[dict[str, Any], Exception]] = []
        if not specs:
            return values, errors, None

        for block in ModbusWorker._build_read_blocks(specs):
            block_values, block_errors, comm_error = ModbusWorker._read_block_with_fallback(
                client,
                block,
                unit_id,
                max(1, int(read_attempts)),
            )
            values.update(block_values)
            errors.extend(block_errors)
            if comm_error is not None:
                return values, errors, comm_error
        return values, errors, None

    @staticmethod
    def _read_block_with_fallback(
        client: ModbusTcpClient,
        block: dict[str, Any],
        unit_id: int,
        read_attempts: int,
    ) -> tuple[
        dict[str, tuple[str, float]],
        list[tuple[dict[str, Any], Exception]],
        tuple[dict[str, Any], Exception] | None,
    ]:
        specs = list(block.get("specs") or [])
        if not specs:
            return {}, [], None

        start = int(block.get("start", 0))
        count = max(1, int(block.get("count", 1)))
        register_type = ModbusWorker._normalize_register_type(str(block.get("register_type", "holding")))

        registers: list[int] = []
        last_exc: Exception | None = None
        for attempt in range(max(1, int(read_attempts))):
            try:
                registers = ModbusWorker._read_block_registers(client, register_type, start, count, unit_id)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if not ModbusWorker._is_connection_error(exc):
                    break
                if attempt + 1 < read_attempts:
                    time.sleep(min(0.05 * (attempt + 1), 0.25))

        if last_exc is not None:
            if ModbusWorker._is_connection_error(last_exc):
                return {}, [], (specs[0], last_exc)
            if len(specs) > 1:
                merged_values: dict[str, tuple[str, float]] = {}
                merged_errors: list[tuple[dict[str, Any], Exception]] = []
                for spec in specs:
                    single_block = {
                        "register_type": str(spec.get("register_type", register_type)),
                        "start": int(spec.get("address", 0)),
                        "count": int(spec.get("word_count", 1)),
                        "specs": [spec],
                    }
                    values, errors, comm_error = ModbusWorker._read_block_with_fallback(
                        client,
                        single_block,
                        unit_id,
                        read_attempts,
                    )
                    merged_values.update(values)
                    merged_errors.extend(errors)
                    if comm_error is not None:
                        return merged_values, merged_errors, comm_error
                return merged_values, merged_errors, None
            return {}, [(specs[0], last_exc)], None

        values: dict[str, tuple[str, float]] = {}
        errors: list[tuple[dict[str, Any], Exception]] = []
        for spec in specs:
            try:
                spec_address = int(spec.get("address", 0))
                word_count = max(1, int(spec.get("word_count", 1)))
                left = spec_address - start
                right = left + word_count
                if left < 0 or right > len(registers):
                    raise RuntimeError(
                        f"Недостаточно регистров в ответе блока ({len(registers)}), требуется {word_count}"
                    )
                chunk = registers[left:right]
                decoded = ModbusWorker._decode_registers(
                    str(spec.get("data_type", "int16")),
                    chunk,
                    str(spec.get("float_order", "ABCD")),
                    int(spec.get("bit_index", 0)),
                )
                value = float(decoded) * float(spec.get("scale", 1.0))
                values[str(spec.get("id", ""))] = (str(spec.get("name", "")), value)
            except Exception as exc:
                errors.append((spec, exc))
        return values, errors, None

    @staticmethod
    def _decode_registers(data_type: str, registers: list[int], float_order: str, bit_index: int) -> float:
        if data_type == "bool":
            bit = max(0, min(15, int(bit_index)))
            raw = int(registers[0]) if registers else 0
            return 1.0 if ((raw >> bit) & 0x01) else 0.0

        if data_type == "uint16":
            return float(registers[0])

        if data_type == "float32":
            if len(registers) < 2:
                raise RuntimeError("Для float32 требуется 2 регистра")
            raw = ModbusWorker._pack_float32(registers[0], registers[1], float_order)
            return float(struct.unpack(">f", raw)[0])

        raw = registers[0]
        if raw >= 32768:
            raw -= 65536
        return float(raw)

    @staticmethod
    def _pack_float32(reg0: int, reg1: int, float_order: str) -> bytes:
        # reg0/reg1 come from Modbus words; order controls byte/word permutation.
        if float_order == "BADC":
            b0 = reg0.to_bytes(2, "big")
            b1 = reg1.to_bytes(2, "big")
            return bytes([b0[1], b0[0], b1[1], b1[0]])
        if float_order == "CDAB":
            return struct.pack(">HH", reg1, reg0)
        if float_order == "DCBA":
            b0 = reg0.to_bytes(2, "big")
            b1 = reg1.to_bytes(2, "big")
            return bytes([b1[1], b1[0], b0[1], b0[0]])
        return struct.pack(">HH", reg0, reg1)

    @staticmethod
    def _encode_float32_words(value: float, float_order: str) -> tuple[int, int]:
        raw = struct.pack(">f", float(value))
        a, b, c, d = raw[0], raw[1], raw[2], raw[3]
        if float_order == "BADC":
            return int.from_bytes(bytes([b, a]), "big"), int.from_bytes(bytes([d, c]), "big")
        if float_order == "CDAB":
            return int.from_bytes(bytes([c, d]), "big"), int.from_bytes(bytes([a, b]), "big")
        if float_order == "DCBA":
            return int.from_bytes(bytes([d, c]), "big"), int.from_bytes(bytes([b, a]), "big")
        return int.from_bytes(bytes([a, b]), "big"), int.from_bytes(bytes([c, d]), "big")

    @staticmethod
    def _read_holding_registers(client: ModbusTcpClient, address: int, count: int, unit_id: int) -> Any:
        return ModbusWorker._read_registers_compat(client.read_holding_registers, address, count, unit_id)

    @staticmethod
    def _read_input_registers(client: ModbusTcpClient, address: int, count: int, unit_id: int) -> Any:
        return ModbusWorker._read_registers_compat(client.read_input_registers, address, count, unit_id)

    @staticmethod
    def _write_single_register(client: ModbusTcpClient, address: int, value: int, unit_id: int) -> Any:
        return ModbusWorker._write_register_compat(client.write_register, address, value, unit_id)

    @staticmethod
    def _write_multiple_registers(client: ModbusTcpClient, address: int, values: list[int], unit_id: int) -> Any:
        return ModbusWorker._write_registers_compat(client.write_registers, address, values, unit_id)

    @staticmethod
    def _read_registers_compat(method, address: int, count: int, unit_id: int) -> Any:
        # pymodbus uses different unit parameter names across versions:
        # device_id (new), slave, unit (older).
        try:
            params = inspect.signature(method).parameters
            kwargs: dict[str, Any] = {}
            if "count" in params:
                kwargs["count"] = count
            if "device_id" in params:
                kwargs["device_id"] = unit_id
            elif "slave" in params:
                kwargs["slave"] = unit_id
            elif "unit" in params:
                kwargs["unit"] = unit_id
            return method(address, **kwargs)
        except (TypeError, ValueError):
            pass

        attempts = (
            {"count": count, "device_id": unit_id},
            {"count": count, "slave": unit_id},
            {"count": count, "unit": unit_id},
            {"count": count},
        )
        for kwargs in attempts:
            try:
                return method(address, **kwargs)
            except TypeError:
                continue

        positional_attempts = (
            (address, count, unit_id),
            (address, count),
        )
        for args in positional_attempts:
            try:
                return method(*args)
            except TypeError:
                continue

        raise RuntimeError("Не удалось вызвать read_*_registers: несовместимая версия pymodbus")

    @staticmethod
    def _write_register_compat(method, address: int, value: int, unit_id: int) -> Any:
        try:
            params = inspect.signature(method).parameters
            kwargs: dict[str, Any] = {}
            if "value" in params:
                kwargs["value"] = int(value)
            if "device_id" in params:
                kwargs["device_id"] = unit_id
            elif "slave" in params:
                kwargs["slave"] = unit_id
            elif "unit" in params:
                kwargs["unit"] = unit_id
            return method(address, **kwargs)
        except (TypeError, ValueError):
            pass

        attempts = (
            {"value": int(value), "device_id": unit_id},
            {"value": int(value), "slave": unit_id},
            {"value": int(value), "unit": unit_id},
            {"value": int(value)},
        )
        for kwargs in attempts:
            try:
                return method(address, **kwargs)
            except TypeError:
                continue

        positional_attempts = (
            (address, int(value), unit_id),
            (address, int(value)),
        )
        for args in positional_attempts:
            try:
                return method(*args)
            except TypeError:
                continue

        raise RuntimeError("Не удалось вызвать write_register: несовместимая версия pymodbus")

    @staticmethod
    def _write_registers_compat(method, address: int, values: list[int], unit_id: int) -> Any:
        vals = [int(v) & 0xFFFF for v in values]
        try:
            params = inspect.signature(method).parameters
            kwargs: dict[str, Any] = {}
            if "values" in params:
                kwargs["values"] = vals
            elif "registers" in params:
                kwargs["registers"] = vals
            if "device_id" in params:
                kwargs["device_id"] = unit_id
            elif "slave" in params:
                kwargs["slave"] = unit_id
            elif "unit" in params:
                kwargs["unit"] = unit_id
            return method(address, **kwargs)
        except (TypeError, ValueError):
            pass

        attempts = (
            {"values": vals, "device_id": unit_id},
            {"values": vals, "slave": unit_id},
            {"values": vals, "unit": unit_id},
            {"values": vals},
            {"registers": vals, "device_id": unit_id},
            {"registers": vals, "slave": unit_id},
            {"registers": vals, "unit": unit_id},
            {"registers": vals},
        )
        for kwargs in attempts:
            try:
                return method(address, **kwargs)
            except TypeError:
                continue

        positional_attempts = (
            (address, vals, unit_id),
            (address, vals),
        )
        for args in positional_attempts:
            try:
                return method(*args)
            except TypeError:
                continue

        raise RuntimeError("Не удалось вызвать write_registers: несовместимая версия pymodbus")

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        if isinstance(exc, OSError):
            return True
        text = str(exc).lower()
        cls_name = exc.__class__.__name__.lower()
        markers = (
            "connection",
            "timeout",
            "timed out",
            "disconnected",
            "broken pipe",
            "forcibly closed",
            "reset by peer",
            "winerror 10054",
            "modbusioexception",
            "connectionexception",
        )
        return any(marker in text or marker in cls_name for marker in markers)
