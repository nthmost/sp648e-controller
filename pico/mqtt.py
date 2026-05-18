"""MQTT client for the SP648E controller, with Home Assistant auto-discovery.

Uses umqtt.simple (blocking) inside an asyncio task — fine for low message
rates. State is published whenever it changes; commands arrive on a subscribed
topic and are translated into BLE writes via the SP648E client.

HA MQTT JSON Light schema:
  https://www.home-assistant.io/integrations/light.mqtt/#json-schema
"""

import json
import uasyncio as asyncio
from umqtt.simple import MQTTClient

import config
from sp648e import EFFECTS


def _topics():
    base = "sp648e/%s" % config.DEVICE_ID
    return {
        "state": base + "/state",
        "command": base + "/set",
        "availability": base + "/availability",
        "discovery": "%s/light/%s/config" % (config.HA_DISCOVERY_PREFIX, config.DEVICE_ID),
    }


def _discovery_payload(topics):
    return {
        # has_entity_name=True + name=None tells HA: "this is the device's
        # primary entity, use the device name as-is." Without this, HA produces
        # an entity_id like light.foo_foo (device + entity name concatenated).
        "name": None,
        "has_entity_name": True,
        "unique_id": config.DEVICE_ID,
        "schema": "json",
        # Optimistic mode: HA treats its own commands as the source of truth
        # for the entity state and only weakly subscribes to state_topic for
        # observation. Without this, any spurious state-topic publish (e.g.
        # a transient MQTT reconnect republishing a stale baseline) flips HA's
        # view incorrectly. We still expose state_topic so HA can pick up
        # state changes that legitimately originate at the device, but the
        # source-of-truth flag stays on the command side.
        "optimistic": True,
        "state_topic": topics["state"],
        "command_topic": topics["command"],
        "availability_topic": topics["availability"],
        "payload_available": "online",
        "payload_not_available": "offline",
        "brightness": True,
        "supported_color_modes": ["rgb"],
        "effect": True,
        "effect_list": list(EFFECTS.keys()),
        "device": {
            "identifiers": [config.DEVICE_ID],
            "name": config.DEVICE_NAME,
            "manufacturer": "BanlanX",
            "model": "SP648E",
            "suggested_area": getattr(config, "DEVICE_AREA", None),
        },
    }


class MqttBridge:
    """Bridges MQTT <-> SP648E. Command-byte stubs delegate to the LED client.

    On each iteration we wait for a message (with a timeout) and re-publish
    state. If the broker disconnects, we reconnect with backoff.
    """

    def __init__(self, led):
        self.led = led
        self.topics = _topics()
        self.client = None
        self.last_state = None  # for change detection in future

    def _new_client(self):
        c = MQTTClient(
            client_id=config.DEVICE_ID,
            server=config.MQTT_HOST,
            port=config.MQTT_PORT,
            user=config.MQTT_USER or None,
            password=config.MQTT_PASS or None,
            keepalive=60,
        )
        c.set_last_will(self.topics["availability"], b"offline", retain=True)
        c.set_callback(self._on_message)
        return c

    def _on_message(self, topic, msg):
        try:
            payload = json.loads(msg)
        except Exception:
            print("mqtt: bad json:", msg)
            return
        print("mqtt: cmd ->", payload)
        # MQTT callback is sync; schedule async BLE work on the event loop.
        asyncio.create_task(self._apply(payload))

    async def _apply(self, payload):
        # HA's JSON light schema: {"state": "ON"|"OFF", optionally brightness/color/effect/...}
        state = payload.get("state")
        brightness = payload.get("brightness")
        color = payload.get("color")
        effect = payload.get("effect")
        try:
            if state == "ON":
                await self.led.power(True)
            elif state == "OFF":
                await self.led.power(False)
            # Effect first, then color/brightness (effects can override color in
            # dynamic/sound modes; setting effect first lets a subsequent color
            # apply when switching back to a static mode).
            if effect is not None:
                ok = await self.led.set_effect(effect)
                if not ok:
                    print("mqtt: unknown effect:", effect)
            if color is not None:
                # Combine color + brightness into one RGB_STATIC packet when both given;
                # use 255 as a sane default level otherwise.
                level = brightness if brightness is not None else 255
                await self.led.set_rgb(color.get("r", 0), color.get("g", 0), color.get("b", 0), level)
            elif brightness is not None:
                await self.led.brightness(brightness)
        except Exception as e:
            print("mqtt: apply failed:", repr(e))
            return
        # Echo applied state back so HA stays in sync (optimistic — we don't read
        # the device, we just report what we set).
        echo = dict(self.last_state) if self.last_state else {}
        if state in ("ON", "OFF"):
            echo["state"] = state
        if brightness is not None:
            echo["brightness"] = brightness
        if color is not None:
            echo["color"] = color
            echo["color_mode"] = "rgb"
        if effect is not None:
            echo["effect"] = effect
        if "state" not in echo:
            # brightness/color/effect without state implies ON in HA's model
            echo["state"] = "ON"
        self.publish_state(echo)

    async def _connect(self):
        self.client = self._new_client()
        self.client.connect()
        print("mqtt: connected to", config.MQTT_HOST)
        # HA discovery (retained so HA picks it up on restart)
        self.client.publish(
            self.topics["discovery"],
            json.dumps(_discovery_payload(self.topics)),
            retain=True,
        )
        self.client.publish(self.topics["availability"], b"online", retain=True)
        self.client.subscribe(self.topics["command"])
        # Re-publish the last state we know about, if any. We deliberately do
        # NOT publish a baseline OFF here: a transient MQTT reconnect would
        # otherwise flip HA's view to OFF even though the LED panel itself is
        # still whatever we last set it to. If last_state is None (first run),
        # HA's MQTT entity shows "unknown" until the first user action — that's
        # the honest answer since we don't query the device for its real state.
        if self.last_state is not None:
            self.publish_state(self.last_state)

    def publish_state(self, state):
        if self.client is None:
            return
        try:
            self.client.publish(self.topics["state"], json.dumps(state), retain=True)
            self.last_state = state
        except Exception as e:
            print("mqtt: publish state failed:", e)

    async def run(self):
        if not config.MQTT_ENABLED:
            print("mqtt: disabled (config.MQTT_ENABLED is False)")
            return
        backoff = 1
        while True:
            try:
                await self._connect()
                backoff = 1
                while True:
                    self.client.check_msg()
                    await asyncio.sleep_ms(200)
            except Exception as e:
                print("mqtt: error:", repr(e))
                try:
                    self.client.disconnect()
                except Exception:
                    pass
                self.client = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
