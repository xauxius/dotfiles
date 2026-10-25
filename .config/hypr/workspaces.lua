-- ~/.config/hypr/workspaces.lua
-- Workspace groups and moving workspaces between monitors.
--
-- Groups: SUPER + F1..F4 switches between groups of 8 workspaces
-- (group 1 = 1-8, group 2 = 9-16, ...). SUPER + 1..8 / SUPER + SHIFT + 1..8 stay
-- inside the current group. SUPER + 9 / 0 are global workspaces (ids 99 / 100) that
-- belong to no group and are reachable from anywhere.
--
-- The current group is written to STATE_FILE for waybar (scripts/workspaces.py).
--
-- SUPER + CTRL + 1/2 brings the current workspace to monitor 1/2 (numbered left to
-- right); the monitor it left shows the workspace that was last active there.

local GROUP_SIZE = 8
local GROUPS = 4
local GLOBAL = { [9] = 99, [0] = 100 } -- key -> workspace id
local STATE_FILE = (os.getenv("XDG_RUNTIME_DIR") or "/tmp") .. "/hypr-workspaces"

local current = 1     -- current group
local history = {}    -- monitor name -> workspace ids, most recent first
local group_last = {} -- group -> monitor name -> last active workspace id

local function group_of(id)
    if id < 1 or id > GROUPS * GROUP_SIZE then return nil end -- global/special
    return math.floor((id - 1) / GROUP_SIZE) + 1
end

local function base_of(group)
    return (group - 1) * GROUP_SIZE
end

local function monitors_by_position()
    local mons = hl.get_monitors()
    table.sort(mons, function(a, b)
        if a.x ~= b.x then return a.x < b.x end
        return a.y < b.y
    end)
    return mons
end

-- First workspace id of the group that doesn't exist yet (and isn't already planned).
local function free_id(group, taken)
    for i = 1, GROUP_SIZE do
        local id = base_of(group) + i
        if not taken[id] and hl.get_workspace(id) == nil then
            return id
        end
    end
    return base_of(group) + GROUP_SIZE
end

local function on_monitor(id, name)
    local ws = hl.get_workspace(id)
    return ws ~= nil and ws.monitor ~= nil and ws.monitor.name == name
end

local function write_state()
    local f = io.open(STATE_FILE .. ".tmp", "w")
    if f == nil then return end
    f:write("group " .. current .. "\n")
    f:close()
    os.rename(STATE_FILE .. ".tmp", STATE_FILE)
end

local function remember()
    local active = hl.get_active_workspace()
    if active ~= nil and group_of(active.id) ~= nil then
        current = group_of(active.id)
    end

    for _, m in ipairs(hl.get_monitors()) do
        local ws = m.active_workspace
        if ws ~= nil and ws.id > 0 then
            local h = history[m.name] or {}
            for i = #h, 1, -1 do
                if h[i] == ws.id then table.remove(h, i) end
            end
            table.insert(h, 1, ws.id)
            while #h > 30 do table.remove(h) end
            history[m.name] = h

            local g = group_of(ws.id)
            if g ~= nil then
                group_last[g] = group_last[g] or {}
                group_last[g][m.name] = ws.id
            end
        end
    end
    write_state()
end

hl.on("workspace.active", remember)
hl.on("workspace.move_to_monitor", remember)
hl.on("monitor.focused", remember)

local function open(id)
    hl.dispatch(hl.dsp.focus({ workspace = id }))
end

local function slot_id(key)
    return GLOBAL[key] or base_of(current) + key
end

-- Used by waybar's click handler: hyprctl eval "ws_open_slot(3)"
function ws_open_slot(slot)
    open(slot_id(slot))
end

local function switch_group(g)
    if g == current then return end
    current = g
    local focused = hl.get_active_monitor()
    local taken, plan = {}, {}

    for _, m in ipairs(monitors_by_position()) do
        -- 1. the workspace this monitor showed last time the group was open
        local target = group_last[g] and group_last[g][m.name]
        if target ~= nil and (taken[target] or (hl.get_workspace(target) ~= nil and not on_monitor(target, m.name))) then
            target = nil
        end
        -- 2. any existing workspace of the group that lives on this monitor
        if target == nil then
            for i = 1, GROUP_SIZE do
                local id = base_of(g) + i
                if not taken[id] and on_monitor(id, m.name) then
                    target = id
                    break
                end
            end
        end
        -- 3. a fresh, empty one
        target = target or free_id(g, taken)
        taken[target] = true
        table.insert(plan, { monitor = m.name, workspace = target })
    end

    -- The focused monitor goes last so focus ends up where it was.
    local function run(step)
        hl.dispatch(hl.dsp.focus({ monitor = step.monitor }))
        hl.dispatch(hl.dsp.focus({ workspace = step.workspace, on_current_monitor = true }))
    end
    for _, step in ipairs(plan) do
        if focused == nil or step.monitor ~= focused.name then run(step) end
    end
    for _, step in ipairs(plan) do
        if focused ~= nil and step.monitor == focused.name then run(step) end
    end
    write_state()
end

local function bring_to_monitor(n)
    local target = monitors_by_position()[n]
    local ws = hl.get_active_workspace()
    if target == nil or ws == nil or ws.id < 1 or ws.monitor == nil then return end
    local source = ws.monitor.name
    if source == target.name then return end

    -- Fill the vacuum with the most recent other workspace still on the source monitor.
    local filler = nil
    for _, id in ipairs(history[source] or {}) do
        if id ~= ws.id and on_monitor(id, source) then
            filler = id
            break
        end
    end
    filler = filler or free_id(group_of(ws.id) or current, { [ws.id] = true })

    local moved = ws.id
    hl.dispatch(hl.dsp.workspace.move({ workspace = moved, monitor = target.name }))
    hl.dispatch(hl.dsp.focus({ monitor = source }))
    hl.dispatch(hl.dsp.focus({ workspace = filler, on_current_monitor = true }))
    hl.dispatch(hl.dsp.focus({ workspace = moved }))
end

-- SUPER + 1..8 (current group), 9 / 0 (global); + SHIFT moves the active window there
for key = 0, 9 do
    hl.bind(mainMod .. " + " .. key, function() open(slot_id(key)) end)
    hl.bind(mainMod .. " + SHIFT + " .. key, function()
        hl.dispatch(hl.dsp.window.move({ workspace = slot_id(key) }))
    end)
end

for g = 1, GROUPS do
    hl.bind(mainMod .. " + F" .. g, function() switch_group(g) end)
end

for n = 1, 2 do
    hl.bind(mainMod .. " + CTRL + " .. n, function() bring_to_monitor(n) end)
end

remember()
