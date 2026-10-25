#!/usr/bin/env python3
"""Workspace overview for waybar.

Runs as one background daemon (the "custom/ws-daemon" module). On every Hyprland event
it writes one JSON file per (bar output, slot) and signals waybar, whose
"custom/ws-N" modules just `cat` their file. That way every workspace is a real
module: rounded boxes, CSS classes per monitor, and its own on-click.

Slots are 1-8 of the current group. The group and the monitor each workspace was last
on come from the state file written by ~/.config/hypr/workspaces.lua, so empty
workspaces keep their monitor color.
"""

import fcntl
import json
import os
import select
import signal
import socket
import sys
import time

GROUP_SIZE = 8
SIGNAL = signal.SIGRTMIN + 9  # must match "signal": 9 in the waybar config

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
OUT_DIR = os.path.join(RUNTIME, "waybar-ws")
HYPR_DIR = os.path.join(RUNTIME, "hypr", os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""))
STATE_FILE = os.path.join(RUNTIME, "hypr-workspaces")

DEFAULT_ICON = "󰣆"
ICONS = {
    "kitty": "󰆍",
    "foot": "󰆍",
    "alacritty": "󰆍",
    "brave-browser": "󰖟",
    "firefox": "󰈹",
    "chromium": "󰊯",
    "google-chrome": "󰊯",
    "code": "󰨞",
    "code-oss": "󰨞",
    "thunar": "󰉋",
    "discord": "󰙯",
    "spotify": "󰓇",
    "blender": "󰂫",
    "mpv": "󰐊",
    "com.anthropic.claude": "󰚩",
    "steam": "󰓓",
    "teams-for-linux": "󰊻",
    "zoom": "󰕧",
    "notion": "󰎚",
    "org.pwmt.zathura": "󰈦",
    "sourcegit": "󰊢",
    "org.pulseaudio.pavucontrol": "󰕾",
}
OFFICE_ICON = "󰈙"


def icon_for(cls):
    cls = (cls or "").lower()
    if cls in ICONS:
        return ICONS[cls]
    if cls.startswith("libreoffice"):
        return OFFICE_ICON
    return DEFAULT_ICON


def hypr(cmd):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(os.path.join(HYPR_DIR, ".socket.sock"))
        s.sendall(f"j/{cmd}".encode())
        chunks = []
        while chunk := s.recv(65536):
            chunks.append(chunk)
    return json.loads(b"".join(chunks) or b"[]")


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def read_state():
    group, ws_monitor = 1, {}
    try:
        with open(STATE_FILE) as f:
            for line in f:
                parts = line.split()
                if parts[:1] == ["group"]:
                    group = int(parts[1])
                elif parts[:1] == ["ws"] and len(parts) == 3:
                    ws_monitor[int(parts[1])] = parts[2]
    except (OSError, ValueError, IndexError):
        pass
    return group, ws_monitor


def state_mtime():
    try:
        return os.stat(STATE_FILE).st_mtime_ns
    except OSError:
        return 0


def render():
    monitors = sorted(hypr("monitors"), key=lambda m: (m["x"], m["y"]))
    workspaces = {w["id"]: w for w in hypr("workspaces") if w["id"] > 0}
    clients = hypr("clients")

    mon_index = {m["name"]: i + 1 for i, m in enumerate(monitors)}
    visible = {m["activeWorkspace"]["id"] for m in monitors}
    focused = next((m["activeWorkspace"]["id"] for m in monitors if m.get("focused")), 1)
    group, ws_monitor = read_state()
    base = (group - 1) * GROUP_SIZE

    windows = {}
    for c in clients:
        if c.get("mapped", True) and not c.get("hidden"):
            windows.setdefault(c["workspace"]["id"], []).append(c)

    files = {}
    for mon in monitors:
        for slot in range(1, GROUP_SIZE + 1):
            ws_id = base + slot
            ws = workspaces.get(ws_id)
            home = ws["monitor"] if ws else ws_monitor.get(ws_id)
            wins = sorted(windows.get(ws_id, []), key=lambda c: (c["at"][0], c["at"][1]))
            # The leftmost window represents the workspace.
            icon = icon_for(wins[0]["class"]) if wins else ""
            text = f"<span size='x-small' rise='4pt'>{slot}</span>"
            if icon:
                text += f" {icon}"
            classes = ["ws", f"mon{mon_index[home]}" if home in mon_index else "nomon"]
            if ws_id in visible:
                classes.append("visible")
            if ws_id == focused:
                classes.append("focused")
            if not wins:
                classes.append("empty")
            tooltip = [f"Workspace {slot}" + (f" · {home}" if home else "")]
            tooltip += [f"{icon_for(c['class'])}  {esc(c['title'][:60] or c['class'])}" for c in wins]
            files[(mon["name"], slot)] = {"text": text, "class": classes, "tooltip": "\n".join(tooltip)}
    return files


def signal_waybar():
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() == "waybar":
                    os.kill(int(pid), SIGNAL)
        except OSError:
            pass


def write(files, last):
    changed = False
    for (output, slot), data in files.items():
        content = json.dumps(data, ensure_ascii=False)
        if last.get((output, slot)) == content:
            continue
        path = os.path.join(OUT_DIR, f"{output}-{slot}.json")
        with open(path + ".tmp", "w") as f:
            f.write(content + "\n")
        os.replace(path + ".tmp", path)
        last[(output, slot)] = content
        changed = True
    return changed


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    # Every bar starts this module; only one daemon needs to run.
    lock = open(os.path.join(OUT_DIR, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return

    parent = os.getppid()
    mtime = 0
    last = {}
    events = None
    dirty = True
    while True:
        if events is None:
            try:
                events = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                events.connect(os.path.join(HYPR_DIR, ".socket2.sock"))
            except OSError:
                events = None
                time.sleep(2)
                continue

        if dirty:
            time.sleep(0.03)  # let bursts of events settle
            try:
                if write(render(), last):
                    signal_waybar()
            except (OSError, ValueError):
                pass
            dirty = False

        ready, _, _ = select.select([events], [], [], 0.5)
        if ready:
            data = events.recv(65536)
            if not data:
                events.close()
                events = None
            dirty = True
        if state_mtime() != mtime:  # group switches, remembered monitors
            mtime = state_mtime()
            dirty = True
        if os.getppid() != parent:  # waybar is gone
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
