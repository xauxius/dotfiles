#!/usr/bin/env bash
# Open slot $1 (1-8) of the current workspace group; handled by ws_open_slot()
# in ~/.config/hypr/workspaces.lua so empty workspaces open on their own monitor.
hyprctl eval "ws_open_slot(${1:?slot})" >/dev/null
