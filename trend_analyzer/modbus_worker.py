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

            samples: dict[str, tuple[str, float]] = {}
            comm_error: tuple[SignalConfig, Exception] | None = None
            for signal in self.profile.signals:
                value: float | None = None
                read_exc: Exception | None = None
                for attempt in range(read_attempts):
                    try:
                        value = self._read_signal(
                            client,
                            signal,
                            self.profile.unit_id,
                            self.profile.address_offset,
                        )
                        read_exc = None
                        break
                    except Exception as exc:
                        read_exc = exc
                        if not ModbusWorker._is_connection_error(exc):
                            break
                        if attempt + 1 < read_attempts:
                            time.sleep(min(0.05 * (attempt + 1), 0.25))

                if read_exc is not None:
                    exc = read_exc
                    if ModbusWorker._is_connection_error(exc):
                        comm_error = (signal, exc)
                        break
                    address = signal.address + self.profile.address_offset
                    addr_text = f"{address}.{signal.bit_index}" if signal.data_type == "bool" else str(address)
                    self.error.emit(
                        f"{signal.name} addr={addr_text} {signal.data_type}/{signal.float_order}: {exc}"
                    )
                    continue

                if not is_connected:
                    is_connected = True
                    self.connection_changed.emit(True)
                samples[signal.id] = (signal.name, float(value))

            if comm_error is not None:
                signal, exc = comm_error
                address = signal.address + self.profile.address_offset
                addr_text = f"{address}.{signal.bit_index}" if signal.data_type == "bool" else str(address)
                self.error.emit(
                    f"{signal.name} addr={addr_text} {signal.data_type}/{signal.float_order}: {exc}"
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
