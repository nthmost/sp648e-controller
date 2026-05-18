import uasyncio as asyncio

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


async def amain():
    wlan = wifi.connect()
    led = SP648E()
    server = Server(led, wlan)
    mqtt = MqttBridge(led)
    await asyncio.gather(
        maintain_ble(led),
        server.serve(),
        mqtt.run(),
    )


def run():
    asyncio.run(amain())


if __name__ == "__main__":
    run()
