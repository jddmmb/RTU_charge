from periphery import GPIO
import logging

logger = logging.getLogger("gpio_control")

class GPIOControl:
    def __init__(self, line, direction="out"):
        self.line = line
        self.direction = direction
        try:
            self.gpio = GPIO(line, "out" if direction=="out" else "in")
        except Exception as e:
            logger.exception("GPIO init failed: %s", e)
            self.gpio = None

    def set(self, value: bool):
        if not self.gpio:
            logger.warning("GPIO not initialized")
            return
        try:
            self.gpio.write(1 if value else 0)
            logger.debug("GPIO %s set %s", self.line, value)
        except Exception as e:
            logger.exception("GPIO set error: %s", e)

    def get(self) -> bool:
        if not self.gpio:
            return False
        try:
            return bool(self.gpio.read())
        except Exception as e:
            logger.exception("GPIO read error: %s", e)
            return False

    def close(self):
        try:
            if self.gpio:
                self.gpio.close()
        except Exception:
            pass
