from __future__ import annotations

import unittest

try:
    from trend_analyzer.modbus_worker import ModbusWorker
    from trend_analyzer.models import SignalConfig
except ModuleNotFoundError:
    ModbusWorker = None
    SignalConfig = None


class _FakeResponse:
    def __init__(self, registers: list[int] | None = None, error: bool = False, message: str = "error") -> None:
        self.registers = list(registers or [])
        self._error = bool(error)
        self._message = str(message)

    def isError(self) -> bool:
        return self._error

    def __str__(self) -> str:
        return self._message


class _FakeClient:
    def __init__(self, holding: dict[int, int], fail_group_reads: bool = False) -> None:
        self._holding = dict(holding)
        self.fail_group_reads = bool(fail_group_reads)
        self.calls: list[tuple[str, int, int]] = []

    def read_holding_registers(
        self,
        address: int,
        count: int = 1,
        device_id: int | None = None,
        slave: int | None = None,
        unit: int | None = None,
    ) -> _FakeResponse:
        self.calls.append(("holding", int(address), int(count)))
        if self.fail_group_reads and int(count) > 1:
            return _FakeResponse(error=True, message="group_failed")
        regs = [int(self._holding.get(int(address) + i, 0)) for i in range(max(0, int(count)))]
        return _FakeResponse(registers=regs, error=False)

    def read_input_registers(
        self,
        address: int,
        count: int = 1,
        device_id: int | None = None,
        slave: int | None = None,
        unit: int | None = None,
    ) -> _FakeResponse:
        # Tests below read only holding registers.
        return self.read_holding_registers(address, count=count, device_id=device_id, slave=slave, unit=unit)


@unittest.skipIf(ModbusWorker is None or SignalConfig is None, "PySide6/pymodbus недоступны в текущем окружении")
class ModbusDecodeTests(unittest.TestCase):
    def test_decode_bool_bit(self) -> None:
        self.assertEqual(ModbusWorker._decode_registers("bool", [0b0000_0010], "ABCD", 1), 1.0)
        self.assertEqual(ModbusWorker._decode_registers("bool", [0b0000_0010], "ABCD", 0), 0.0)

    def test_decode_int16(self) -> None:
        self.assertEqual(ModbusWorker._decode_registers("int16", [0x000A], "ABCD", 0), 10.0)
        self.assertEqual(ModbusWorker._decode_registers("int16", [0xFFFF], "ABCD", 0), -1.0)

    def test_float32_word_orders_roundtrip(self) -> None:
        value = 12.5
        for order in ("ABCD", "BADC", "CDAB", "DCBA"):
            reg0, reg1 = ModbusWorker._encode_float32_words(value, order)
            decoded = ModbusWorker._decode_registers("float32", [reg0, reg1], order, 0)
            self.assertAlmostEqual(decoded, value, places=5)

    def test_grouped_read_merges_contiguous_addresses(self) -> None:
        f0, f1 = ModbusWorker._encode_float32_words(3.5, "ABCD")
        client = _FakeClient(
            {
                10: 111,
                11: 0b0010,
                12: f0,
                13: f1,
            }
        )
        signals = [
            SignalConfig(id="s1", name="S1", address=10, register_type="holding", data_type="int16", scale=1.0),
            SignalConfig(
                id="s2",
                name="S2",
                address=11,
                register_type="holding",
                data_type="bool",
                bit_index=1,
                scale=1.0,
            ),
            SignalConfig(
                id="s3",
                name="S3",
                address=12,
                register_type="holding",
                data_type="float32",
                float_order="ABCD",
                scale=1.0,
            ),
        ]

        specs = ModbusWorker._build_read_specs(signals, address_offset=0, default_scale=1.0)
        values, errors, comm_error = ModbusWorker._read_specs_grouped(client, specs, unit_id=1, read_attempts=1)

        self.assertIsNone(comm_error)
        self.assertEqual(errors, [])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0], ("holding", 10, 4))
        self.assertAlmostEqual(values["s1"][1], 111.0)
        self.assertAlmostEqual(values["s2"][1], 1.0)
        self.assertAlmostEqual(values["s3"][1], 3.5, places=4)

    def test_grouped_read_splits_blocks_by_125_words_limit(self) -> None:
        holding = {addr: addr for addr in range(126)}
        client = _FakeClient(holding)
        signals = [
            SignalConfig(
                id=f"s{addr}",
                name=f"S{addr}",
                address=addr,
                register_type="holding",
                data_type="int16",
                scale=1.0,
            )
            for addr in range(126)
        ]

        specs = ModbusWorker._build_read_specs(signals, address_offset=0, default_scale=1.0)
        values, errors, comm_error = ModbusWorker._read_specs_grouped(client, specs, unit_id=1, read_attempts=1)

        self.assertIsNone(comm_error)
        self.assertEqual(errors, [])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0], ("holding", 0, 125))
        self.assertEqual(client.calls[1], ("holding", 125, 1))
        self.assertEqual(len(values), 126)

    def test_grouped_read_falls_back_to_single_on_block_error(self) -> None:
        client = _FakeClient({20: 5, 21: 6}, fail_group_reads=True)
        signals = [
            SignalConfig(id="a", name="A", address=20, register_type="holding", data_type="int16", scale=1.0),
            SignalConfig(id="b", name="B", address=21, register_type="holding", data_type="int16", scale=1.0),
        ]

        specs = ModbusWorker._build_read_specs(signals, address_offset=0, default_scale=1.0)
        values, errors, comm_error = ModbusWorker._read_specs_grouped(client, specs, unit_id=1, read_attempts=1)

        self.assertIsNone(comm_error)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(values["a"][1], 5.0)
        self.assertAlmostEqual(values["b"][1], 6.0)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[0], ("holding", 20, 2))


if __name__ == "__main__":
    unittest.main()
