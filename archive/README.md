# archive/

Everything here is **not** part of the Cloudflare Pages web deployment. It is
kept for reference only: ESP32 firmware, the Raspberry Pi serial→MQTT bridge,
systemd/deploy scripts, Blender build scripts, wiring/upgrade notes, and old
exports.

## What actually gets deployed (repo root)

- `index.html`
- `style.css`
- `app.js`
- `sim_engine.js`
- `assets/Conveyor_Twin_v1.glb`

Cloudflare Pages build settings: no build command, output directory = `/` (root).
Nothing in `archive/` is referenced by `index.html`.
