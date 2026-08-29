"""
=============================================================================
  DIGITAL TWIN — SYSTEM WIRING BUILDER v5.0 (Local Parenting Fixed)
  File: build_wiring.py
  Run from: Blender Scripting Tab (Text Editor → Run Script)

  Builds and parents all wires relative to "DT_System_Root" so they translate,
  rotate, and scale correctly with the conveyor belt and Control Box.
=============================================================================
"""

import bpy
import bmesh
from mathutils import Vector
import math

def clear_collection(name):
    """Retrieve collection and delete all existing objects inside it."""
    if name in bpy.data.collections:
        col = bpy.data.collections[name]
        for obj in list(col.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        return col
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def add_material(name, color_rgb, roughness=0.8):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color_rgb, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = 0.0
    return mat


def make_wire(name, col, mat, points_xyz, bevel_depth=0.0006, parent_obj=None):
    curve_data = bpy.data.curves.new(name + "_crv", type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.bevel_depth = bevel_depth
    curve_data.bevel_resolution = 4
    curve_data.fill_mode = 'FULL'

    spline = curve_data.splines.new('BEZIER')
    spline.bezier_points.add(len(points_xyz) - 1)

    for i, (x, y, z) in enumerate(points_xyz):
        pt = spline.bezier_points[i]
        pt.co = (x, y, z)
        pt.handle_left_type  = 'AUTO'
        pt.handle_right_type = 'AUTO'

    obj = bpy.data.objects.new(name, curve_data)
    col.objects.link(obj)
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    if parent_obj:
        obj.parent = parent_obj
    return obj


def main():
    col = clear_collection("HW_Wiring")
    bpy.ops.object.select_all(action='DESELECT')

    # Find system root to parent correctly
    root_obj = bpy.data.objects.get("DT_System_Root")
    if not root_obj:
        for o in bpy.data.objects:
            if o.name.startswith("DT_System_Root"):
                root_obj = o
                break

    # Materials
    mats = {
        "Red":    add_material("MTR_Wire_Red",    (0.80, 0.02, 0.02)),
        "Black":  add_material("MTR_Wire_Black",  (0.04, 0.04, 0.04)),
        "Yellow": add_material("MTR_Wire_Yellow", (0.85, 0.70, 0.02)),
        "Green":  add_material("MTR_Wire_Green",  (0.02, 0.60, 0.02)),
        "Blue":   add_material("MTR_Wire_Blue",   (0.02, 0.08, 0.70)),
        "White":  add_material("MTR_Wire_White",  (0.90, 0.90, 0.90)),
        "Gray":   add_material("MTR_Wire_Gray",   (0.40, 0.40, 0.42))
    }

    # Coordinates relative to local conveyor space (exact specs)
    MOTOR_POS = Vector((-0.1768, -0.0190, -0.0428))
    BOX_POS   = Vector((-0.087786, 0.29454, -0.041048))
    ADAP_POS  = Vector((0.040, 0.350, -0.060))
    PLUG_POS  = Vector((0.140, 0.400, -0.066))
    # DT_System_Root world Z = +0.0699 → floor local Z = -0.0699
    FLOOR_Z   = -0.0699

    # ---------------------------------------------------------------------------
    # 1. 6 MOTOR WIRES -> Right Side Glands (gland 0)
    # ---------------------------------------------------------------------------
    GLAND_MOTOR = BOX_POS + Vector((0.044, -0.008, 0.0))
    radius_motor = 0.0004

    wire_configs = [
        ("Red",    mats["Red"],    -0.005),
        ("Black",  mats["Black"],  -0.003),
        ("Yellow", mats["Yellow"], -0.001),
        ("Green",  mats["Green"],   0.001),
        ("Blue",   mats["Blue"],    0.003),
        ("White",  mats["White"],   0.005)
    ]

    for name_suffix, mat, x_offset in wire_configs:
        # pt0: exits motor cap bottom — AUTO (smooth start)
        p0 = (MOTOR_POS.x + x_offset, MOTOR_POS.y - 0.002, MOTOR_POS.z - 0.012)
        # pt1: near-floor beside motor leg — VECTOR (no overshoot)
        p1 = (MOTOR_POS.x + x_offset * 1.5, MOTOR_POS.y + 0.010, FLOOR_Z + 0.012)
        # pt2: run along floor toward box — VECTOR
        p2 = (GLAND_MOTOR.x + 0.020, GLAND_MOTOR.y + x_offset * 2.0, FLOOR_Z + 0.008)
        # pt3: enters gland — AUTO (smooth finish)
        p3 = (GLAND_MOTOR.x, GLAND_MOTOR.y + x_offset, GLAND_MOTOR.z)

        curve_data = bpy.data.curves.new(f"HW_Wire_Motor_{name_suffix}_crv", type='CURVE')
        curve_data.dimensions = '3D'
        curve_data.bevel_depth = radius_motor
        curve_data.bevel_resolution = 4
        curve_data.fill_mode = 'FULL'
        spline = curve_data.splines.new('BEZIER')
        spline.bezier_points.add(3)
        bp = spline.bezier_points

        bp[0].co = p0;  bp[0].handle_left_type = 'AUTO';   bp[0].handle_right_type = 'AUTO'
        bp[1].co = p1;  bp[1].handle_left_type = 'VECTOR'; bp[1].handle_right_type = 'VECTOR'
        bp[2].co = p2;  bp[2].handle_left_type = 'VECTOR'; bp[2].handle_right_type = 'VECTOR'
        bp[3].co = p3;  bp[3].handle_left_type = 'AUTO';   bp[3].handle_right_type = 'AUTO'

        wo = bpy.data.objects.new(f"HW_Wire_Motor_{name_suffix}", curve_data)
        col.objects.link(wo)
        wo.data.materials.clear()
        wo.data.materials.append(mat)
        if root_obj:
            wo.parent = root_obj

    # ---------------------------------------------------------------------------
    # 2. DC POWER CORD (Control Box bottom -> Power Adapter)
    # ---------------------------------------------------------------------------
    GLAND_POWER = BOX_POS + Vector((0.0, 0.0, -0.020))
    make_wire("HW_Wire_DC_Power", col, mats["Black"], [
        GLAND_POWER,
        # Drop down just to floor level
        (GLAND_POWER.x, GLAND_POWER.y, FLOOR_Z + 0.008),
        # Run along floor to adapter
        (GLAND_POWER.x + 0.060, GLAND_POWER.y + 0.055, FLOOR_Z + 0.006),
        (ADAP_POS.x - 0.010, ADAP_POS.y - 0.040, FLOOR_Z + 0.006),
        # Rise up into adapter body
        (ADAP_POS.x, ADAP_POS.y - 0.045, ADAP_POS.z)
    ], bevel_depth=0.0015, parent_obj=root_obj)

    # ---------------------------------------------------------------------------
    # 3. AC MAINS CORD (Power Adapter -> US 3-Prong Plug)
    # ---------------------------------------------------------------------------
    make_wire("HW_Wire_AC_Mains", col, mats["Gray"], [
        (ADAP_POS.x, ADAP_POS.y + 0.045, ADAP_POS.z),
        # Lay flat on floor from adapter
        (ADAP_POS.x + 0.010, ADAP_POS.y + 0.060, FLOOR_Z + 0.006),
        # Run along floor toward plug
        (PLUG_POS.x - 0.030, PLUG_POS.y - 0.030, FLOOR_Z + 0.006),
        # Enter plug body
        (PLUG_POS.x, PLUG_POS.y - 0.014, PLUG_POS.z)
    ], bevel_depth=0.0020, parent_obj=root_obj)

    print("=" * 60)
    print("[WIRING] Floor-clamped wires — no geometry below world Z=0")
    print("=" * 60)

main()

