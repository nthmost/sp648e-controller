# SP648E protocol notes

Mined from [monty68/uniled `banlanx_6xx.py`](https://github.com/monty68/uniled/blob/main/custom_components/uniled/lib/ble/banlanx_6xx.py) (cloned at `~/projects/git/uniled/`).

## Identity

- **Model name (BLE advertised):** `SP648E`
- **Description:** "SPI RGB (Music) Controller"
- **Device byte:** `0x33`
- **Family class:** `SP638E_SP648E` (siblings: SP638E = 0x27)
- **Config:** `CFG_86()` — single config, so we don't need to negotiate
- **Manufacturer ID:** `20563` (BanlanX, in adv manufacturer_data)
- **Manufacturer data prefix:** `bytes([0x33, 0x10])` — match the first two bytes of adv manufacturer_data to confirm an SP648E vs. a sibling

## BLE GATT (confirmed by live discovery on a real SP648E, 2026-05-17)

The device exposes **two control services** plus the two standard GAP/GATT services:

- `0x1800` — Generic Access (standard)
- `0x1801` — Generic Attribute (standard)
- `0xffe0` — **classic Nordic ffe0/ffe1** (what uniled targets)
  - `0xffe1` — props `0x1c` = write-no-resp + write + notify
- `5833ff01-9b8b-5191-6142-22a4536ef123` — **custom undocumented service** (not in uniled)
  - `5833ff02-...` — write
  - `5833ff03-...` — notify

We target the `0xffe0/0xffe1` pair (matches uniled). The `5833ff01` service is interesting — possibly a newer command channel, possibly auth, possibly OTA. **TODO:** sniff what the BanlanX app writes there.

**MicroPython gotcha:** when matching short-form UUIDs, `bluetooth.UUID(0xffe0)` is **not equal** to `bluetooth.UUID("0000ffe0-0000-1000-8000-00805f9b34fb")` even though they encode the same UUID on the wire. Always use the 16-bit integer form for short UUIDs.

## Constraints

- **Single connection only.** While the Pico holds the link, the BanlanX phone app cannot connect. Force-kill the app (or turn phone BT off) when testing if it's been used recently.
- **No password / pairing.** Plain BLE GATT, no bonding required.

## Finding your device's MAC

Run a BLE scan from a phone (e.g., nRF Connect for Android/iOS) or from the Pico's REPL:

```python
import aioble, asyncio
async def scan():
    async with aioble.scan(duration_ms=8000) as scanner:
        async for result in scanner:
            if result.name() == "SP648E":
                print(result.device.addr_hex(), "rssi=", result.rssi)
asyncio.run(scan())
```

Put the resulting MAC in `pico/config.py` as `SP648E_MAC`.

## Command encoding (ported subset)

Packet format from uniled's `__encoder`:

```
[0x53, cmd, 0x00, 0x01, 0x00, len(data), *data]
   ^   ^    ^    ^    ^      ^
   |   |    |    |    |      length of payload
   |   |    |    |    constant
   |   |    |    constant
   |   |    key byte — hardcoded 0x00 in uniled source, unused
   |   command byte
   header (always 0x53)
```

Implemented commands (see `pico/sp648e.py`):

| Cmd  | Function          | Data bytes                                   |
|------|-------------------|----------------------------------------------|
| 0x02 | state query       | `[0x01]`                                     |
| 0x50 | power on/off      | `[0x01 if on else 0x00]`                     |
| 0x51 | brightness        | `[which, level]` (which=0x00 color, 0x01 white) |
| 0x52 | RGB static        | `[r, g, b, level]`                           |
| 0x53 | mode + effect     | `[mode_byte, effect_byte]`                   |
| 0x57 | RGB dynamic       | `[r, g, b]` (no level)                       |

Mode bytes for the SP648E (CFG_86 = "SPI RGB Music"):

| Byte | Mode                   | Effect count (uniled) |
|------|------------------------|-----------------------|
| 0x01 | static color           | 1 (just SOLID)        |
| 0x03 | dynamic color          | ~150 (rainbows, fire, comets, snakes, waves, stars, …) |
| 0x05 | sound color            | ~18 (spectrum, pulse, VU meter, party, …) |
| 0x07 | custom color           | user-defined          |

Our `EFFECTS` catalog in `sp648e.py` is a hand-picked 17-entry subset across modes 0x01, 0x03, 0x05 — chosen to give HA users a useful dropdown without 224 entries.

## Not yet ported

- **Speed** (cmd 0x54): `[speed]` for animation speed in dynamic/sound modes
- **Length** (cmd 0x55): `[length]` for effect span
- **Direction** (cmd 0x56): `[0x01 if forward else 0x00]`
- **Audio input source** (cmd 0x59): `[input]` — mic vs aux
- **Audio sensitivity** (cmd 0x5A): `[gain]` 0-255
- **Chip ordering** (cmd 0x6B): `[order]` — RGB vs BGR vs GRB etc.
- **Loop/auto-cycle effects** (cmd 0x56?): `[0x01 if enabled else 0x00]`

Each is a one-line addition: read the bytes from uniled's corresponding `build_*` method, add a method on `SP648E`, expose via MQTT/HTTP.
