#!/usr/bin/env python3
"""Sound popup for waybar: output/input device + volume, expandable per-app volumes."""

import json
import subprocess

from popup_common import (
    Gio, GLib, Gtk, Popup, clear, header, label, row, scrolled, section, small_button,
)

ICON_SPEAKER = "󰓃"
ICON_HEADPHONES = "󰋋"
ICON_HDMI = "󰍹"
ICON_MIC = "󰍬"
ICON_CHECK = "󰄬"
MUTE_ICONS = {
    "sink": ("󰕾", "󰖁"),
    "source": ("󰍬", "󰍭"),
    "sink-input": ("󰕾", "󰖁"),
}


def pactl_json(*args):
    try:
        out = subprocess.run(["pactl", "-f", "json", *args], capture_output=True, text=True, timeout=3).stdout
        return json.loads(out or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def pactl(*args):
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def volume_percent(obj):
    """Loudest channel, clamped to 100 since the UI never goes above it."""
    channels = obj.get("volume", {}).values()
    vals = [int(c["value_percent"].rstrip("%")) for c in channels if "value_percent" in c]
    return min(100, max(vals, default=0))


def device_icon(dev, kind):
    if kind == "source":
        return ICON_MIC
    name = (dev.get("name", "") + dev.get("active_port", "") + dev.get("description", "")).lower()
    if "bluez" in name or "headphone" in name or "headset" in name:
        return ICON_HEADPHONES
    if "hdmi" in name or "displayport" in name:
        return ICON_HDMI
    return ICON_SPEAKER


def app_name(stream):
    props = stream.get("properties", {})
    return props.get("application.name") or props.get("node.name") or "Unknown"


class SoundMenu(Popup):
    def __init__(self):
        super().__init__("sound")
        self.show_apps = False
        self.picker = None      # "sink" / "source" while its device list is open
        self.controls = {}      # (kind, id) -> (scale, handler, mute_button, pct_label)
        self.dragging = set()
        self.pending_volume = {}
        self._volume_timer = None
        self._refresh_timer = None
        self._structure = None

        self.apps_button = small_button("Apps 󰅀", self._toggle_apps, "Per-app volume")
        self.box.pack_start(header("󰕾  Sound", self.apps_button), False, False, 0)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.scroll = scrolled(self.content)
        self.box.pack_start(self.scroll, True, True, 0)

        self._subscribe()
        self.refresh()

    # ---- pactl events ----

    def _subscribe(self):
        self.sub = Gio.Subprocess.new(["pactl", "subscribe"], Gio.SubprocessFlags.STDOUT_PIPE)
        self.sub_stream = Gio.DataInputStream.new(self.sub.get_stdout_pipe())
        self.sub_stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_event)

    def _on_event(self, stream, res):
        line, _ = stream.read_line_finish_utf8(res)
        if line is None:
            return
        if self._refresh_timer is None:
            self._refresh_timer = GLib.timeout_add(100, self._debounced_refresh)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_event)

    def _debounced_refresh(self):
        self._refresh_timer = None
        self.refresh()
        return False

    def quit(self, *_):
        if getattr(self, "sub", None):
            self.sub.force_exit()
        return super().quit()

    # ---- actions ----

    def _toggle_apps(self):
        self.show_apps = not self.show_apps
        self.apps_button.set_label("Apps 󰅃" if self.show_apps else "Apps 󰅀")
        self.refresh()

    def _toggle_picker(self, kind):
        self.picker = None if self.picker == kind else kind
        self.refresh()

    def _set_default(self, kind, name):
        pactl(f"set-default-{kind}", name)
        self.picker = None
        self.refresh()

    def _toggle_mute(self, kind, ident):
        Gio.Subprocess.new(["pactl", f"set-{kind}-mute", str(ident), "toggle"], Gio.SubprocessFlags.NONE)

    def _on_volume(self, scale, kind, ident, pct_label):
        value = int(scale.get_value())
        pct_label.set_text(f"{value}%")
        self.pending_volume[(kind, ident)] = value
        if self._volume_timer is None:  # throttle while dragging
            self._volume_timer = GLib.timeout_add(40, self._flush_volume)

    def _flush_volume(self):
        self._volume_timer = None
        for (kind, ident), value in self.pending_volume.items():
            Gio.Subprocess.new(["pactl", f"set-{kind}-volume", str(ident), f"{value}%"],
                               Gio.SubprocessFlags.NONE)
        self.pending_volume.clear()
        return False

    # ---- UI ----

    def _volume_control(self, kind, ident, volume, muted):
        box = Gtk.Box(spacing=6)
        mute = small_button(MUTE_ICONS[kind][muted], lambda: self._toggle_mute(kind, ident),
                            "Mute / unmute")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_draw_value(False)
        scale.set_value(volume)
        pct = label(f"{volume}%", "status", xalign=1.0, ellipsize=False)
        pct.set_width_chars(4)
        handler = scale.connect("value-changed", self._on_volume, kind, ident, pct)
        scale.connect("button-press-event", lambda *_: self.dragging.add((kind, ident)))
        scale.connect("button-release-event", lambda *_: self.dragging.discard((kind, ident)))
        if muted:
            scale.get_style_context().add_class("muted")
        box.pack_start(mute, False, False, 0)
        box.pack_start(scale, True, True, 0)
        box.pack_start(pct, False, False, 0)
        self.controls[(kind, ident)] = (scale, handler, mute, pct)
        return box

    def _update_control(self, kind, ident, volume, muted):
        """Update an existing slider in place (rebuilding would break an ongoing drag)."""
        scale, handler, mute, pct = self.controls[(kind, ident)]
        mute.set_label(MUTE_ICONS[kind][muted])
        ctx = scale.get_style_context()
        (ctx.add_class if muted else ctx.remove_class)("muted")
        if (kind, ident) in self.dragging or (kind, ident) in self.pending_volume:
            return
        scale.handler_block(handler)
        scale.set_value(volume)
        scale.handler_unblock(handler)
        pct.set_text(f"{volume}%")

    def _device_section(self, add, kind, title, devices, default):
        add(section(title))
        current = next((d for d in devices if d["name"] == default), None)
        if current is None:
            add(label(f"No {title.lower()} device", "empty"))
            return
        add(row(
            device_icon(current, kind), current.get("description", current["name"]), None,
            on_click=(lambda: self._toggle_picker(kind)) if len(devices) > 1 else None,
            extra=[label("󰅃" if self.picker == kind else "󰅀", "status", ellipsize=False)]
            if len(devices) > 1 else [],
        ))
        if self.picker == kind:
            for d in devices:
                is_current = d["name"] == default
                btn = row(
                    device_icon(d, kind), d.get("description", d["name"]), None,
                    on_click=None if is_current else (lambda n=d["name"]: self._set_default(kind, n)),
                    extra=[label(ICON_CHECK, "status", ellipsize=False)] if is_current else [],
                    classes=["active"] if is_current else [],
                )
                btn.set_margin_start(20)
                add(btn)
        add(self._volume_control(kind, current["name"], volume_percent(current), int(current.get("mute", False))))

    def refresh(self):
        sinks = pactl_json("list", "sinks")
        sources = [s for s in pactl_json("list", "sources") if not s["name"].endswith(".monitor")]
        apps = pactl_json("list", "sink-inputs") if self.show_apps else []
        default_sink = pactl("get-default-sink")
        default_source = pactl("get-default-source")

        structure = (
            tuple(s["name"] for s in sinks), tuple(s["name"] for s in sources),
            tuple(a["index"] for a in apps), default_sink, default_source,
            self.show_apps, self.picker,
        )
        values = [("sink", s["name"], s) for s in sinks if s["name"] == default_sink]
        values += [("source", s["name"], s) for s in sources if s["name"] == default_source]
        values += [("sink-input", a["index"], a) for a in apps]

        if structure == self._structure:
            for kind, ident, obj in values:
                if (kind, ident) in self.controls:
                    self._update_control(kind, ident, volume_percent(obj), int(obj.get("mute", False)))
            return False
        self._structure = structure

        self.controls.clear()
        clear(self.content)
        add = lambda w: self.content.pack_start(w, False, False, 0)

        self._device_section(add, "sink", "Output", sinks, default_sink)
        self._device_section(add, "source", "Input", sources, default_source)

        if self.show_apps:
            add(section("Apps"))
            if not apps:
                add(label("Nothing is playing", "empty"))
            for a in apps:
                name = app_name(a)
                media = a.get("properties", {}).get("media.name", "")
                text = Gtk.Box(spacing=6)
                text.pack_start(label(name, "name", ellipsize=False), False, False, 0)
                if media and media != name:
                    text.pack_start(label(media, "detail"), True, True, 0)
                text.set_margin_start(8)
                text.set_margin_top(4)
                add(text)
                add(self._volume_control("sink-input", a["index"], volume_percent(a), int(a.get("mute", False))))

        self.content.show_all()
        self.fit(self.scroll, self.content, 480)
        return False


if __name__ == "__main__":
    SoundMenu().run()
