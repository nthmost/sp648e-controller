import uasyncio as asyncio
from machine import Pin

import wifi
from sp648e import SP648E
from server import Server
from mqtt import MqttBridge


async def maintain_ble(led):
    """Keep the BLE link to the SP648E up; reconnect with backoff on drop."""
    backoff = 1
    while True:
        try:
            if not led.connected:
                print("ble: connecting to", led.mac)
                await led.connect()
                print("ble: connected")
                backoff = 1
            await asyncio.sleep(2)
        except Exception as e:
            print("ble: error:", repr(e))
            await led.disconnect()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def status_indicator(wlan, led, mqtt):
    """Drive the onboard LED to indicate health.

      solid on  — wifi + BLE + MQTT all healthy
      slow blink (1Hz) — at least one subsystem is down (BLE or MQTT not up)
      off — wifi is down (shouldn't happen — wifi.connect blocks until up)

    The LED is wired through the CYW43439 wireless chip on Pico 2 W, so it's
    accessed via Pin("LED") rather than a numbered GPIO.
    """
    pin = Pin("LED", Pin.OUT)
    blink = False
    while True:
        wifi_ok = wlan.isconnected()
        ble_ok = led.connected
        mqtt_ok = mqtt.client is not None
        if wifi_ok and ble_ok and mqtt_ok:
            pin.on()
            await asyncio.sleep(1)
        elif wifi_ok:
            blink = not blink
            pin.value(1 if blink else 0)
            await asyncio.sleep_ms(500)
        else:
            pin.off()
            await asyncio.sleep(1)


async def amain():
    wlan = wifi.connect()
    led = SP648E()
    server = Server(led, wlan)
    mqtt = MqttBridge(led)
    await asyncio.gather(
        maintain_ble(led),
        server.serve(),
        mqtt.run(),
        status_indicator(wlan, led, mqtt),
    )


def run():
    asyncio.run(amain())


if __name__ == "__main__":
    run()
