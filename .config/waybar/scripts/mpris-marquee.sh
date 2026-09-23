#!/usr/bin/env bash

MAX_CHARS=60
DISPLAY_CHARS=20
SLEEP=0.5
SELECT="$HOME/.config/waybar/scripts/playerctl-active.sh"

while true; do

    PLAYER="$("$SELECT")"

    if [[ -z "$PLAYER" ]]; then
        jq -cn '{text:"", class:"empty", tooltip:""}'
        sleep "$SLEEP"
        continue
    fi

    status="$(playerctl --player="$PLAYER" status 2>/dev/null)"
    title="$(playerctl --player="$PLAYER" metadata --format '{{ title }}' 2>/dev/null)"
    artist="$(playerctl --player="$PLAYER" metadata --format '{{ artist }}' 2>/dev/null)"

    text="$title - $artist"

    if [[ ${#text} -gt $MAX_CHARS ]]; then
        text="${text:0:$((MAX_CHARS - 1))}…"
    fi

    case "$status" in
        Playing)
            marquee="$text     "
            while [[ ${#marquee} -lt $DISPLAY_CHARS ]]; do
                marquee+=" "
            done
            len=${#marquee}
            pos=$(( $(date +%s%3N) / 150 % len ))
            output="${marquee:$pos}${marquee:0:$pos}"
            output="${output:0:$DISPLAY_CHARS}"
            class="playing"
            ;;
        Paused)
            output="$text"
            class="paused"
            ;;
        *)
            output="$text"
            class="stopped"
            ;;
    esac

    jq -cn \
        --arg text "♪  $output" \
        --arg class "$class" \
        --arg tooltip "$text" \
        '{text:$text, class:$class, tooltip:$tooltip}'

    sleep "$SLEEP"
done