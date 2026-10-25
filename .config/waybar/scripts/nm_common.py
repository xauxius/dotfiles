"""NetworkManager helpers shared by network-status.py and network-menu.py."""

import gi

gi.require_version("NM", "1.0")
from gi.repository import NM  # noqa: E402

HOTSPOT = "Hotspot"

ICON_ETHERNET = "󰈀"
ICON_HOTSPOT = "󰀃"
ICON_OFF = "󰤮"
ICON_VPN = "󰖂"
# wifi-strength-1..4, each followed by its "-alert" variant
ICON_WIFI = ["󰤟", "󰤢", "󰤥", "󰤨"]
ICON_WIFI_ALERT = ["󰤠", "󰤣", "󰤦", "󰤩"]

CONNECTIVITY = {
    NM.ConnectivityState.FULL: "Connected",
    NM.ConnectivityState.LIMITED: "No internet",
    NM.ConnectivityState.PORTAL: "Login required (captive portal)",
    NM.ConnectivityState.NONE: "No internet",
    NM.ConnectivityState.UNKNOWN: "Unknown",
}

IGNORED_TYPES = {"loopback", "bridge", "tun", "dummy", "veth", "generic"}  # docker, lo, ...


def wifi_icon(strength, alert=False):
    idx = min(3, max(0, (strength or 0) // 25))
    return (ICON_WIFI_ALERT if alert else ICON_WIFI)[idx]


def ssid_str(ssid):
    if ssid is None:
        return ""
    return NM.utils_ssid_to_utf8(ssid.get_data())


def is_hotspot(conn):
    """conn: NM.Connection (remote or active's underlying connection)."""
    if conn is None:
        return False
    if conn.get_id() == HOTSPOT:
        return True
    wireless = conn.get_setting_wireless()
    return wireless is not None and wireless.get_mode() in ("ap", "adhoc")


def active_info(ac):
    """Describe an NM.ActiveConnection as a dict."""
    devices = ac.get_devices() or []
    dev = devices[0] if devices else None
    ctype = ac.get_connection_type()
    info = {
        "id": ac.get_id(),
        "type": ctype,
        "device": dev.get_iface() if dev else "",
        "ip": "",
        "strength": None,
        "ssid": "",
        "hotspot": is_hotspot(ac.get_connection()),
        "active": ac,
        "state": ac.get_state(),
    }
    ip4 = ac.get_ip4_config()
    if ip4 and ip4.get_addresses():
        addr = ip4.get_addresses()[0]
        info["ip"] = f"{addr.get_address()}/{addr.get_prefix()}"
    if isinstance(dev, NM.DeviceWifi) and not info["hotspot"]:
        ap = dev.get_active_access_point()
        if ap:
            info["strength"] = ap.get_strength()
            info["ssid"] = ssid_str(ap.get_ssid())
    return info


def active_connections(client):
    """Relevant active connections (skips loopback, docker bridges, etc.)."""
    result = []
    for ac in client.get_active_connections() or []:
        if ac.get_connection_type() in IGNORED_TYPES:
            continue
        result.append(active_info(ac))
    return result


def summary(client):
    """Icon, css class, and tooltip lines for the bar."""
    conns = active_connections(client)
    activated = [c for c in conns if c["state"] == NM.ActiveConnectionState.ACTIVATED]
    activating = [c for c in conns if c["state"] == NM.ActiveConnectionState.ACTIVATING]
    eth = [c for c in activated if c["type"] == "802-3-ethernet"]
    wifi = [c for c in activated if c["type"] == "802-11-wireless" and not c["hotspot"]]
    hotspot = [c for c in activated if c["hotspot"]]
    vpn = [c for c in activated if c["type"] in ("vpn", "wireguard")]

    connectivity = client.get_connectivity()
    online = connectivity in (NM.ConnectivityState.FULL, NM.ConnectivityState.UNKNOWN)
    lines = []

    if eth:
        icon, cls = ICON_ETHERNET, "ethernet"
    elif wifi:
        icon, cls = wifi_icon(wifi[0]["strength"], not online), "wifi"
    elif hotspot:
        icon, cls = ICON_HOTSPOT, "hotspot"
    else:
        icon, cls = ICON_OFF, "disconnected"

    for c in eth:
        lines.append(f"Ethernet: {c['id']} ({c['device']})")
    for c in wifi:
        lines.append(f"Wi-Fi: {c['ssid'] or c['id']} ({c['strength']}%)")
    for c in vpn:
        lines.append(f"VPN: {c['id']}")
    for c in hotspot:
        lines.append(f"Hotspot: {c['id']} on {c['device']}")
    for c in activating:
        lines.append(f"Connecting: {c['id']}…")

    if cls == "disconnected":
        lines.append("Not connected" if client.networking_get_enabled() else "Networking disabled")
    elif cls != "hotspot":
        lines.append(f"Internet: {CONNECTIVITY.get(connectivity, 'Unknown')}")
        if not online:
            cls += " limited"
    return icon, cls, lines
