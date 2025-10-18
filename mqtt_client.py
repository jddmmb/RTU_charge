import asyncio
import logging
from asyncio_mqtt import Client, MqttError
import json

logger = logging.getLogger("mqtt_client")

class MQTTHandler:
    def __init__(self, host="mqtt.example.com", port=1883, topic_prefix="rtu/edge", loop=None, username=None, password=None):
        self.host = host
        self.port = port
        self.topic_prefix = topic_prefix
        self.loop = loop or asyncio.get_event_loop()
        self.client = Client(hostname=self.host, port=self.port, username=username, password=password)

    async def run(self, in_queue: asyncio.Queue):
        while True:
            try:
                async with self.client as client:
                    logger.info("MQTT connected to %s:%s", self.host, self.port)
                    while True:
                        name, payload = await in_queue.get()
                        topic = f"{self.topic_prefix}/{name}"
                        msg = json.dumps(payload)
                        await client.publish(topic, msg.encode(), qos=1)
                        logger.debug("MQTT published %s -> %s", topic, msg)
            except MqttError as e:
                logger.warning("MQTT error: %s, reconnecting in 5s", e)
                await asyncio.sleep(5)
            except Exception as e:
                logger.exception("MQTT unexpected: %s", e)
                await asyncio.sleep(5)
