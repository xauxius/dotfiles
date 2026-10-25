"""Shared helpers for the small waybar popup menus (bluetooth, network)."""

import os
import signal
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell  # noqa: E402

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
POPUPS = ("bluetooth", "network", "sound")

CSS = b"""
* {
    font-family: "JetBrainsMono Nerd Font", "Symbols Nerd Font Mono", monospace;
    font-size: 12px;
    color: #ffffff;
}
window { background-color: transparent; }
.popup {
    background-color: rgba(20, 20, 20, 0.96);
    border-radius: 10px;
    padding: 10px;
}
.title { font-size: 14px; font-weight: bold; }
.section {
    font-size: 10px;
    font-weight: bold;
    color: rgba(255, 255, 255, 0.5);
    margin-top: 10px;
    margin-bottom: 2px;
}
.empty { color: rgba(255, 255, 255, 0.4); padding: 4px 8px; }
.status { color: rgba(255, 255, 255, 0.6); font-size: 11px; }
.error { color: #ff5555; }
button {
    background: none;
    border: none;
    box-shadow: none;
    border-radius: 8px;
    padding: 4px 8px;
    transition: background-color 0.15s ease;
}
button:hover { background-color: rgba(255, 255, 255, 0.12); }
button.row .icon { font-size: 16px; min-width: 22px; }
button.row .detail { color: rgba(255, 255, 255, 0.5); font-size: 10px; }
button.row.dim { opacity: 0.5; }
button.row.active .name { font-weight: bold; }
button.small { padding: 2px 6px; color: rgba(255, 255, 255, 0.6); }
button.small:hover { color: #ffffff; }
entry {
    background-color: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 6px;
    box-shadow: none;
    padding: 2px 6px;
}
switch {
    background-color: rgba(255, 255, 255, 0.15);
    border: none;
    border-radius: 10px;
}
switch:checked { background-color: rgba(120, 180, 255, 0.6); }
switch slider {
    background-color: #ffffff;
    border: none;
    border-radius: 10px;
    min-width: 16px;
    min-height: 16px;
}
scrollbar { background: none; border: none; }
.detail { color: rgba(255, 255, 255, 0.5); font-size: 10px; }
scale { padding: 6px 0; }
scale trough {
    background-color: rgba(255, 255, 255, 0.15);
    border: none;
    border-radius: 3px;
    min-height: 4px;
}
scale highlight {
    background-color: rgba(120, 180, 255, 0.8);
    border: none;
    border-radius: 3px;
}
scale.muted highlight { background-color: rgba(255, 255, 255, 0.3); }
scale slider {
    background-color: #ffffff;
    border: none;
    box-shadow: none;
    border-radius: 7px;
    min-width: 12px;
    min-height: 12px;
    margin: -5px 0;
}
"""


def _pidfile(name):
    return os.path.join(RUNTIME_DIR, f"waybar-popup-{name}.pid")


def _kill(name):
    """Kill a running popup instance. Returns True if one was running."""
    try:
        with open(_pidfile(name)) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, ValueError):
        return False


def toggle_instance(name):
    """Clicking the bar module again closes the popup; opening one closes the others."""
    if _kill(name):
        sys.exit(0)
    for other in POPUPS:
        if other != name:
            _kill(other)
    with open(_pidfile(name), "w") as f:
        f.write(str(os.getpid()))


def cleanup(name):
    try:
        with open(_pidfile(name)) as f:
            if int(f.read().strip()) == os.getpid():
                os.remove(_pidfile(name))
    except (OSError, ValueError):
        pass


class Popup(Gtk.Window):
    """Transparent full-screen layer with the popup in the top-right corner, under the bar.

    Covering the screen means hovering other windows can't steal focus (follow_mouse),
    and a click anywhere outside the popup closes it.
    """

    def __init__(self, name, width=340):
        super().__init__()
        self.name = name
        toggle_instance(name)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, f"waybar-{name}")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, edge, True)
        GtkLayerShell.set_exclusive_zone(self, 0)  # stay below the bar, so its buttons still work
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)

        backdrop = Gtk.EventBox()
        backdrop.set_visible_window(False)
        backdrop.connect("button-press-event", lambda *_: self.quit())
        self.add(backdrop)

        # Swallow clicks on the popup itself so they don't reach the backdrop.
        holder = Gtk.EventBox()
        holder.set_visible_window(False)
        holder.set_halign(Gtk.Align.END)
        holder.set_valign(Gtk.Align.START)
        holder.set_margin_top(4)
        holder.set_margin_end(6)
        holder.connect("button-press-event", lambda *_: True)
        backdrop.add(holder)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.box.get_style_context().add_class("popup")
        self.box.set_size_request(width, -1)
        holder.add(self.box)

        self.connect("key-press-event", self._on_key)
        self.connect("destroy", lambda *_: self.quit())
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.quit()
        return False

    def quit(self, *_):
        cleanup(self.name)
        Gtk.main_quit()
        return False

    def fit(self, scroll, content, max_height):
        """GTK3 windows don't follow their content's natural size, so size the scroll area by hand."""
        _, natural = content.get_preferred_height()
        scroll.set_min_content_height(min(natural, max_height))

    def run(self):
        self.show_all()
        Gtk.main()


# ---- widget helpers ----

def label(text, cls=None, xalign=0.0, ellipsize=True):
    lbl = Gtk.Label(label=text, xalign=xalign)
    if ellipsize:
        lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
    if cls:
        lbl.get_style_context().add_class(cls)
    return lbl


def header(title, widget=None):
    box = Gtk.Box(spacing=8)
    box.pack_start(label(title, "title"), True, True, 0)
    if widget:
        widget.set_valign(Gtk.Align.CENTER)
        box.pack_end(widget, False, False, 0)
    return box


def section(title):
    return label(title.upper(), "section")


def row(icon, name, detail=None, on_click=None, extra=None, classes=()):
    """A clickable list row: icon | name + detail | optional extra widgets."""
    btn = Gtk.Button()
    ctx = btn.get_style_context()
    for c in ("row", *classes):
        ctx.add_class(c)
    box = Gtk.Box(spacing=8)
    box.pack_start(label(icon, "icon", xalign=0.5, ellipsize=False), False, False, 0)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    text.pack_start(label(name, "name"), False, False, 0)
    if detail:
        text.pack_start(label(detail, "detail"), False, False, 0)
    box.pack_start(text, True, True, 0)
    for w in extra or ():
        box.pack_end(w, False, False, 0)
    btn.add(box)
    if on_click:
        btn.connect("clicked", lambda *_: on_click())
    return btn


def small_button(text, on_click, tooltip=None):
    btn = Gtk.Button(label=text)
    btn.get_style_context().add_class("small")
    if tooltip:
        btn.set_tooltip_text(tooltip)
    btn.connect("clicked", lambda *_: on_click())
    return btn


def scrolled(child):
    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    sw.add(child)
    return sw


def clear(box):
    for child in box.get_children():
        box.remove(child)
