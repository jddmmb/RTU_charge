import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pymodbus.client.sync import ModbusSerialClient

logger = logging.getLogger("modbus_poll")

class ModbusPoller:
    def __init__(self, port="/dev/ttyUSB0", baudrate=19200, unit_id=1, loop=None):
        self.port = port
        self.baudrate = baudrate
        self.unit_id = unit_id
        self.loop = loop or asyncio.get_event_loop()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.client = None

    def _connect_sync(self):
        client = ModbusSerialClient(method="rtu", port=self.port, baudrate=self.baudrate, timeout=1, parity='N', stopbits=1)
        if not client.connect():
            raise ConnectionError("Modbus serial connect failed")
        return client

    def _read_sync(self, address, count):
        if self.client is None:
            self.client = self._connect_sync()
        result = self.client.read_holding_registers(address, count, unit=self.unit_id)
        return result

    async def read_registers(self, address, count):
        try:
            result = await self.loop.run_in_executor(self.executor, self._read_sync, address, count)
            if hasattr(result, "isError") and result.isError():
                logger.warning("Modbus error reading %s:%s", address, result)
                return None
            return getattr(result, "registers", None)
        except Exception as e:
            logger.exception("Modbus read error: %s", e)
            try:
                if self.client:
                    self.client.close()
            finally:
                self.client = None
            return None

    async def poll_loop(self, out_queue: asyncio.Queue, interval=5):
        while True:
            regs = await self.read_registers(0, 4)  # 举例读取 4 寄存器
            if regs is not None:
                payload = {"unit": self.unit_id, "registers": regs}
                await out_queue.put(("meter", payload))
            await asyncio.sleep(interval)
