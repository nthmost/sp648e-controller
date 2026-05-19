# Multi-device upgrade plan

When this Pico needs to control more than one BLE LED controller (more hexagons, or other devices in the same physical area), follow this plan. The current code assumes a single device throughout; this is a focused refactor — not a rewrite — to accept N devices on one Pico.

## Hardware ceiling

Pico 2 W (CYW43439) supports up to ~5 concurrent BLE central connections. RAM is plentiful (~520KB on RP2350). Devices share the BLE radio time-sliced; LED command rates (a few bytes, a few times per second per device) are well below saturation.

If you need more than ~5 devices in one area, either split across multiple Picos or move to ESP32 (some variants advertise ~8 connections). Don't push the limit on the Pico's CYW43.

## Scope

- **In scope (this plan):** N devices of the same chip family (SP6xxE), in BLE range of one Pico.
- **Out of scope (future plan if needed):** heterogeneous chip families (Govee, Magic Home, etc.) — would add a `BLEDevice` ABC + per-family implementations following the same interface.

## Config shape

Replace single-device fields in `pico/config.py`:

```python
# Before (single device):
SP648E_MAC = "..."
SP648E_NAME = "SP648E"
DEVICE_ID = "wiresprite_hexagon"
DEVICE_NAME = "Hexagon"
DEVICE_AREA = "Front Entrance"

# After (multi-device):
DEVICES = [
    {
        "id": "wiresprite_hexagon",       # HA unique_id + topic component
        "name": "Hexagon",                 # HA friendly name
        "area": "Front Entrance",
        "mac": "a9:09:5a:36:09:6f",
        "type": "sp648e",                  # forward-looking: chip family selector
    },
    {
        "id": "wiresprite_hexagon_2",
        "name": "Hexagon (rear)",
        "area": "Rear Lounge",
        "mac": "xx:xx:xx:xx:xx:xx",
        "type": "sp648e",
    },
]
```

(Single-device compatibility shim is not worth the complexity — just migrate the install config at the same time as the code.)

## Code changes

### `pico/main.py`

Build one `SP648E` instance per config entry; spawn one `maintain_ble` task per device. The `MqttBridge` takes the list of devices instead of one.

```python
devices = [SP648E(mac=d["mac"]) for d in config.DEVICES]
device_map = {d["id"]: dev for d, dev in zip(config.DEVICES, devices)}
mqtt = MqttBridge(device_map)
server = Server(device_map, wlan)
await asyncio.gather(
    *[maintain_ble(dev) for dev in devices],
    server.serve(),
    mqtt.run(),
    status_indicator(wlan, devices, mqtt),
)
```

`status_indicator` now checks "all devices connected" instead of one.

### `pico/mqtt.py`

`MqttBridge` becomes multi-tenant. The current single-device topics:

```
sp648e/wiresprite_hexagon/set
sp648e/wiresprite_hexagon/state
sp648e/wiresprite_hexagon/availability
```

…become per-device, using the device's `id` for the middle path component. The bridge subscribes to all set topics with one wildcard sub (`sp648e/+/set`) or N individual subs.

Each device gets its own HA discovery payload (different `unique_id`, `name`, `device.identifiers`, `device.suggested_area`, topics). Publish all of them on connect.

Last-will for availability is per-device; with `umqtt.simple` only one last-will is allowed per connection, so either:
- accept that all devices share one availability topic (one connection drop = all marked offline together), OR
- run one MQTTClient per device (cleaner but more memory/connections)

The first option is simpler and correct for our use case (single Pico = single failure domain).

The `_on_message` callback dispatches by topic:
```python
def _on_message(self, topic, msg):
    # topic format: sp648e/<device_id>/set
    parts = topic.decode().split("/")
    if len(parts) != 3 or parts[2] != "set":
        return
    device_id = parts[1]
    device = self.devices.get(device_id)
    if device is None:
        return
    asyncio.create_task(self._apply(device, json.loads(msg)))
```

`_apply` takes a device parameter; state echo publishes to that device's state topic. Each device tracks its own `last_state`.

### `pico/server.py`

Routes change from flat to `/devices/<id>/...`:

```
GET  /status                    → list all devices + their state
GET  /devices/<id>/status       → one device
POST /devices/<id>/power/{on,off}
POST /devices/<id>/brightness/<level>
POST /devices/<id>/color/<r>/<g>/<b>[/<level>]
POST /devices/<id>/effect/<name>
GET  /effects                   → still global (catalog is per chip family, all our devices share it)
```

For backward compat with single-device callers, can keep the legacy flat routes if the install has exactly one device (route to that device implicitly). Probably not worth the special case.

### `pico/sp648e.py`

No changes. `SP648E(mac=...)` already takes a MAC; instantiate once per device. The `EFFECTS` catalog stays module-level (shared across all SP6xxE devices).

## Deployment

Standard deploy procedure (`mpremote touch :skip-main`, soft-reset, cp files, rm skip-main, soft-reset). Pico boots and:
1. Connects to wifi
2. Spawns BLE connect task per device — first connects in parallel
3. MQTT bridge publishes N discovery payloads, subscribes to N set topics (or one wildcard)
4. HA picks up N new entities

Verify each device individually before declaring success:
```sh
source ~/projects/nthmost-systems/.secrets/ha-noisebridge-mqtt.env
mosquitto_pub -h "$MQTT_HOST" -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t 'sp648e/wiresprite_hexagon_2/set' -m '{"state":"ON","effect":"Rainbow"}'
```

## HA-side housekeeping

Each new device shows up as its own entity. Add it to:
- `noisebridge-ha/dashboards/lights.yaml` — appropriate room tab (or new tab if new area)
- `noisebridge-ha/nblights.py` — `ENTITIES` list with group + kind=light
- noisebell automation if the new device should participate in open/close

## What to test (with two devices)

- Independent power: each device toggles independently
- Concurrent commands: send to both quickly, both respond
- One device offline: other keeps working; offline one is marked unavailable in HA after keepalive timeout
- BLE radio contention: send rapid commands to both, no command drops

## Estimate

~2-3 hours focused work, mostly in mqtt.py and main.py. The protocol layer (sp648e.py) and effects catalog don't change.

## When to revisit this plan

Open this doc when:
- A second LED controller arrives at NB and gets plugged in within range of this Pico
- OR the existing controller is moved and a co-located one wants the same bridge

If the new device is a **different chip family** (Govee, Magic Home, etc.), this plan still applies but you'll also need a new BLE protocol implementation. Source the protocol from [monty68/uniled](https://github.com/monty68/uniled) where possible; add a sibling class to `SP648E` that exposes the same `power / brightness / set_rgb / set_effect` methods, and route by `type` field in the device config.
