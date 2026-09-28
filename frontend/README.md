# Mainsail integration source

The prebuilt beta frontend is based on Mainsail v2.19.0 (`5fb9e77f`). It is distributed as a release asset because the current Mainsail frontend does not load external panels at runtime.

`McuUpdateManagerPanel.vue` is the panel component. `mainsail-v2.19.0.patch` contains the Machine page registration and English/Czech translation keys. To rebuild, check out the Mainsail v2.19.0 tag, apply the patch, copy the panel into `src/components/panels/Machine/`, install dependencies according to Mainsail's instructions, and run its production build. Review upstream changes before applying this patch to any other Mainsail version.

The panel communicates exclusively with the Moonraker component through `/machine/mcu_update_manager/*`. It does not directly access USB, CAN, or system services from the browser.
