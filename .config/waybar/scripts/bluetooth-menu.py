#!/usr/bin/env python3
"""Bluetooth popup for waybar: connected / remembered / other devices."""

import re
import subprocess

from popup_common import (
    Gio, GLib, Gtk, Popup, clear, header, label, row, scrolled, section, small_button,
)

BLUEZ = "org.bluez"

ICONS = {
    "audio-headset": "󰋋",
    "audio-headphones": "󰋋",
    "audio-card": "󰓃",
    "input-mouse": "󰍽",
    "input-keyboard": "󰌌",
    "input-gaming": "󰊴",
    "input-tablet": "󰓶",
    "phone": "󰏲",
    "computer": "󰟀",
    "video-display": "󰍹",
}


class BluetoothMenu(Popup):
    def __init__(self):
        super().__init__("bluetooth")
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.adapter = None
        self.default_address = self._default_controller()
        self.busy = {}      # address -> "Connecting…" etc.
        self.error = None
        self._pending = None

        self.power = Gtk.Switch()
        self._power_handler = self.power.connect("notify::active", self._on_power)
        self.box.pack_start(header("󰂯  Bluetooth", self.power), False, False, 0)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.scroll = scrolled(self.content)
        self.box.pack_start(self.scroll, True, True, 0)

        self.bus.signal_subscribe(
            BLUEZ, None, None, None, None, Gio.DBusSignalFlags.NONE, self._on_signal
        )
        self.refresh()
        self._start_discovery()

    # ---- D-Bus ----

    @staticmethod
    def _default_controller():
        """Address of bluetoothctl's default controller, since all actions go through bluetoothctl."""
        try:
            out = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"Controller (\S+) .*\[default\]", out)
        return m.group(1) if m else None

    def _objects(self):
        res = self.bus.call_sync(
            BLUEZ, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
            None, None, Gio.DBusCallFlags.NONE, -1, None,
        )
        return res.unpack()[0]

    def _start_discovery(self):
        if not self.adapter:
            return
        # Discovery is tied to this D-Bus connection, so it stops when the popup closes.
        self.bus.call(
            BLUEZ, self.adapter, "org.bluez.Adapter1", "StartDiscovery",
            None, None, Gio.DBusCallFlags.NONE, -1, None, lambda *_: None,
        )

    def _on_signal(self, *_):
        if self._pending is None:
            self._pending = GLib.timeout_add(300, self._debounced_refresh)

    def _debounced_refresh(self):
        self._pending = None
        self.refresh()
        return False

    # ---- actions ----

    def _run(self, address, status, script):
        """Run a bluetoothctl command chain without blocking the UI."""
        self.busy[address] = status
        self.error = None
        self.refresh()
        proc = Gio.Subprocess.new(
            ["sh", "-c", script],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE,
        )

        def done(p, res):
            _, out, _ = p.communicate_utf8_finish(res)
            self.busy.pop(address, None)
            if not p.get_successful() or "Failed" in (out or ""):
                lines = [l for l in (out or "").splitlines() if "Failed" in l or "Error" in l]
                self.error = lines[-1].strip() if lines else f"{status.rstrip('…')} failed"
            self.refresh()

        proc.communicate_utf8_async(None, None, done)

    def connect_dev(self, dev):
        self._run(dev["Address"], "Connecting…", f"bluetoothctl connect {dev['Address']}")

    def disconnect_dev(self, dev):
        self._run(dev["Address"], "Disconnecting…", f"bluetoothctl disconnect {dev['Address']}")

    def pair_dev(self, dev):
        a = dev["Address"]
        self._run(
            a, "Pairing…",
            f"bluetoothctl pairable on >/dev/null; "
            f"bluetoothctl --agent NoInputNoOutput --timeout 30 pair {a} && "
            f"bluetoothctl trust {a} && bluetoothctl connect {a}",
        )

    def forget_dev(self, dev):
        self._run(dev["Address"], "Removing…", f"bluetoothctl remove {dev['Address']}")

    def _on_power(self, switch, _param):
        on = switch.get_active()
        proc = Gio.Subprocess.new(
            ["sh", "-c", f"{'rfkill unblock bluetooth; ' if on else ''}bluetoothctl power {'on' if on else 'off'}"],
            Gio.SubprocessFlags.STDOUT_SILENCE,
        )
        proc.wait_async(None, lambda *_: (self.refresh(), on and self._start_discovery()))

    # ---- UI ----

    def _device_row(self, dev, kind):
        addr = dev["Address"]
        name = dev.get("Alias") or dev.get("Name") or addr
        icon = ICONS.get(dev.get("Icon", ""), "󰂯")
        detail_parts = []
        extra = []

        if addr in self.busy:
            detail_parts.append(self.busy[addr])
        elif kind == "connected":
            detail_parts.append("Connected")
            if dev.get("Battery") is not None:
                detail_parts.append(f"󰁹 {dev['Battery']}%")
        elif kind == "remembered":
            detail_parts.append("Available" if "RSSI" in dev else "Not in range")
        else:
            detail_parts.append(addr)

        if kind in ("connected", "remembered") and addr not in self.busy:
            extra.append(small_button("󰆴", lambda: self.forget_dev(dev), "Forget device"))

        action = {
            "connected": self.disconnect_dev,
            "remembered": self.connect_dev,
            "other": self.pair_dev,
        }[kind]
        classes = []
        if kind == "connected":
            classes.append("active")
        if kind == "remembered" and "RSSI" not in dev:
            classes.append("dim")
        return row(
            icon, name, " · ".join(detail_parts),
            on_click=None if addr in self.busy else (lambda: action(dev)),
            extra=extra, classes=classes,
        )

    def refresh(self):
        objects = self._objects()
        self.adapter = None
        powered = False
        devices = []
        for path, ifaces in objects.items():
            adapter = ifaces.get("org.bluez.Adapter1")
            if adapter and (self.adapter is None or adapter.get("Address") == self.default_address):
                self.adapter = path
                powered = bool(adapter.get("Powered"))
        for path, ifaces in objects.items():
            if ifaces.get("org.bluez.Device1", {}).get("Adapter") == self.adapter:
                dev = dict(ifaces["org.bluez.Device1"])
                dev["Battery"] = ifaces.get("org.bluez.Battery1", {}).get("Percentage")
                devices.append(dev)

        self.power.handler_block(self._power_handler)
        self.power.set_active(powered)
        self.power.set_sensitive(self.adapter is not None)
        self.power.handler_unblock(self._power_handler)

        clear(self.content)
        add = lambda w: self.content.pack_start(w, False, False, 0)

        if self.error:
            add(label(self.error, "error"))
        if self.adapter is None:
            add(label("No Bluetooth adapter found", "empty"))
        elif not powered:
            add(label("Bluetooth is off", "empty"))
        else:
            connected = [d for d in devices if d.get("Connected")]
            remembered = [d for d in devices if d.get("Paired") and not d.get("Connected")]
            others = [d for d in devices if not d.get("Paired") and not d.get("Connected") and d.get("Name")]
            remembered.sort(key=lambda d: ("RSSI" not in d, (d.get("Alias") or "").lower()))
            others.sort(key=lambda d: -d.get("RSSI", -999))

            for title, devs, kind, empty in (
                ("Connected", connected, "connected", "No devices connected"),
                ("Remembered", remembered, "remembered", "No remembered devices"),
                ("Other devices", others, "other", "Scanning…"),
            ):
                add(section(title))
                if devs:
                    for d in devs:
                        add(self._device_row(d, kind))
                else:
                    add(label(empty, "empty"))

        self.content.show_all()
        self.fit(self.scroll, self.content, 420)
        return False


if __name__ == "__main__":
    BluetoothMenu().run()
