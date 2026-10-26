#!/usr/bin/env python3
"""Network popup for waybar: current connection, hotspot, Wi-Fi networks, saved connections."""

from popup_common import (
    Gio, GLib, Gtk, Popup, clear, header, label, row, scrolled, section, small_button,
)
from nm_common import (
    CONNECTIVITY, HOTSPOT, ICON_ETHERNET, ICON_HOTSPOT, ICON_VPN, IGNORED_TYPES, NM,
    active_connections, is_hotspot, ssid_str, wifi_icon,
)

KEY_MGMT_PSK = 0x100
KEY_MGMT_8021X = 0x200
KEY_MGMT_SAE = 0x400
AP_FLAG_PRIVACY = 0x1

TYPE_ICONS = {"802-3-ethernet": ICON_ETHERNET, "vpn": ICON_VPN, "wireguard": ICON_VPN}
TYPE_NAMES = {"802-3-ethernet": "Ethernet", "vpn": "VPN", "wireguard": "WireGuard"}


class NetworkMenu(Popup):
    def __init__(self):
        super().__init__("network")
        self.client = NM.Client.new(None)
        self.error = None
        self.busy = None        # name of connection currently being activated
        self.prompt_ssid = None  # SSID whose password entry is open
        self._pending = None

        self.wifi_switch = Gtk.Switch()
        self._wifi_handler = self.wifi_switch.connect("notify::active", self._on_wifi_toggle)
        self.box.pack_start(header("󰖩  Network", self.wifi_switch), False, False, 0)
        self.wifi_switch.set_tooltip_text("Wi-Fi")

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.scroll = scrolled(self.content)
        self.box.pack_start(self.scroll, True, True, 0)

        for sig in ("notify", "active-connection-added", "active-connection-removed",
                    "connection-added", "connection-removed"):
            self.client.connect(sig, self._schedule)
        for dev in self._wifi_devices():
            dev.connect("access-point-added", self._schedule)
            dev.connect("access-point-removed", self._schedule)

        self.rescan()
        self.refresh()

    # ---- helpers ----

    def _wifi_devices(self):
        return [d for d in self.client.get_devices() if isinstance(d, NM.DeviceWifi)]

    def _saved_wifi(self):
        saved = {}
        for conn in self.client.get_connections():
            wireless = conn.get_setting_wireless()
            if wireless and not is_hotspot(conn):
                saved[ssid_str(wireless.get_ssid())] = conn
        return saved

    def _schedule(self, *_):
        if self._pending is None:
            self._pending = GLib.timeout_add(300, self._debounced_refresh)

    def _debounced_refresh(self):
        self._pending = None
        if self.prompt_ssid is None:  # don't wipe the password entry while typing
            self.refresh()
        return False

    def _fail(self, msg):
        self.error = msg
        self.busy = None
        self.refresh()

    # ---- actions ----

    def rescan(self):
        for dev in self._wifi_devices():
            dev.request_scan_async(None, lambda d, r: self._ignore(d.request_scan_finish, r))

    @staticmethod
    def _ignore(finish, res):
        try:
            finish(res)
        except GLib.Error:
            pass

    def _watch_activation(self, ac, name, delete_on_fail=None):
        """Report failures that happen after activation has started (e.g. wrong password)."""
        def changed(ac, state, reason):
            if state == NM.ActiveConnectionState.ACTIVATED:
                self.busy = None
                ac.disconnect(handler)  # a later deactivation is a normal disconnect
                self._schedule()
            elif state == NM.ActiveConnectionState.DEACTIVATED:
                if delete_on_fail is not None:
                    delete_on_fail.delete_async(None, None, None)
                    self._fail(f"Failed to connect to {name} (wrong password?)")
                else:
                    self._fail(f"Failed to connect to {name}")
        handler = ac.connect("state-changed", changed)

    def activate(self, conn):
        name = conn.get_id()
        self.busy, self.error = name, None

        def done(client, res):
            try:
                self._watch_activation(client.activate_connection_finish(res), name)
            except GLib.Error as e:
                self._fail(e.message)

        self.client.activate_connection_async(conn, None, None, None, done)
        self.refresh()

    def deactivate(self, ac):
        def done(client, res):
            try:
                client.deactivate_connection_finish(res)
            except GLib.Error as e:
                self._fail(e.message)

        self.error = None
        self.client.deactivate_connection_async(ac, None, done)

    def connect_new(self, ap, dev, password=None):
        ssid = ssid_str(ap.get_ssid())
        partial = None
        if password is not None:
            partial = NM.SimpleConnection.new()
            sec = NM.SettingWirelessSecurity.new()
            flags = ap.get_rsn_flags() | ap.get_wpa_flags()
            sae_only = flags & KEY_MGMT_SAE and not flags & KEY_MGMT_PSK
            sec.set_property("key-mgmt", "sae" if sae_only else "wpa-psk")
            sec.set_property("psk", password)
            partial.add_setting(sec)
        self.busy, self.error, self.prompt_ssid = ssid, None, None

        def done(client, res):
            try:
                ac = client.add_and_activate_connection_finish(res)
                self._watch_activation(ac, ssid, delete_on_fail=ac.get_connection())
            except GLib.Error as e:
                self._fail(e.message)

        self.client.add_and_activate_connection_async(partial, dev, ap.get_path(), None, done)
        self.refresh()

    def _on_wifi_toggle(self, switch, _param):
        on = switch.get_active()
        Gio.Subprocess.new(["nmcli", "radio", "wifi", "on" if on else "off"],
                           Gio.SubprocessFlags.NONE)
        if on:
            GLib.timeout_add_seconds(2, lambda: (self.rescan(), False)[1])

    def _on_hotspot_toggle(self, switch, _param, conn, ac):
        if switch.get_active():
            self.activate(conn)
        elif ac:
            self.deactivate(ac)

    # ---- UI ----

    def _password_box(self, ap, dev):
        ssid = ssid_str(ap.get_ssid())
        box = Gtk.Box(spacing=6)
        box.set_margin_start(30)
        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_placeholder_text(f"Password for {ssid}")
        submit = lambda *_: entry.get_text() and self.connect_new(ap, dev, entry.get_text())
        entry.connect("activate", submit)
        box.pack_start(entry, True, True, 0)
        box.pack_start(small_button("󰌑", submit, "Connect"), False, False, 0)
        box.pack_start(small_button("󰅖", self._cancel_prompt, "Cancel"), False, False, 0)
        GLib.idle_add(entry.grab_focus)
        return box

    def _cancel_prompt(self):
        self.prompt_ssid = None
        self.refresh()

    def _open_prompt(self, ssid):
        self.prompt_ssid = ssid
        self.error = None
        self.refresh()

    def _ap_clicked(self, ap, dev, saved):
        ssid = ssid_str(ap.get_ssid())
        if saved:
            self.activate(saved)
            return
        flags = ap.get_rsn_flags() | ap.get_wpa_flags()
        if flags & KEY_MGMT_8021X:
            self._fail(f"{ssid}: enterprise networks need nmcli / manual setup")
        elif flags or ap.get_flags() & AP_FLAG_PRIVACY:
            self._open_prompt(ssid)
        else:
            self.connect_new(ap, dev)

    def refresh(self):
        client = self.client
        wifi_on = client.wireless_get_enabled()
        self.wifi_switch.handler_block(self._wifi_handler)
        self.wifi_switch.set_active(wifi_on)
        self.wifi_switch.set_sensitive(client.wireless_hardware_get_enabled())
        self.wifi_switch.handler_unblock(self._wifi_handler)

        clear(self.content)
        add = lambda w: self.content.pack_start(w, False, False, 0)
        if self.error:
            add(label(self.error, "error"))

        conns = active_connections(client)
        current = [c for c in conns if not c["hotspot"]]
        active_ids = {c["id"] for c in conns}

        # Current connection(s)
        add(section("Current"))
        if not current:
            add(label("Not connected", "empty"))
        for c in current:
            if c["type"] == "802-11-wireless":
                icon = wifi_icon(c["strength"])
                parts = [f"{c['strength']}%" if c["strength"] is not None else "Wi-Fi"]
            else:
                icon = TYPE_ICONS.get(c["type"], "󰛳")
                parts = [TYPE_NAMES.get(c["type"], c["type"])]
            parts += [c["device"], c["ip"]]
            if c["state"] == NM.ActiveConnectionState.ACTIVATING:
                parts.insert(0, "Connecting…")
            add(row(
                icon, c["ssid"] or c["id"], " · ".join(p for p in parts if p),
                extra=[small_button("󰅖", lambda ac=c["active"]: self.deactivate(ac), "Disconnect")],
                classes=["active"],
            ))
        if current:
            add(label(f"Internet: {CONNECTIVITY.get(client.get_connectivity(), 'Unknown')}", "status"))

        # Hotspot
        hotspot_conn = client.get_connection_by_id(HOTSPOT)
        if hotspot_conn:
            hs = next((c for c in conns if c["id"] == HOTSPOT), None)
            starting = self.busy == HOTSPOT
            switch = Gtk.Switch(active=hs is not None or starting)
            switch.connect("notify::active", self._on_hotspot_toggle, hotspot_conn, hs and hs["active"])
            wireless = hotspot_conn.get_setting_wireless()
            if starting:
                detail = "Starting…"
            elif hs and hs["state"] == NM.ActiveConnectionState.ACTIVATED:
                detail = f"Active · {hs['ip'].split('/')[0]}"
            else:
                detail = "Starting…" if hs else "Off"
            if wireless:
                detail = f"{ssid_str(wireless.get_ssid())} · {detail}"
            add(section("Hotspot"))
            add(row(ICON_HOTSPOT, HOTSPOT, detail, extra=[switch], classes=["active"] if hs else []))

        # Wi-Fi networks
        saved = self._saved_wifi()
        title = Gtk.Box()
        title.pack_start(section("Wi-Fi networks"), True, True, 0)
        if wifi_on:
            title.pack_end(small_button("󰑐", self.rescan, "Rescan"), False, False, 0)
        add(title)
        if not wifi_on:
            add(label("Wi-Fi is off", "empty"))
        else:
            current_ssids = {c["ssid"] for c in current if c["ssid"]}
            hotspot_devs = {c["device"] for c in conns if c["hotspot"]}
            if hotspot_conn and hotspot_conn.get_setting_wireless():
                current_ssids.add(ssid_str(hotspot_conn.get_setting_wireless().get_ssid()))
            best = {}
            for dev in self._wifi_devices():
                if dev.get_iface() in hotspot_devs:  # an adapter in AP mode can't scan
                    continue
                for ap in dev.get_access_points():
                    ssid = ssid_str(ap.get_ssid())
                    if ssid and ssid not in current_ssids and (
                        ssid not in best or ap.get_strength() > best[ssid][0].get_strength()
                    ):
                        best[ssid] = (ap, dev)
            aps = sorted(best.items(), key=lambda kv: (kv[0] not in saved, -kv[1][0].get_strength()))
            if not aps:
                busy_note = f"{', '.join(sorted(hotspot_devs))} is busy with the hotspot" if hotspot_devs else ""
                add(label(busy_note or "No networks found", "empty"))
            for ssid, (ap, dev) in aps:
                secured = bool(ap.get_rsn_flags() | ap.get_wpa_flags() or ap.get_flags() & AP_FLAG_PRIVACY)
                parts = [f"{ap.get_strength()}%"]
                if ssid == self.busy:
                    parts.insert(0, "Connecting…")
                elif ssid in saved:
                    parts.insert(0, "Saved")
                if secured:
                    parts.append("󰌾")
                add(row(
                    wifi_icon(ap.get_strength()), ssid, " · ".join(parts),
                    on_click=lambda ap=ap, dev=dev, s=saved.get(ssid): self._ap_clicked(ap, dev, s),
                ))
                if ssid == self.prompt_ssid:
                    add(self._password_box(ap, dev))

        # Other saved connections (ethernet, VPN, ...)
        others = [
            conn for conn in client.get_connections()
            if conn.get_connection_type() not in IGNORED_TYPES
            and conn.get_connection_type() != "802-11-wireless"
            and conn.get_id() not in active_ids
        ]
        if others:
            add(section("Other connections"))
            for conn in sorted(others, key=lambda c: c.get_id().lower()):
                ctype = conn.get_connection_type()
                detail = TYPE_NAMES.get(ctype, ctype)
                if conn.get_id() == self.busy:
                    detail = f"Connecting… · {detail}"
                add(row(TYPE_ICONS.get(ctype, "󰛳"), conn.get_id(), detail,
                        on_click=lambda conn=conn: self.activate(conn)))

        self.content.show_all()
        self.fit(self.scroll, self.content, 460)
        return False


if __name__ == "__main__":
    NetworkMenu().run()
