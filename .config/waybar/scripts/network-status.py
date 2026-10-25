#!/usr/bin/env python3
"""Waybar network module: prints a JSON line whenever NetworkManager state changes."""

import json
import sys

from gi.repository import GLib

from nm_common import NM, summary

client = NM.Client.new(None)
last = None
pending = None


def emit():
    global last, pending
    pending = None
    icon, cls, lines = summary(client)
    out = json.dumps(
        {"text": icon, "class": cls.split(), "tooltip": "\n".join(lines)}, ensure_ascii=False
    )
    if out != last:
        print(out, flush=True)
        last = out
    return False


def schedule(*_):
    global pending
    if pending is None:
        pending = GLib.timeout_add(250, emit)


def check_connectivity():
    # Actively re-check internet reachability (NM's own interval is 5 minutes).
    def done(c, res):
        try:
            c.check_connectivity_finish(res)
        except GLib.Error:
            pass
        schedule()

    client.check_connectivity_async(None, done)
    return True


client.connect("notify", schedule)
client.connect("active-connection-added", schedule)
client.connect("active-connection-removed", schedule)
emit()
GLib.timeout_add_seconds(5, lambda: (schedule(), True)[1])  # wifi signal strength
GLib.timeout_add_seconds(30, check_connectivity)

try:
    GLib.MainLoop().run()
except (KeyboardInterrupt, BrokenPipeError):
    sys.exit(0)
