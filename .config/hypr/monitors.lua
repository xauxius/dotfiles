------------------
---- MONITORS ----
------------------
hl.env("ELECTRON_OZONE_PLATFORM_HINT", "auto")
-- See https://wiki.hypr.land/Configuring/Basics/Monitors/
hl.monitor({
    output   = "HDMI-A-1",
    mode     = "preferred",
    position = "auto",
    scale    = 1,
})

hl.monitor({
    output   = "HDMI-A-2",
    mode     = "preferred",
    position = "auto",
    scale    = 1,
})
