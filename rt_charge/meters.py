"""
Meter manager with batch (range) Modbus reads to reduce round-trips.

Usage:
    from rt_charge import MeterManager
    mgr = MeterManager()
    await mgr.start()
    vals = await mgr.read_meter(0)
    await mgr.stop()
"""

import asyncio
import logging
import importlib
from typing import Dict, Any, List, Tuple

from . import config as cfg  # 直接导入包内 config.py
from .modbus_poll import ModbusPoller

logger = logging.getLogger("rt_charge.meters")

DEFAULT_MEASURES = getattr(cfg, "DEFAULT_MEASURES", {})

def code_to_address(code: str) -> int:
    """把 code 的后 4 个 hex 字符转成 Modbus 地址 (int)"""
    try:
        return int(code[-4:], 16)
    except Exception:
        raise ValueError(f"Invalid code format: {code}")

def category_of_code(code: str) -> str:
    if code.startswith("02") and code[2:4] == "01":
        return "voltage"
    if code.startswith("02") and code[2:4] == "02":
        return "current"
    if code.startswith("00") and code[2:4] == "01":
        return "energy"
    return "other"

def _build_ranges(addresses: List[int]) -> List[Tuple[int, int]]:
    """
    Given a list of register addresses (ints), produce minimal list of continuous ranges.
    Output: list of (start_address, count)
    """
    if not addresses:
        return []
    a = sorted(set(addresses))
    ranges: List[Tuple[int, int]] = []
    start = a[0]
    prev = a[0]
    for addr in a[1:]:
        if addr == prev + 1:
            prev = addr
            continue
        # close current range
        ranges.append((start, prev - start + 1))
        start = addr
        prev = addr
    ranges.append((start, prev - start + 1))
    return ranges


class MeterManager:
    def __init__(self, config_module: str = "rt_charge.config", loop: asyncio.AbstractEventLoop = None):
        """
        config_module: Python module path (默认 rt_charge.config)
        """
        self.config_module = config_module
        self.loop = loop or asyncio.get_event_loop()
        self.config = None
        self.poller: ModbusPoller = None
        self._running = False

    def load_config(self):
        # 动态导入配置模块
        try:
            mod = importlib.import_module(self.config_module)
        except Exception as e:
            logger.exception("Failed to import config module %s: %s", self.config_module, e)
            raise
        # 读取配置对象
        self.config = {
            "modbus": getattr(mod, "MODBUS", {}),
            "guns": getattr(mod, "GUNS", 1),
            "meters": getattr(mod, "METERS", []),
            "measures": getattr(mod, "DEFAULT_MEASURES", {}),
            "poll_interval": getattr(mod, "POLLING_INTERVAL", 5),
        }

    async def start(self):
        self.load_config()
        modbus_cfg = self.config.get("modbus", {})
        port = modbus_cfg.get("port", "/dev/ttyUSB0")
        baud = modbus_cfg.get("baudrate", 19200)
        self.poller = ModbusPoller(port=port, baudrate=baud, loop=self.loop)
        self._running = True
        logger.info("MeterManager started with %d meters", len(self.config.get("meters", [])))

    async def stop(self):
        try:
            if self.poller and getattr(self.poller, "client", None):
                self.poller.client.close()
        except Exception:
            pass
        self._running = False

    async def _batch_read(self, unit_id: int, addrs: List[int]) -> Dict[int, Any]:
        """
        Batch read multiple addresses for a given unit_id.
        Returns a mapping address -> raw_value (int) or None if read failed/missing.
        """
        result_map: Dict[int, Any] = {addr: None for addr in addrs}
        if not addrs:
            return result_map

        ranges = _build_ranges(addrs)
        # Ensure poller uses the requested unit id
        self.poller.unit_id = unit_id

        for start, count in ranges:
            try:
                regs = await self.poller.read_registers(start, count)
                if regs is None:
                    logger.warning("Modbus batch read returned None for unit %s range %s:%s", unit_id, start, count)
                    # leave corresponding addresses as None
                    continue
                # regs is a list of length count
                for i in range(count):
                    addr = start + i
                    if addr in result_map:
                        # safety: check index bounds
                        try:
                            result_map[addr] = regs[i]
                        except Exception:
                            result_map[addr] = None
            except Exception as e:
                logger.exception("Exception during batch read unit %s range %s:%s -> %s", unit_id, start, count, e)
                # on exception, leave these addresses as None and continue
                continue

        return result_map

    async def read_meter(self, meter_index: int) -> Dict[str, Any]:
        """
        Unified entry point: read voltages, currents and energy for a given meter index.
        Returns a dict with keys: voltages (A,B,C), currents (A,B,C), energy, raw_registers
        """
        if not self._running:
            raise RuntimeError("MeterManager not started")
        meters = self.config.get("meters", [])
        if meter_index < 0 or meter_index >= len(meters):
            raise IndexError("meter_index out of range")
        meter_cfg = meters[meter_index]
        unit_id = int(meter_cfg.get("unit_id", 1))
        v_coeff = float(meter_cfg.get("voltage_coeff", 1.0))
        i_coeff = float(meter_cfg.get("current_coeff", 1.0))
        e_coeff = float(meter_cfg.get("energy_coeff", v_coeff * i_coeff))

        results = {"voltages": {}, "currents": {}, "energy": None, "raw": {}}
        measures = self.config.get("measures", DEFAULT_MEASURES)

        # build address lists and map code->address
        code_addr = {}
        addresses: List[int] = []
        for code, meta in measures.items():
            addr = code_to_address(code)
            code_addr[code] = addr
            addresses.append(addr)

        # batch read all required addresses for this meter's unit_id
        addr_values = await self._batch_read(unit_id=unit_id, addrs=addresses)

        # map results back to codes and apply scales/coeffs
        for code, meta in measures.items():
            addr = code_addr.get(code)
            raw = addr_values.get(addr)
            results["raw"][code] = raw
            if raw is None:
                continue
            value = raw * meta.get("scale", 1.0)
            cat = category_of_code(code)
            if cat == "voltage":
                actual = value * v_coeff
                if code.endswith("0100"):
                    results["voltages"]["A"] = actual
                elif code.endswith("0200"):
                    results["voltages"]["B"] = actual
                elif code.endswith("0300"):
                    results["voltages"]["C"] = actual
            elif cat == "current":
                actual = value * i_coeff
                if code.endswith("0100"):
                    results["currents"]["A"] = actual
                elif code.endswith("0200"):
                    results["currents"]["B"] = actual
                elif code.endswith("0300"):
                    results["currents"]["C"] = actual
            elif cat == "energy":
                actual = value * e_coeff
                results["energy"] = actual
            else:
                results["raw"][code + "_scaled"] = value

        return results


# CLI / demo helper
async def demo_read_all(config_module: str = "rt_charge.config"):
    mgr = MeterManager(config_module=config_module)
    await mgr.start()
    try:
        meters = mgr.config.get("meters", [])
        for idx in range(len(meters)):
            vals = await mgr.read_meter(idx)
            logger.info("Meter %s values: %s", idx, vals)
    finally:
        await mgr.stop()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(demo_read_all())
