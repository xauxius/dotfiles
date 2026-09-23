#!/usr/bin/env bash

STATE_FILE="${XDG_RUNTIME_DIR:-/tmp}/waybar-active-player"

mapfile -t players < <(playerctl -l 2>/dev/null)

status_of() { playerctl --player="$1" status 2>/dev/null; }

pick() {
    echo "$1" > "$STATE_FILE"
    echo "$1"
    exit 0
}

# Find Spotify's MPRIS name (usually "spotify", sometimes with an instance suffix)
spotify="spotify"
for p in "${players[@]}"; do
    if [[ $p == spotify* ]]; then
        spotify="$p"
        break
    fi
done

# 1. Spotify playing -> always Spotify
if [[ -n $spotify && "$(status_of "$spotify")" == "Playing" ]]; then
    pick "$spotify"
fi

# 2. Some other player playing
for p in "${players[@]}"; do
    [[ $p == "$spotify" ]] && continue
    if [[ "$(status_of "$p")" == "Playing" ]]; then
        pick "$p"
    fi
done

# 3. Nothing playing, Spotify open -> Spotify
if [[ -n $spotify ]]; then
    pick "$spotify"
fi

# 4. Spotify not open -> last remembered player, if it still exists
if [[ -f $STATE_FILE ]]; then
    p="$(<"$STATE_FILE")"
    if playerctl --player="$p" status >/dev/null 2>&1; then
        echo "$p"
        exit 0
    fi
    rm -f "$STATE_FILE"
fi

exit 1