import asyncio
import logging

logger = logging.getLogger("tcp_operator")

class TCPClient:
    def __init__(self, host, port, loop=None):
        self.host = host
        self.port = port
        self.loop = loop or asyncio.get_event_loop()
        self.reader = None
        self.writer = None

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            logger.info("TCP connected to %s:%s", self.host, self.port)
            return True
        except Exception as e:
            logger.warning("TCP connect failed: %s", e)
            return False

    async def send(self, data: bytes):
        try:
            if self.writer is None:
                ok = await self.connect()
                if not ok:
                    return False
            self.writer.write(data)
            await self.writer.drain()
            return True
        except Exception as e:
            logger.exception("TCP send error: %s", e)
            self.close()
            return False

    async def recv_loop(self, out_queue: asyncio.Queue):
        while True:
            if self.reader is None:
                ok = await self.connect()
                if not ok:
                    await asyncio.sleep(5)
                    continue
            try:
                data = await asyncio.wait_for(self.reader.readline(), timeout=30)
                if not data:
                    logger.warning("TCP connection closed by remote")
                    self.close()
                    continue
                msg = data.decode().strip()
                await out_queue.put(("tcp_cmd", {"msg": msg}))
            except asyncio.TimeoutError:
                try:
                    await self.send(b"PING\n")
                except Exception:
                    pass
            except Exception as e:
                logger.exception("TCP recv error: %s", e)
                self.close()
                await asyncio.sleep(5)

    def close(self):
        try:
            if self.writer:
                self.writer.close()
        finally:
            self.reader = None
            self.writer = None
