"""
=============================================================================
  DIGITAL TWIN — HARDWARE LAYER BUILDER v1.0
  File: build_hardware_components.py
  Run from: Blender Scripting Tab (Text Editor → Run Script)

  Builds all PCB/electrical hardware components:
    - 12V 5A PSU (SMPS adapter)
    - BTS7960 43A H-Bridge Driver Board  ← CONFIRMED
    - ESP32 WROOM-32 Development Board
    - PC817 2-Channel Optocoupler Board
    - ATC Blade Fuse Holder (7.5A)

  COORDINATE SYSTEM:
    The conveyor sits centered at origin.
    Belt surface is ~Y=0.04m above origin.
    Table surface assumed at Y=0.0 (conveyor sits ON the table).
    All components placed UNDER/BESIDE the conveyor on a virtual shelf.
    Scale: 1 Blender Unit = 1 Meter (real world)
=============================================================================
"""

import bpy
import bmesh
from mathutils import Vector

# ---------------------------------------------------------------------------
# UTILITY FUNCTIONS
# ---------------------------------------------------------------------------

def clear_collection(name):
    """Get or create a collection for hardware components."""
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def new_obj(name, mesh_data, collection):
    """Create object, link to collection, and return it."""
    obj = bpy.data.objects.new(name, mesh_data)
    collection.objects.link(obj)
    return obj


def add_material(name, color_rgba, roughness=0.4, metallic=0.0, emission=None):
    """Create or retrieve a material by name."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = color_rgba
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
        if emission:
            bsdf.inputs["Emission Color"].default_value = emission
            bsdf.inputs["Emission Strength"].default_value = 2.0
    return mat


def apply_mat(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def box_mesh(name, sx, sy, sz):
    """Create a simple box mesh."""
    mesh = bpy.data.meshes.new(name + "_mesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def cyl_mesh(name, radius, depth, segments=16):
    """Create a cylinder mesh."""
    mesh = bpy.data.meshes.new(name + "_mesh")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm,
                          cap_ends=True,
                          cap_tris=False,
                          segments=segments,
                          radius1=radius,
                          radius2=radius,
                          depth=depth)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


# ---------------------------------------------------------------------------
# MATERIALS
# ---------------------------------------------------------------------------

def build_materials():
    mats = {}
    # PCB green
    mats["pcb_green"]      = add_material("HW_PCB_Green",     (0.05, 0.25, 0.05, 1.0), roughness=0.6)
    mats["pcb_blue"]       = add_material("HW_PCB_Blue",      (0.02, 0.08, 0.30, 1.0), roughness=0.6)
    # Heatsink / metal
    mats["aluminium"]      = add_material("HW_Aluminium",     (0.65, 0.67, 0.70, 1.0), roughness=0.3, metallic=1.0)
    mats["dark_metal"]     = add_material("HW_DarkMetal",     (0.15, 0.15, 0.16, 1.0), roughness=0.5, metallic=0.8)
    # Plastics
    mats["black_plastic"]  = add_material("HW_BlackPlastic",  (0.04, 0.04, 0.04, 1.0), roughness=0.8)
    mats["grey_plastic"]   = add_material("HW_GreyPlastic",   (0.30, 0.30, 0.30, 1.0), roughness=0.75)
    mats["white_plastic"]  = add_material("HW_WhitePlastic",  (0.85, 0.85, 0.85, 1.0), roughness=0.7)
    mats["orange_plastic"] = add_material("HW_OrangePlastic", (0.80, 0.35, 0.02, 1.0), roughness=0.65)
    # LEDs
    mats["led_green"]      = add_material("HW_LED_Green",     (0.0, 1.0, 0.0, 1.0),  emission=(0.0, 1.0, 0.0, 1.0))
    mats["led_red"]        = add_material("HW_LED_Red",       (1.0, 0.0, 0.0, 1.0),  emission=(1.0, 0.0, 0.0, 1.0))
    # Chip / IC
    mats["ic_black"]       = add_material("HW_IC_Black",      (0.02, 0.02, 0.02, 1.0), roughness=0.5)
    # Terminal / connector
    mats["connector_black"]= add_material("HW_Connector",     (0.05, 0.05, 0.05, 1.0), roughness=0.7)
    mats["screw_silver"]   = add_material("HW_Screw",         (0.55, 0.55, 0.55, 1.0), roughness=0.2, metallic=1.0)
    return mats

# ---------------------------------------------------------------------------
# HELPER — PARENT AND PLACE
# ---------------------------------------------------------------------------

def place(obj, x, y, z):
    obj.location = (x, y, z)


def set_parent(child, parent):
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()


# ---------------------------------------------------------------------------
# COMPONENT BUILDERS
# ---------------------------------------------------------------------------

def build_psu(col, mats):
    """
    12V 5A SMPS Power Supply Adapter
    Real size: ~150mm × 85mm × 40mm
    Position: Under conveyor table, rear-right
    """
    W, H, D = 0.150, 0.040, 0.085   # Width (X), Height (Y), Depth (Z)

    # Main body
    body = new_obj("HW_PSU_Body", box_mesh("psu_body", W, H, D), col)
    apply_mat(body, mats["white_plastic"])
    place(body, 0.30, -0.05, -0.10)   # right side, below conveyor

    # Vent slots (decorative cuts — modeled as dark strips on side)
    for i in range(4):
        vent = new_obj(f"HW_PSU_Vent_{i}", box_mesh(f"vent_{i}", 0.002, 0.020, 0.012), col)
        apply_mat(vent, mats["dark_metal"])
        vent.location = (
            body.location.x + W * 0.5 + 0.001,
            body.location.y + H * 0.5 - 0.005 - i * 0.008,
            body.location.z
        )
        set_parent(vent, body)

    # Output terminal block (two green screw terminals)
    for i, name in enumerate(["HW_PSU_Term_Pos", "HW_PSU_Term_Neg"]):
        term = new_obj(name, box_mesh(name, 0.010, 0.012, 0.010), col)
        apply_mat(term, mats["connector_black"])
        term.location = (
            body.location.x - W * 0.5 + 0.008 + i * 0.013,
            body.location.y + H * 0.5 + 0.006,
            body.location.z
        )
        # Screw head on top
        screw = new_obj(f"{name}_Screw", cyl_mesh(f"{name}_scr", 0.003, 0.003), col)
        apply_mat(screw, mats["screw_silver"])
        screw.location = (term.location.x, term.location.y + 0.007, term.location.z)
        set_parent(screw, body)

    # Power LED (green)
    led = new_obj("HW_PSU_LED", cyl_mesh("psu_led", 0.003, 0.003, 8), col)
    apply_mat(led, mats["led_green"])
    led.location = (
        body.location.x + W * 0.5 - 0.010,
        body.location.y + H * 0.5 + 0.002,
        body.location.z + 0.015
    )
    set_parent(led, body)

    print("[HW] PSU built.")
    return body


def build_bts7960(col, mats):
    """
    BTS7960 43A H-Bridge Driver Board (Double module)
    Real PCB size: ~70mm × 50mm × 15mm including heatsink chips
    PCB color: Blue (typical for this module)
    Position: Attached to conveyor frame side, near motor
    """
    PCB_W, PCB_H, PCB_D = 0.070, 0.008, 0.050

    # PCB base (blue)
    pcb = new_obj("HW_BTS7960_PCB", box_mesh("bts_pcb", PCB_W, PCB_H, PCB_D), col)
    apply_mat(pcb, mats["pcb_blue"])
    # Mount on left side of conveyor frame, vertical (like wall-mount)
    pcb.location = (-0.30, 0.10, 0.0)

    # --- BTS7960 chips × 2 (large TO-220-7 chips, mounted upright) ---
    for i, cname in enumerate(["HW_BTS7960_Chip_L", "HW_BTS7960_Chip_R"]):
        chip_body = new_obj(cname, box_mesh(cname, 0.010, 0.020, 0.008), col)
        apply_mat(chip_body, mats["ic_black"])
        chip_body.location = (
            pcb.location.x - PCB_W * 0.3 + i * 0.025,
            pcb.location.y + PCB_H * 0.5 + 0.010,
            pcb.location.z
        )
        set_parent(chip_body, pcb)

        # Metal heatsink tab on top of each BTS chip
        hs = new_obj(cname + "_HS", box_mesh(cname + "_hs", 0.010, 0.003, 0.008), col)
        apply_mat(hs, mats["aluminium"])
        hs.location = (
            chip_body.location.x,
            chip_body.location.y + 0.012,
            chip_body.location.z
        )
        set_parent(hs, pcb)

    # --- Motor output terminal (4-pin screw block, side) ---
    terminal_block = new_obj("HW_BTS7960_MotorTerm", box_mesh("bts_term", 0.030, 0.012, 0.010), col)
    apply_mat(terminal_block, mats["connector_black"])
    terminal_block.location = (
        pcb.location.x + PCB_W * 0.5 + 0.005,
        pcb.location.y,
        pcb.location.z
    )
    set_parent(terminal_block, pcb)

    # Screws on terminal
    for i in range(4):
        s = new_obj(f"HW_BTS7960_TScrew_{i}", cyl_mesh(f"bts_ts_{i}", 0.002, 0.003, 6), col)
        apply_mat(s, mats["screw_silver"])
        s.location = (
            terminal_block.location.x,
            terminal_block.location.y + 0.008,
            terminal_block.location.z - 0.010 + i * 0.007
        )
        set_parent(s, pcb)

    # --- 6-pin input signal header (Dupont, connects to optocoupler) ---
    sig_header = new_obj("HW_BTS7960_SigHeader", box_mesh("bts_sighdr", 0.018, 0.008, 0.005), col)
    apply_mat(sig_header, mats["connector_black"])
    sig_header.location = (
        pcb.location.x - PCB_W * 0.4,
        pcb.location.y + PCB_H * 0.5 + 0.004,
        pcb.location.z - 0.010
    )
    set_parent(sig_header, pcb)

    # --- Power LED ---
    led = new_obj("HW_BTS7960_LED", cyl_mesh("bts_led", 0.002, 0.003, 8), col)
    apply_mat(led, mats["led_red"])
    led.location = (
        pcb.location.x + PCB_W * 0.4,
        pcb.location.y + PCB_H * 0.5 + 0.002,
        pcb.location.z + 0.010
    )
    set_parent(led, pcb)

    # VCC + GND power connector (2-pin, left edge)
    pwr_block = new_obj("HW_BTS7960_PwrBlock", box_mesh("bts_pwr", 0.010, 0.010, 0.005), col)
    apply_mat(pwr_block, mats["connector_black"])
    pwr_block.location = (
        pcb.location.x - PCB_W * 0.5 - 0.003,
        pcb.location.y,
        pcb.location.z
    )
    set_parent(pwr_block, pcb)

    print("[HW] BTS7960 H-Bridge built.")
    return pcb


def build_esp32(col, mats):
    """
    ESP32 WROOM-32 Development Board
    Real PCB size: ~48mm × 26mm, WROOM module ~18mm × 25mm
    CH340C USB chip, Type-C port, 38 GPIO pins
    Position: Adjacent to H-Bridge, connected via signal wires
    """
    PCB_W, PCB_H, PCB_D = 0.048, 0.007, 0.026

    # PCB base
    pcb = new_obj("HW_ESP32_PCB", box_mesh("esp_pcb", PCB_W, PCB_H, PCB_D), col)
    apply_mat(pcb, mats["pcb_green"])
    pcb.location = (-0.28, 0.10, 0.065)  # Just above H-Bridge

    # WROOM-32 Metal Module (shielded can) on top center of PCB
    module = new_obj("HW_ESP32_WROOM_Module", box_mesh("wroom_mod", 0.025, 0.004, 0.018), col)
    apply_mat(module, mats["aluminium"])
    module.location = (
        pcb.location.x - 0.003,
        pcb.location.y + PCB_H * 0.5 + 0.002,
        pcb.location.z
    )
    set_parent(module, pcb)

    # WiFi antenna cutout/notch represented as dark strip
    antenna = new_obj("HW_ESP32_Antenna", box_mesh("wroom_ant", 0.005, 0.003, 0.002), col)
    apply_mat(antenna, mats["dark_metal"])
    antenna.location = (
        module.location.x - 0.012,
        module.location.y,
        module.location.z - 0.008
    )
    set_parent(antenna, pcb)

    # Left GPIO header (19 pins)
    for i in range(10):
        pin = new_obj(f"HW_ESP32_PinL_{i}", cyl_mesh(f"esp_pl_{i}", 0.0005, 0.008, 6), col)
        apply_mat(pin, mats["screw_silver"])
        pin.location = (
            pcb.location.x - PCB_W * 0.5 + 0.003,
            pcb.location.y,
            pcb.location.z - PCB_D * 0.4 + i * 0.0025
        )
        set_parent(pin, pcb)

    # Right GPIO header (19 pins)
    for i in range(10):
        pin = new_obj(f"HW_ESP32_PinR_{i}", cyl_mesh(f"esp_pr_{i}", 0.0005, 0.008, 6), col)
        apply_mat(pin, mats["screw_silver"])
        pin.location = (
            pcb.location.x + PCB_W * 0.5 - 0.003,
            pcb.location.y,
            pcb.location.z - PCB_D * 0.4 + i * 0.0025
        )
        set_parent(pin, pcb)

    # USB Type-C port (bottom edge, CH340C)
    usb = new_obj("HW_ESP32_USB_TypeC", box_mesh("esp_usb", 0.009, 0.004, 0.003), col)
    apply_mat(usb, mats["dark_metal"])
    usb.location = (
        pcb.location.x + PCB_W * 0.5 - 0.005,
        pcb.location.y,
        pcb.location.z - PCB_D * 0.5 - 0.001
    )
    set_parent(usb, pcb)

    # EN button
    btn_en = new_obj("HW_ESP32_BTN_EN", box_mesh("esp_bten", 0.004, 0.004, 0.004), col)
    apply_mat(btn_en, mats["grey_plastic"])
    btn_en.location = (
        pcb.location.x + PCB_W * 0.4,
        pcb.location.y + PCB_H * 0.5 + 0.002,
        pcb.location.z - 0.007
    )
    set_parent(btn_en, pcb)

    # BOOT button
    btn_boot = new_obj("HW_ESP32_BTN_BOOT", box_mesh("esp_btboot", 0.004, 0.004, 0.004), col)
    apply_mat(btn_boot, mats["grey_plastic"])
    btn_boot.location = (
        pcb.location.x + PCB_W * 0.4,
        pcb.location.y + PCB_H * 0.5 + 0.002,
        pcb.location.z + 0.007
    )
    set_parent(btn_boot, pcb)

    # Power LED (red)
    led_pwr = new_obj("HW_ESP32_LED_Power", cyl_mesh("esp_ledp", 0.001, 0.002, 6), col)
    apply_mat(led_pwr, mats["led_red"])
    led_pwr.location = (
        pcb.location.x - PCB_W * 0.4,
        pcb.location.y + PCB_H * 0.5 + 0.002,
        pcb.location.z
    )
    set_parent(led_pwr, pcb)

    # TX LED (blue)
    led_tx = new_obj("HW_ESP32_LED_TX", cyl_mesh("esp_ledtx", 0.001, 0.002, 6), col)
    apply_mat(led_tx, mats["led_green"])
    led_tx.location = (
        pcb.location.x - PCB_W * 0.4,
        pcb.location.y + PCB_H * 0.5 + 0.002,
        pcb.location.z + 0.005
    )
    set_parent(led_tx, pcb)

    print("[HW] ESP32 WROOM-32 built.")
    return pcb


def build_pc817(col, mats):
    """
    PC817 2-Channel Optocoupler Isolation Board
    Real size: ~33mm × 14mm PCB, 2× PC817 DIP-4 chips
    Position: Between ESP32 and BTS7960 (signal chain)
    """
    PCB_W, PCB_H, PCB_D = 0.033, 0.005, 0.014

    pcb = new_obj("HW_PC817_PCB", box_mesh("opto_pcb", PCB_W, PCB_H, PCB_D), col)
    apply_mat(pcb, mats["pcb_green"])
    pcb.location = (-0.26, 0.10, 0.055)  # Between ESP32 and H-Bridge

    # 2× PC817 DIP-4 chips (black, small)
    for i in range(2):
        ic = new_obj(f"HW_PC817_IC_{i}", box_mesh(f"opto_ic_{i}", 0.006, 0.004, 0.005), col)
        apply_mat(ic, mats["ic_black"])
        ic.location = (
            pcb.location.x - 0.007 + i * 0.016,
            pcb.location.y + PCB_H * 0.5 + 0.002,
            pcb.location.z
        )
        set_parent(ic, pcb)

        # 4 pins per chip
        for p in range(4):
            pin = new_obj(f"HW_PC817_IC_{i}_Pin_{p}", cyl_mesh(f"opto_pin_{i}_{p}", 0.0003, 0.005, 4), col)
            apply_mat(pin, mats["screw_silver"])
            pin.location = (
                ic.location.x - 0.002 + (p % 2) * 0.004,
                pcb.location.y,
                ic.location.z - 0.001 + (p // 2) * 0.003
            )
            set_parent(pin, pcb)

    # 2× 1kΩ resistors (axial, horizontal)
    for i in range(2):
        res_body = new_obj(f"HW_PC817_R_{i}", cyl_mesh(f"opto_r_{i}", 0.001, 0.006, 8), col)
        apply_mat(res_body, mats["grey_plastic"])
        res_body.rotation_euler = (0, 1.5708, 0)  # Lay flat
        res_body.location = (
            pcb.location.x - 0.010 + i * 0.016,
            pcb.location.y + PCB_H * 0.5 + 0.002,
            pcb.location.z + 0.004
        )
        set_parent(res_body, pcb)

    # 4-pin input header
    in_hdr = new_obj("HW_PC817_InHeader", box_mesh("opto_inh", 0.012, 0.004, 0.004), col)
    apply_mat(in_hdr, mats["connector_black"])
    in_hdr.location = (
        pcb.location.x - PCB_W * 0.45,
        pcb.location.y + PCB_H * 0.5 + 0.002,
        pcb.location.z
    )
    set_parent(in_hdr, pcb)

    # 4-pin output header
    out_hdr = new_obj("HW_PC817_OutHeader", box_mesh("opto_outh", 0.012, 0.004, 0.004), col)
    apply_mat(out_hdr, mats["connector_black"])
    out_hdr.location = (
        pcb.location.x + PCB_W * 0.45,
        pcb.location.y + PCB_H * 0.5 + 0.002,
        pcb.location.z
    )
    set_parent(out_hdr, pcb)

    print("[HW] PC817 Optocoupler built.")
    return pcb


def build_fuse_holder(col, mats):
    """
    ATC Blade Fuse Holder — Waterproof Inline, 18AWG, 7.5A
    Cylindrical barrel: ~60mm long × 14mm diameter
    Position: On 12V rail between PSU and H-Bridge
    """
    length = 0.060
    radius = 0.007

    # Fuse barrel body (black plastic)
    barrel = new_obj("HW_Fuse_Barrel", cyl_mesh("fuse_brl", radius, length, 16), col)
    apply_mat(barrel, mats["black_plastic"])
    barrel.rotation_euler = (0, 1.5708, 0)  # Lay horizontal
    barrel.location = (0.02, 0.08, -0.03)

    # Interior fuse window (orange/translucent to show blade)
    window = new_obj("HW_Fuse_Window", cyl_mesh("fuse_win", radius * 0.7, length * 0.4, 12), col)
    apply_mat(window, mats["orange_plastic"])
    window.rotation_euler = (0, 1.5708, 0)
    window.location = barrel.location
    set_parent(window, barrel)

    # Metal end caps (silver)
    for i, name in enumerate(["HW_Fuse_Cap_L", "HW_Fuse_Cap_R"]):
        cap = new_obj(name, cyl_mesh(name, radius * 1.05, 0.005, 16), col)
        apply_mat(cap, mats["aluminium"])
        cap.rotation_euler = (0, 1.5708, 0)
        cap.location = (
            barrel.location.x + length * 0.5 - 0.003 - i * length,
            barrel.location.y,
            barrel.location.z
        )
        set_parent(cap, barrel)

    print("[HW] Fuse Holder built.")
    return barrel


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    # Deselect all
    bpy.ops.object.select_all(action='DESELECT')

    # Collection
    col = clear_collection("HW_Components")

    # Materials
    mats = build_materials()

    # Build each component
    psu      = build_psu(col, mats)
    bts7960  = build_bts7960(col, mats)
    esp32    = build_esp32(col, mats)
    pc817    = build_pc817(col, mats)
    fuse     = build_fuse_holder(col, mats)

    print("=" * 60)
    print("[HW] Hardware Layer Build COMPLETE!")
    print(f"      PSU:       {psu.location}")
    print(f"      BTS7960:   {bts7960.location}")
    print(f"      ESP32:     {esp32.location}")
    print(f"      PC817:     {pc817.location}")
    print(f"      Fuse:      {fuse.location}")
    print("=" * 60)


main()
