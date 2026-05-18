import bluetooth
import aioble

import config


SERVICE_UUID = bluetooth.UUID(0xFFE0)
WRITE_CHAR_UUID = bluetooth.UUID(0xFFE1)

# BanlanX SP6xxE protocol packet format (from uniled banlanx_6xx.py __encoder):
#   [0x53, cmd, 0x00, 0x01, 0x00, len(data), *data]
_HEADER = 0x53

# Commands (subset; full list in uniled banlanx_6xx.py)
_CMD_STATE_QUERY = 0x02
_CMD_POWER = 0x50
_CMD_BRIGHTNESS = 0x51   # data: [which, level]; which=0x00 color, 0x01 white
_CMD_RGB_STATIC = 0x52   # data: [r, g, b, level] — static-mode color set
_CMD_MODE_EFFECT = 0x53  # data: [mode_byte, effect_byte] — set mode + effect
_CMD_RGB_DYNAMIC = 0x57  # data: [r, g, b] — dynamic-mode color set

# Mode bytes (from uniled banlanx_6xx.py MODE_* constants)
_MODE_STATIC_COLOR = 0x01
_MODE_DYNAMIC_COLOR = 0x03
_MODE_SOUND_COLOR = 0x05

# Curated effects catalog: friendly_name -> (mode_byte, effect_byte).
# Hand-picked subset of the ~224 SP648E effects, chosen for visual variety.
# Friendly names render in HA's effect dropdown.
EFFECTS = {
    "Solid Color":     (_MODE_STATIC_COLOR, 0x01),  # uses the set RGB
    "Rainbow":         (_MODE_DYNAMIC_COLOR, 0x01),
    "Rainbow Meteor":  (_MODE_DYNAMIC_COLOR, 0x02),
    "Rainbow Comet":   (_MODE_DYNAMIC_COLOR, 0x03),
    "Rainbow Wave":    (_MODE_DYNAMIC_COLOR, 0x05),
    "Rainbow Stars":   (_MODE_DYNAMIC_COLOR, 0x07),
    "Rainbow Spin":    (_MODE_DYNAMIC_COLOR, 0x08),
    "Fire Red/Yellow": (_MODE_DYNAMIC_COLOR, 0x09),
    "Fire Blue/Cyan":  (_MODE_DYNAMIC_COLOR, 0x0E),
    "Comet Red":       (_MODE_DYNAMIC_COLOR, 0x0F),
    "Meteor White":    (_MODE_DYNAMIC_COLOR, 0x1C),
    "Sound: Spectrum": (_MODE_SOUND_COLOR, 0x01),
    "Sound: Stars":    (_MODE_SOUND_COLOR, 0x03),
    "Sound: Pulse":    (_MODE_SOUND_COLOR, 0x07),
    "Sound: VU Meter": (_MODE_SOUND_COLOR, 0x0D),
    "Sound: Heartbeat":(_MODE_SOUND_COLOR, 0x11),
    "Sound: Party":    (_MODE_SOUND_COLOR, 0x12),
}


def _packet(cmd, data=b""):
    return bytes([_HEADER, cmd & 0xFF, 0x00, 0x01, 0x00, len(data) & 0xFF]) + bytes(data)


def _mac_to_bytes(mac_str):
    return bytes(int(b, 16) for b in mac_str.split(":"))


class SP648E:
    """Minimal async BLE client for the BanlanX SP648E LED controller.

    Baseline scope: connect, discover service/characteristic, raw write_command().
    Command-byte mapping (power/mode/color/brightness) ported in a follow-up pass.
    """

    def __init__(self, mac=config.SP648E_MAC):
        self.mac = mac
        self.addr_bytes = _mac_to_bytes(mac)
        self.device = None
        self.connection = None
        self.write_char = None

    @property
    def connected(self):
        return self.connection is not None and self.connection.is_connected()

    async def connect(self, timeout_ms=10000):
        self.device = aioble.Device(aioble.ADDR_PUBLIC, self.addr_bytes)
        self.connection = await self.device.connect(timeout_ms=timeout_ms)
        service = await self.connection.service(SERVICE_UUID)
        if service is None:
            raise RuntimeError("sp648e: service 0xffe0 not found")
        self.write_char = await service.characteristic(WRITE_CHAR_UUID)
        if self.write_char is None:
            raise RuntimeError("sp648e: write char 0xffe1 not found")
        return self.connection

    async def disconnect(self):
        if self.connection is not None:
            try:
                await self.connection.disconnect()
            except Exception:
                pass
        self.connection = None
        self.write_char = None

    async def write_command(self, payload):
        if not self.connected or self.write_char is None:
            raise RuntimeError("sp648e: not connected")
        await self.write_char.write(payload, response=False)

    async def power(self, on):
        await self.write_command(_packet(_CMD_POWER, [0x01 if on else 0x00]))

    async def brightness(self, level):
        """Set brightness 0-255 in color-mode channel (which=0x00)."""
        level = max(0, min(255, int(level)))
        await self.write_command(_packet(_CMD_BRIGHTNESS, [0x00, level]))

    async def set_rgb(self, r, g, b, level=255):
        """Set static RGB color + brightness in one packet (cmd 0x52)."""
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))
        level = max(0, min(255, int(level)))
        await self.write_command(_packet(_CMD_RGB_STATIC, [r, g, b, level]))

    async def set_mode_effect(self, mode, effect):
        """Low-level: set mode byte + effect byte directly."""
        await self.write_command(_packet(_CMD_MODE_EFFECT, [mode & 0xFF, effect & 0xFF]))

    async def set_effect(self, name):
        """Set effect by friendly name from EFFECTS catalog. Returns True if found."""
        pair = EFFECTS.get(name)
        if pair is None:
            return False
        mode, effect = pair
        await self.set_mode_effect(mode, effect)
        return True

    async def query_state(self):
        await self.write_command(_packet(_CMD_STATE_QUERY, [0x01]))

    async def discover(self):
        """Return [(service_uuid, [(char_uuid, props), ...]), ...] for debug.

        aioble doesn't allow nested discovery iterators, so we materialize the
        service list first, then walk characteristics on each.
        """
        if not self.connected:
            raise RuntimeError("sp648e: not connected")
        services = []
        async for svc in self.connection.services(timeout_ms=5000):
            services.append(svc)
        out = []
        for svc in services:
            chars = []
            try:
                async for ch in svc.characteristics(timeout_ms=5000):
                    chars.append((str(ch.uuid), ch.properties))
            except Exception as e:
                chars.append(("(error: %s)" % e, 0))
            out.append((str(svc.uuid), chars))
        return out
