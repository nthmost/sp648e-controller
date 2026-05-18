# sp648e-controller

A Raspberry Pi Pico 2 W bridges WiFi → BLE → MQTT/HTTP to control a BanlanX **SP648E** SPI RGB (Music) LED controller (the chip behind a lot of cheap addressable-LED products controlled by the BanlanX / iDeal LED / LED Hue apps). The Pico publishes itself as a Home Assistant light via MQTT auto-discovery and also exposes a small HTTP API on the LAN for ad-hoc control and debugging.

The SP648E only accepts one BLE connection at a time, so while the Pico is connected to it, the BanlanX phone app cannot reach it (and vice versa).

## Quickstart

1. Flash MicroPython on a Pico 2 W (firmware: [`RPI_PICO2_W-*.uf2`](https://micropython.org/download/RPI_PICO2_W/)).
2. Clone this repo, then `cp pico/config.py.example pico/config.py` and fill in your WiFi credentials, MQTT broker details, and your SP648E's BLE MAC (see `docs/protocol-notes.md` for how to scan for it).
3. Install MicroPython's MQTT library via `mip`: connect to wifi from the Pico's REPL, then `import mip; mip.install("umqtt.simple")`.
4. Push the contents of `pico/` to the Pico with `mpremote cp pico/*.py :`.
5. Power-cycle the Pico; it should connect to WiFi, find your SP648E, register itself as a light entity in Home Assistant via MQTT discovery, and start accepting commands.

## Status

**Working end-to-end:** WiFi + BLE auto-reconnect, Home Assistant MQTT auto-discovery, JSON HTTP API. All from a single Pico 2 W running ~250 lines of MicroPython.

Supported commands (all live in HA's UI):
- Power on/off (cmd `0x50`)
- Brightness 0-255 (cmd `0x51`)
- RGB color in static mode (cmd `0x52`)
- 17 hand-picked effects across static / dynamic / sound modes (cmd `0x53`)

**Not ported yet:** effect speed (0x54), effect length (0x55), effect direction (0x56), audio sensitivity (0x5A), custom effects (CFG_86 supports user-defined patterns), and the ~210 dynamic/sound effects not in our curated list. All trivially additive — read the byte in `~/projects/git/uniled/custom_components/uniled/lib/ble/banlanx_6xx.py`, add a method on `SP648E`, wire to MQTT/HTTP handler.

## Hardware

- Raspberry Pi Pico 2 W (RP2350 + CYW43439)
- MicroPython v1.28.0 firmware (`RPI_PICO2_W-20260406-v1.28.0.uf2`)
- Target: BanlanX SP648E (manufacturer ID `20563`, device byte `0x33`, type "SPI RGB Music Controller")

## Project layout

```
pico/
  config.py.example — template; copy to config.py and fill in your values
  config.py         — your real install values (gitignored)
  wifi.py           — connect with primary + optional fallback SSID
  sp648e.py         — async BLE client (aioble) + EFFECTS catalog
  server.py         — tiny asyncio HTTP server, JSON API
  mqtt.py           — HA JSON-light bridge with auto-discovery
  main.py           — entrypoint: wifi → BLE → http server + mqtt bridge
  boot.py           — minimal, runs main (with skip-main rescue file)
docs/
  protocol-notes.md — intel mined from uniled (packet format, command bytes, GATT layout)
```

## Deploy

```
mpremote cp pico/*.py :
mpremote exec "import machine; machine.soft_reset()"
```

(Use `soft_reset` rather than `mpremote reset` / hard-reset — see *Gotchas* below.)

After reboot, the Pico prints its IP on the USB serial console. HTTP API is on port 8080:

```
curl http://<pico-ip>:8080/status
curl -X POST http://<pico-ip>:8080/power/on
curl -X POST http://<pico-ip>:8080/effect/Rainbow%20Wave
```

## Iterating on the code

`boot.py` auto-runs `main.py`, which takes over the asyncio event loop and can prevent `mpremote` from entering raw REPL. To safely push updates:

```sh
# 1. Drop into REPL-only mode (creates a skip-main marker file)
mpremote touch :skip-main
mpremote exec "import machine; machine.soft_reset()"

# 2. Push your changes
mpremote cp pico/sp648e.py :sp648e.py

# 3. Re-enable autorun
mpremote rm :skip-main
mpremote exec "import machine; machine.soft_reset()"
```

## Gotchas

- **`mpremote reset` (hard reset) reliably kills USB CDC** on this firmware + our app pattern. Once `main.run()` takes over after a hard reset, the host stops seeing `/dev/ttyACM*` until a full `picotool erase -a` + re-flash. Always soft-reset, or use the `skip-main` rescue file.
- **MicroPython's `bluetooth.UUID(0xffe0)` is NOT equal to `bluetooth.UUID("0000ffe0-0000-1000-8000-00805f9b34fb")`** even though they encode the same UUID on the wire. Use the 16-bit integer form for short UUIDs.
- **aioble doesn't allow nested discovery iterators** — collect services into a list first, then iterate characteristics on each.
- **HA's MQTT discovery may not process a retained message published before the integration subscribed.** Reload the MQTT integration via REST API (`POST /api/config/config_entries/entry/<id>/reload`) to force a re-scan of all retained discovery topics.
- **HA preserves entity_id ↔ unique_id mapping forever** — renaming the device via discovery does NOT rename the entity_id. To get a fresh entity_id, change the `unique_id` AND clear the old retained discovery topic.

## Credits / references

Built by reading the protocol implementation in [monty68/uniled](https://github.com/monty68/uniled) (Home Assistant integration for the BanlanX SPxxxE family). Many thanks to that project — without it, this would have required hours of BLE sniffing.

## API

| Endpoint                              | Method | Notes                                                |
|---------------------------------------|--------|------------------------------------------------------|
| `/status`                             | GET    | wifi + BLE connection state                          |
| `/scan`                               | GET    | rescan GATT services + characteristics               |
| `/effects`                            | GET    | list of named effects in catalog                     |
| `/power/on`, `/power/off`             | POST   | toggle the strip                                     |
| `/brightness/<level>`                 | POST   | brightness 0-255 in color mode                       |
| `/color/<r>/<g>/<b>[/<level>]`        | POST   | RGB + optional brightness in static mode             |
| `/effect/<name>`                      | POST   | switch to a named effect from the catalog            |

MQTT (Home Assistant JSON light schema) is the primary control path; HTTP is for ad-hoc scripts and debugging.
