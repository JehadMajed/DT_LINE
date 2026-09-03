"""
=============================================================================
  DIGITAL TWIN — PROXIMITY SENSOR BUILDER (Local Parenting Fixed)
  File: build_proximity_sensor.py
  Run from: Blender Scripting Tab (Text Editor → Run Script)

  Builds and parents the sensor, bracket, and wires to "DT_System_Root" so
  they align perfectly with the conveyor belt and compact Control Box.
=============================================================================
"""

import bpy
import bmesh
from mathutils import Vector
import math

def clear_collection(name):
    if name in bpy.data.collections:
        col = bpy.data.collections[name]
        for obj in list(col.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        return col
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def new_obj(name, mesh, col):
    obj = bpy.data.objects.new(name, mesh)
    col.objects.link(obj)
    return obj


def add_material(name, color, roughness=0.5, metallic=0.0, emission=None, alpha=1.0):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color, alpha)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
        if alpha < 1.0:
            mat.blend_method = 'BLEND'
            bsdf.inputs["Alpha"].default_value = alpha
        if emission:
            if "Emission Color" in bsdf.inputs:
                bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
            elif "Emission" in bsdf.inputs:
                bsdf.inputs["Emission"].default_value = (*emission, 1.0)
            
            if "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = 2.0
    return mat


def apply_mat(obj, mat):
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def make_cylinder(name, radius, depth, segments=24):
    mesh = bpy.data.meshes.new(name + "_m")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False,
                          segments=segments, radius1=radius, radius2=radius, depth=depth)
    for v in bm.verts:
        v.co = Vector((v.co.x, v.co.z, v.co.y))
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def make_box(name, sx, sy, sz):
    mesh = bpy.data.meshes.new(name + "_m")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


# Materials
mat_yellow_body    = add_material("SN_BarrelYellow",  (0.92, 0.70, 0.05), roughness=0.4)
mat_optical_purple = add_material("SN_OpticalPurple",  (0.22, 0.02, 0.18), roughness=0.1, alpha=0.95)
mat_lens_dark      = add_material("SN_LensDark",      (0.02, 0.02, 0.03), roughness=0.2)
mat_nut_black      = add_material("SN_NutBlack",      (0.05, 0.05, 0.06), roughness=0.6)
mat_metal_chrome   = add_material("SN_Chrome",        (0.70, 0.70, 0.72), roughness=0.2, metallic=1.0)
mat_led_green      = add_material("SN_LED_Green",     (0.0, 1.0, 0.1), roughness=0.2, emission=(0.0, 1.0, 0.1))
mat_led_red        = add_material("SN_LED_Red",       (1.0, 0.05, 0.0), roughness=0.2, emission=(1.0, 0.05, 0.0))
mat_bracket_plate  = add_material("SN_BracketPlate",  (0.70, 0.70, 0.72), roughness=0.3, metallic=0.9)

mat_wire_brown     = add_material("SN_Wire_Brown",    (0.25, 0.12, 0.02), roughness=0.7)
mat_wire_blue      = add_material("SN_Wire_Blue",     (0.02, 0.08, 0.50), roughness=0.7)
mat_wire_black     = add_material("SN_Wire_Black",    (0.04, 0.04, 0.04), roughness=0.7)

col = clear_collection("HW_Sensor")
bpy.ops.object.select_all(action='DESELECT')

# Find system root to parent correctly
root_obj = bpy.data.objects.get("DT_System_Root")
if not root_obj:
    for o in bpy.data.objects:
        if o.name.startswith("DT_System_Root"):
            root_obj = o
            break

# Exact local coordinates relative to the system root
SENSOR_RADIUS  = 0.009
SENSOR_LENGTH  = 0.065
SENSOR_X = -0.14562
SENSOR_Y =  0.10121
SENSOR_Z = 0.023043 # Calculated to sit perfectly on the flange (Z = 0.012543)

# Main yellow barrel
barrel = new_obj("HW_Sensor_Barrel", make_cylinder("sn_barrel", SENSOR_RADIUS, SENSOR_LENGTH, 32), col)
apply_mat(barrel, mat_yellow_body)
if root_obj:
    barrel.parent = root_obj
barrel.location = (SENSOR_X, SENSOR_Y, SENSOR_Z)
barrel.rotation_euler = (0, 0, 0)

# Face
face = new_obj("HW_Sensor_Face", make_cylinder("sn_face", SENSOR_RADIUS * 0.9, 0.003, 32), col)
apply_mat(face, mat_optical_purple)
face.parent = barrel
face.location = (0.0, -SENSOR_LENGTH * 0.5, 0.0)

# Lenses
for i, offset_x in enumerate([-0.003, 0.003]):
    lens = new_obj(f"HW_Sensor_Lens_{i}", make_cylinder(f"sn_lens_{i}", 0.0025, 0.001, 16), col)
    apply_mat(lens, mat_lens_dark)
    lens.parent = barrel
    lens.location = (offset_x, -SENSOR_LENGTH * 0.5 - 0.001, 0.0)

# Nuts
for i in range(2):
    nut = new_obj(f"HW_Sensor_Nut_{i}", make_cylinder(f"sn_nut_{i}", SENSOR_RADIUS + 0.003, 0.005, 6), col)
    apply_mat(nut, mat_nut_black)
    nut.parent = barrel
    nut.location = (0.0, -SENSOR_LENGTH * 0.2 + i * 0.025, 0.0)

# LEDs
led_ring_green = new_obj("HW_Sensor_LED_Green", make_cylinder("sn_led_g", SENSOR_RADIUS + 0.001, 0.003, 24), col)
apply_mat(led_ring_green, mat_led_green)
led_ring_green.parent = barrel
led_ring_green.location = (0.0, SENSOR_LENGTH * 0.45, 0.0)

led_ring_red = new_obj("HW_Sensor_LED_Red", make_cylinder("sn_led_r", SENSOR_RADIUS + 0.0015, 0.002, 24), col)
apply_mat(led_ring_red, mat_led_red)
led_ring_red.parent = barrel
led_ring_red.location = (0.0, SENSOR_LENGTH * 0.47, 0.0)
led_ring_red.hide_viewport = True

# ============================================================
# L-BRACKET — calculated from user-specified flange Z location (0.012543)
# ============================================================
FLANGE_H = 0.003
FLANGE_Z = 0.012543
POST_HEIGHT = 0.030                                   # from rail up to flange
POST_Z = FLANGE_Z - POST_HEIGHT * 0.5               # center of post

bracket_base = new_obj("HW_Sensor_Bracket_Base", make_box("sn_br_base", 0.025, 0.003, POST_HEIGHT), col)
apply_mat(bracket_base, mat_bracket_plate)
if root_obj:
    bracket_base.parent = root_obj
bracket_base.location = (SENSOR_X, 0.056, POST_Z)

bracket_flange = new_obj("HW_Sensor_Bracket_Flange", make_box("sn_br_flange", 0.025, 0.045, FLANGE_H), col)
apply_mat(bracket_flange, mat_bracket_plate)
if root_obj:
    bracket_flange.parent = root_obj
bracket_flange.location = (SENSOR_X, 0.0785, FLANGE_Z)

bolt = new_obj("HW_Bracket_Bolt", make_cylinder("sn_br_bolt", 0.0025, 0.006, 6), col)
apply_mat(bolt, mat_metal_chrome)
if root_obj:
    bolt.parent = root_obj
bolt.location = (SENSOR_X, 0.054, POST_Z - POST_HEIGHT * 0.3)

# ============================================================
# WIRE ROUTING — floor-aware (world floor = local Z = -0.0699)
# ============================================================
# From extractor data:
#   Sensor barrel local: X=-0.1456, Y=0.1012, Z=0.0295
#   Control Box local:   X=-0.0878, Y=0.2945, Z=-0.0410
#   Gland_1 (prox wires) on box: local offset = (+0.044, +0.008, 0.0)
#   => Gland_1 world local: X=-0.0438, Y=0.3025, Z=-0.0410
#   Floor = local Z = -0.0699  (never go below this!)

FLOOR_Z  = -0.0699
GLAND_SN = (-0.0438, 0.3025, -0.0410)  # Sensor gland on control box

# Exit from rear of barrel (Y axis is barrel axis — positive Y = rear)
cable_exit_y = SENSOR_Y + SENSOR_LENGTH * 0.5   # = 0.1012 + 0.0325 = 0.1337

wire_definitions = [
    ("HW_Sensor_Wire_Brown", mat_wire_brown, -0.002),
    ("HW_Sensor_Wire_Blue",  mat_wire_blue,   0.000),
    ("HW_Sensor_Wire_Black", mat_wire_black,  0.002)
]

for wname, wmat, x_offset in wire_definitions:
    curve_data = bpy.data.curves.new(wname + "_crv", type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.bevel_depth = 0.0006
    curve_data.bevel_resolution = 4
    curve_data.fill_mode = 'FULL'

    spline = curve_data.splines.new('BEZIER')
    spline.bezier_points.add(4)
    pts = spline.bezier_points

    # pt0 — exit rear of barrel (AUTO: smooth start)
    pts[0].co = (SENSOR_X + x_offset, cable_exit_y, SENSOR_Z)
    pts[0].handle_left_type  = 'AUTO'
    pts[0].handle_right_type = 'AUTO'

    # pt1 — drape behind bracket post (AUTO: smooth arc)
    pts[1].co = (SENSOR_X + x_offset, cable_exit_y + 0.018, SENSOR_Z - 0.010)
    pts[1].handle_left_type  = 'AUTO'
    pts[1].handle_right_type = 'AUTO'

    # pt2 — touch near-floor level beside frame leg
    #        VECTOR handles → curve goes straight in/out, no overshoot below FLOOR_Z
    drop_z = max(SENSOR_Z - 0.060, FLOOR_Z + 0.008)
    pts[2].co = (SENSOR_X + x_offset, cable_exit_y + 0.025, drop_z)
    pts[2].handle_left_type  = 'VECTOR'
    pts[2].handle_right_type = 'VECTOR'

    # pt3 — run along near-floor straight to box area (VECTOR: no dip)
    pts[3].co = (GLAND_SN[0] + x_offset, GLAND_SN[1] - 0.028, FLOOR_Z + 0.006)
    pts[3].handle_left_type  = 'VECTOR'
    pts[3].handle_right_type = 'VECTOR'

    # pt4 — rise to enter control box gland (AUTO: smooth termination)
    pts[4].co = (GLAND_SN[0] + x_offset, GLAND_SN[1], GLAND_SN[2])
    pts[4].handle_left_type  = 'AUTO'
    pts[4].handle_right_type = 'AUTO'

    wire_obj = bpy.data.objects.new(wname, curve_data)
    col.objects.link(wire_obj)
    apply_mat(wire_obj, wmat)
    if root_obj:
        wire_obj.parent = root_obj

print("=" * 60)
print("[SENSOR] Bracket rests under barrel. Wires floor-clamped.")
print("=" * 60)

