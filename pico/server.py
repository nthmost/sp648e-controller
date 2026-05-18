import json
import uasyncio as asyncio

import config


def _response(status, body, ctype="application/json"):
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    if isinstance(body, str):
        body = body.encode()
    return (
        b"HTTP/1.1 " + status.encode() + b"\r\n"
        b"Content-Type: " + ctype.encode() + b"\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n"
    ) + body


def _ok(body):
    return _response("200 OK", body)


def _not_found():
    return _response("404 Not Found", {"error": "not found"})


def _not_implemented(what):
    return _response("501 Not Implemented", {"error": "stub", "feature": what})


class Server:
    def __init__(self, led, wlan):
        self.led = led
        self.wlan = wlan

    async def handle(self, reader, writer):
        try:
            req_line = await reader.readline()
            # drain headers
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
            parts = req_line.decode().split()
            if len(parts) < 2:
                writer.write(_response("400 Bad Request", {"error": "bad request"}))
                await writer.drain()
                return
            method, path = parts[0], parts[1]
            response = await self.route(method, path)
            writer.write(response)
            await writer.drain()
        except Exception as e:
            try:
                writer.write(_response("500 Internal Server Error", {"error": repr(e)}))
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                await writer.aclose()
            except Exception:
                pass

    async def route(self, method, path):
        if method == "GET" and path == "/status":
            return _ok({
                "wifi": self.wlan.isconnected(),
                "ip": self.wlan.ifconfig()[0] if self.wlan.isconnected() else None,
                "ble_connected": self.led.connected,
                "sp648e_mac": self.led.mac,
            })
        if method == "GET" and path == "/scan":
            if not self.led.connected:
                return _response("503 Service Unavailable", {"error": "ble not connected"})
            services = await self.led.discover()
            return _ok({
                "services": [
                    {"uuid": s, "chars": [{"uuid": cu, "props": "0x%02x" % cp} for cu, cp in chars]}
                    for s, chars in services
                ]
            })
        if method == "POST" and path == "/power/on":
            if not self.led.connected:
                return _response("503 Service Unavailable", {"error": "ble not connected"})
            await self.led.power(True)
            return _ok({"state": "ON"})
        if method == "POST" and path == "/power/off":
            if not self.led.connected:
                return _response("503 Service Unavailable", {"error": "ble not connected"})
            await self.led.power(False)
            return _ok({"state": "OFF"})
        if method == "GET" and path == "/effects":
            from sp648e import EFFECTS
            return _ok({"effects": list(EFFECTS.keys())})
        if method == "POST" and path.startswith("/effect/"):
            if not self.led.connected:
                return _response("503 Service Unavailable", {"error": "ble not connected"})
            name = path[len("/effect/"):].replace("%20", " ")
            ok = await self.led.set_effect(name)
            if not ok:
                return _response("404 Not Found", {"error": "unknown effect", "name": name})
            return _ok({"effect": name})
        if method == "POST" and path.startswith("/mode/"):
            return _not_implemented("mode (use /effect/<name>)")
        if method == "POST" and path.startswith("/brightness/"):
            if not self.led.connected:
                return _response("503 Service Unavailable", {"error": "ble not connected"})
            try:
                level = int(path[len("/brightness/"):])
            except ValueError:
                return _response("400 Bad Request", {"error": "brightness must be int 0-255"})
            await self.led.brightness(level)
            return _ok({"brightness": max(0, min(255, level))})
        if method == "POST" and path == "/color":
            return _not_implemented("color (use POST /color/<r>/<g>/<b>)")
        if method == "POST" and path.startswith("/color/"):
            if not self.led.connected:
                return _response("503 Service Unavailable", {"error": "ble not connected"})
            try:
                parts = path[len("/color/"):].split("/")
                r, g, b = (int(p) for p in parts[:3])
                level = int(parts[3]) if len(parts) > 3 else 255
            except (ValueError, IndexError):
                return _response("400 Bad Request", {"error": "use /color/<r>/<g>/<b>[/<level>] with 0-255 ints"})
            await self.led.set_rgb(r, g, b, level)
            return _ok({"color": {"r": r, "g": g, "b": b}, "brightness": level})
        return _not_found()

    async def serve(self):
        srv = await asyncio.start_server(self.handle, "0.0.0.0", config.HTTP_PORT)
        print("http: listening on :%d" % config.HTTP_PORT)
        while True:
            await asyncio.sleep(3600)
