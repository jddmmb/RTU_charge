import asyncio
import logging
from modbus_poll import ModbusPoller
from serial_devices import open_serial, parse_line_frames
from mqtt_client import MQTTHandler
from tcp_operator import TCPClient
from gpio_control import GPIOControl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

async def aggregator_task(in_queue: asyncio.Queue, mqtt_queue: asyncio.Queue):
    while True:
        src, payload = await in_queue.get()
        await mqtt_queue.put((src, payload))

async def main():
    loop = asyncio.get_event_loop()
    in_queue = asyncio.Queue()
    mqtt_queue = asyncio.Queue()

    gpio = GPIOControl(line=17)  # 根据平台调整 GPIO 编号
    gpio.set(False)

    modbus = ModbusPoller(port="/dev/ttyUSB0", baudrate=19200, unit_id=1, loop=loop)
    asyncio.create_task(modbus.poll_loop(in_queue, interval=5))

    asyncio.create_task(open_serial(loop, "/dev/ttyS1", 115200, "screen", in_queue, frame_parser=parse_line_frames))
    asyncio.create_task(open_serial(loop, "/dev/ttyS2", 9600, "rfid", in_queue, frame_parser=parse_line_frames))

    tcp = TCPClient(host="192.168.1.100", port=12345, loop=loop)
    asyncio.create_task(tcp.recv_loop(in_queue))

    mqtt = MQTTHandler(host="mqtt.example.com", port=1883, topic_prefix="rtu/jddmmb", loop=loop)
    asyncio.create_task(mqtt.run(mqtt_queue))

    asyncio.create_task(aggregator_task(in_queue, mqtt_queue))

    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down")
