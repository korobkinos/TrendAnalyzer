from __future__ import annotations

import unittest

try:
    from trend_analyzer.modbus_worker import ModbusWorker
except ModuleNotFoundError:
    ModbusWorker = None


@unittest.skipIf(ModbusWorker is None, "PySide6/pymodbus недоступны в текущем окружении")
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


if __name__ == "__main__":
    unittest.main()
