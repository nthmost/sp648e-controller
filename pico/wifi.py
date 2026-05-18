import network
import time

import config


def _try_connect(ssid, password, timeout=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.disconnect()
    time.sleep(0.5)
    if password:
        wlan.connect(ssid, password)
    else:
        wlan.connect(ssid)
    deadline = time.ticks_add(time.ticks_ms(), timeout * 1000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if wlan.isconnected():
            return wlan
        time.sleep(0.5)
    return None


def connect():
    """Connect to the primary SSID; fall back to the open one on failure."""
    for ssid, password in (config.WIFI_PRIMARY, config.WIFI_FALLBACK):
        print("wifi: trying", ssid)
        wlan = _try_connect(ssid, password)
        if wlan is not None:
            print("wifi: connected to", ssid, wlan.ifconfig())
            return wlan
        print("wifi: failed on", ssid)
    raise RuntimeError("wifi: no networks connected")
