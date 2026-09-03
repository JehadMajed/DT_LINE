# Digital Twin Conveyor System Overview & Integration Guide

This document summarizes the current software architecture, asset automation scripts, and hardware integration paths for the Digital Twin conveyor system. Use this as a reference for team discussions.

---

## 1. Project Directory Contents

The workspace contains two primary layers: the **Blender 3D Asset Automation scripts** and the **Interactive Web Application**.

### Web Application files
*   **[index.html](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/index.html)**: The frontend dashboard containing the telemetry meters (RPM, Current, Voltage, Piece Counter), the real-time system log, AI anomaly metrics, camera control settings, and the Emergency Stop (E-STOP) button.
*   **[app.js](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/app.js)**: The core Babylon.js 3D rendering loop. It handles loading the GLB model, physics simulations (like dynamic box spawning, speed controls, and falling cargo), component picking/clicking logic, and camera tracking modes (Orbit vs. First-Person Walk vs. Cargo Follow).
*   **[style.css](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/style.css)**: Glassmorphic theme styling, HUD layouts, sidebar panels, custom scrollbars, and aesthetic configurations.

### Blender Python Automation Scripts
These scripts assemble the electrical/sensor hardware layers inside your Blender file, parenting all items to the conveyor root (`DT_System_Root`) so they snap, translate, and scale automatically:
*   **[build_floor.py](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/build_floor.py)**: Spawns the concrete floor grid plane at world $Z = 0$.
*   **[build_control_box.py](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/build_control_box.py)**: Generates the horizontal logic controller enclosure (with top status LEDs and right-side glands), the floor power supply brick, and a US 3-prong plug resting on the floor plane.
*   **[build_proximity_sensor.py](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/build_proximity_sensor.py)**: Spawns the yellow barrel sensor clamped to the rail using a flipped metal L-bracket, matching the exact height coordinate (`Z = 0.012543`). Also routes the 3-wire sensor cable (Brown, Blue, Black) flat along the floor and into the box glands without going through the floor plane.
*   **[build_wiring.py](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/build_wiring.py)**: Renders the 6 individual motor wires exiting from the bottom edge of the motor encoder cap, loops them down, and routes them along the floor into the control box glands. Also draws the AC/DC power cables.

### Diagnostic Tools
*   **[extract_scene_details.py](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/extract_scene_details.py)**: A helper script that inspects your active Blender session to dump local/world dimensions, parent chains, and active coordinates into `scene_details.txt` to help align 3D assets.

---

## 2. Hardware Integration Path Options

To link the physical conveyor components (ESP32 MCU, NPN proximity sensor, encoder DC motor) to the web dashboard, your team can choose one of three integration routes:

### Option A: WiFi / MQTT Broker (Highly Recommended)
*   **How it works:** The ESP32 MCU reads sensor ticks and motor current, formats them into JSON, and publishes them over WiFi to a local MQTT broker (e.g. Eclipse Mosquitto) hosted on your server or PC. Node-RED subscribes to the MQTT topic and pushes the data to the web app via WebSockets.
*   **Pros:** Entirely wireless, scalable (multiple conveyor units can publish to the same broker), and allows the Node-RED system to easily log metrics to a database.
*   **Cons:** Requires local network router access and an MQTT broker setup.

### Option B: Local USB / Web Serial API (Easiest for single-unit desktop testing)
*   **How it works:** The ESP32 is plugged directly into the host computer's USB port. It writes data to the serial bus (`Serial.println()`). Using the browser's native **Web Serial API** directly inside `app.js`, the web application requests permission to read from the COM port, parsing the incoming serial stream locally.
*   **Pros:** Zero network dependencies, no backend servers (no Node-RED or MQTT needed), completely client-side in the browser.
*   **Cons:** Wired connection required, browser compatibility is limited to Chromium (Chrome, Edge, Opera).

### Option C: Industrial PLC Integration (Modbus TCP/IP)
*   **How it works:** If the physical conveyor is controlled by an industrial PLC (like Siemens S7 or Allen-Bradley) instead of an ESP32, Node-RED acts as a master querying the PLC registers over **Modbus TCP** or **S7 Protocol**. Node-RED then translates those registers into WebSockets for the frontend 3D viewport.
*   **Pros:** Industry standard, robust, safe for production environments.
*   **Cons:** Requires industrial hardware communication modules.
