"""
Meter manager: unified entry point to read voltages, currents and energy from Modbus meters.
Assumptions:
- Default register codes provided in DEFAULT_MEASURES use their last 4 hex digits as Modbus register address.
- Voltage/current/energy coefficients are specified per-meter in config.yml.
- Actual voltage = read_value * measure_scale * voltage_coeff
- Actual current = read_value * measure_scale * current_coeff
- Actual energy = read_value * measure_scale * voltage_coeff * current_coeff

Usage:
  manager = MeterManager(config_path='config.yml')
  await manager.start()
  values = await manager.read_meter(0)  # read first meter
  await manager.stop()
"""

import asyncio
import logging
import yaml
from typing import Dict, Any
from modbus_poll import ModbusPoller

logger = logging.getLogger('meters')

# Default measure definitions (from your provided mapping snippet)
# Key is the device code, value contains name, scale and unit
DEFAULT_MEASURES = {
    "02010100": {"name": "A相电压", "scale": 0.1, "unit": "V"},
    "02010200": {"name": "B相电压", "scale": 0.1, "unit": "V"},
    "02010300": {"name": "C相电压", "scale": 0.1, "unit": "V"},
    "02020100": {"name": "A相电流", "scale": 0.001, "unit": "A"},
    "02020200": {"name": "B相电流", "scale": 0.001, "unit": "A"},
    "02020300": {"name": "C相电流", "scale": 0.001, "unit": "A"},
    "00010000": {"name": "总电能", "scale": 0.01, "unit": "kWh"},
}

# Helpers

def code_to_address(code: str) -> int:
    """Derive a Modbus register address from the last 4 hex digits of the code.
    Example: '02010100' -> address int('0100', 16) == 256
    """
    try:
        return int(code[-4:], 16)
    except Exception:
        raise ValueError(f"Invalid code format: {code}")


def category_of_code(code: str) -> str:
    """Very small heuristic to categorize code into 'voltage','current','energy'"""
    if code.startswith("02") and code[2:4] == "01":
        return 'voltage'
    if code.startswith("02") and code[2:4] == "02":
        return 'current'
    if code.startswith("00") and code[2:4] == "01":
        return 'energy'
    return 'other'


class MeterManager:
    def __init__(self, config_path: str = 'config.yml', loop: asyncio.AbstractEventLoop = None):
        self.config_path = config_path
        self.loop = loop or asyncio.get_event_loop()
        self.config: Dict[str, Any] = {}
        self.poller: ModbusPoller = None
        self._running = False

    def load_config(self):
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f) or {}
        # minimal validations and defaults
        if 'modbus' not in self.config:
            self.config['modbus'] = {'port': '/dev/ttyUSB0', 'baudrate': 19200}
        if 'meters' not in self.config:
            self.config['meters'] = []

    async def start(self):
        self.load_config()
        modbus_cfg = self.config.get('modbus', {})
        port = modbus_cfg.get('port', '/dev/ttyUSB0')
        baud = modbus_cfg.get('baudrate', 19200)
        # create a shared ModbusPoller used to talk to different unit ids (meters)
        self.poller = ModbusPoller(port=port, baudrate=baud, loop=self.loop)
        # no explicit connect here; ModbusPoller will connect on demand
        self._running = True

    async def stop(self):
        # close underlying resources if needed
        try:
            if self.poller and getattr(self.poller, 'client', None):
                self.poller.client.close()
        except Exception:
            pass
        self._running = False

    async def read_meter(self, meter_index: int) -> Dict[str, Any]:
        """Unified entry point: read voltages, currents and energy for a given meter index.
        Returns a dict with keys: voltages (A,B,C), currents (A,B,C), energy, raw_registers
        """
        if not self._running:
            raise RuntimeError('MeterManager not started')
        meters = self.config.get('meters', [])
        if meter_index < 0 or meter_index >= len(meters):
            raise IndexError('meter_index out of range')
        meter_cfg = meters[meter_index]
        unit_id = int(meter_cfg.get('unit_id', 1))
        v_coeff = float(meter_cfg.get('voltage_coeff', 1.0))
        i_coeff = float(meter_cfg.get('current_coeff', 1.0))
        e_coeff = float(meter_cfg.get('energy_coeff', v_coeff * i_coeff))

        results = {'voltages': {}, 'currents': {}, 'energy': None, 'raw': {}}

        # iterate through default measures; you can extend config to provide custom mapping per meter
        for code, meta in DEFAULT_MEASURES.items():
            addr = code_to_address(code)
            # set poller unit id to the meter's unit
            self.poller.unit_id = unit_id
            # read one register by default
            regs = await self.poller.read_registers(addr, 1)
            raw = None
            if regs:
                # regs is a list of register values
                raw = regs[0]
            results['raw'][code] = raw
            if raw is None:
                # leave missing
                continue
            value = raw * meta.get('scale', 1.0)
            cat = category_of_code(code)
            if cat == 'voltage':
                # apply voltage coefficient
                actual = value * v_coeff
                # map A/B/C by code tail if needed
                if code.endswith('0100'):
                    results['voltages']['A'] = actual
                elif code.endswith('0200'):
                    results['voltages']['B'] = actual
                elif code.endswith('0300'):
                    results['voltages']['C'] = actual
            elif cat == 'current':
                actual = value * i_coeff
                if code.endswith('0100'):
                    results['currents']['A'] = actual
                elif code.endswith('0200'):
                    results['currents']['B'] = actual
                elif code.endswith('0300'):
                    results['currents']['C'] = actual
            elif cat == 'energy':
                # energy applies both coeffs per your rule
                actual = value * v_coeff * i_coeff
                results['energy'] = actual
            else:
                # unknown category, attach as raw scaled
                results['raw'][code + '_scaled'] = value

        return results


# convenience helper for synchronous-style usage in scripts
async def demo_read_all(config_path: str = 'config.yml'):
    mgr = MeterManager(config_path=config_path)
    await mgr.start()
    try:
        meters = mgr.config.get('meters', [])
        for idx in range(len(meters)):
            vals = await mgr.read_meter(idx)
            logger.info('Meter %s values: %s', idx, vals)
    finally:
        await mgr.stop()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(demo_read_all())