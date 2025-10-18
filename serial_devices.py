import asyncio
import logging
import serial_asyncio

logger = logging.getLogger("serial_devices")

class SerialProtocol(asyncio.Protocol):
    def __init__(self, name, queue, frame_parser):
        self.transport = None
        self.buf = bytearray()
        self.name = name
        self.queue = queue
        self.frame_parser = frame_parser

    def connection_made(self, transport):
        self.transport = transport
        logger.info("%s serial opened: %s", self.name, transport)
        try:
            ser = transport.serial
            ser.timeout = 0
        except Exception:
            pass

    def data_received(self, data):
        self.buf.extend(data)
        frames = self.frame_parser(self.buf)
        for frame in frames:
            asyncio.ensure_future(self.queue.put((self.name, frame)))

    def connection_lost(self, exc):
        logger.warning("%s serial closed: %s", self.name, exc)

async def open_serial(loop, device, baudrate, name, queue, frame_parser):
    while True:
        try:
            transport, protocol = await serial_asyncio.create_serial_connection(loop, lambda: SerialProtocol(name, queue, frame_parser), device, baudrate=baudrate)
            while not transport.is_closing():
                await asyncio.sleep(1)
        except Exception as e:
            logger.exception("Serial %s open error: %s", device, e)
        await asyncio.sleep(5)

def parse_line_frames(buf: bytearray):
    frames = []
    while b"\n" in buf:
        idx = buf.index(b"\n")
        raw = bytes(buf[:idx+1])
        frames.append(raw.decode(errors="ignore").strip())
        del buf[:idx+1]
    return frames
