"""
=============================================================================
  DIGITAL TWIN — CONTROL BOX & POWER ADAPTER BUILDER (Local Parenting Fixed)
  File: build_control_box.py
  Run from: Blender Scripting Tab (Text Editor → Run Script)

  Builds and parents all objects relative to "DT_System_Root" so they snap
  perfectly to the conveyor belt, matching its position and scale.
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


def add_material(name, color, roughness=0.4, metallic=0.0, emission=None):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
        bsdf.inputs["Metallic"].default_value = metallic
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


def make_box(name, sx, sy, sz):
    mesh = bpy.data.meshes.new(name + "_m")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def make_cylinder(name, radius, depth, segments=16):
    mesh = bpy.data.meshes.new(name + "_m")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False,
                          segments=segments, radius1=radius, radius2=radius, depth=depth)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


# Materials
mat_steel    = add_material("CB_EnclosureSteel",   (0.22, 0.22, 0.24), roughness=0.3, metallic=0.9)
mat_door     = add_material("CB_EnclosureDoor",    (0.18, 0.18, 0.20), roughness=0.4, metallic=0.9)
mat_adapter  = add_material("CB_AdapterPlastic",   (0.04, 0.04, 0.04), roughness=0.7)
mat_prongs   = add_material("CB_USPlugProngs",     (0.80, 0.70, 0.30), roughness=0.2, metallic=1.0)
mat_gland    = add_material("CB_GlandPlastic",     (0.05, 0.05, 0.05), roughness=0.6)
mat_green    = add_material("CB_LED_Green",        (0.0, 1.0, 0.1), emission=(0.0, 1.0, 0.1))
mat_blue     = add_material("CB_LED_Blue",         (0.0, 0.5, 1.0), emission=(0.0, 0.5, 1.0))
mat_yellow   = add_material("CB_LED_Yellow",       (1.0, 0.8, 0.0), emission=(1.0, 0.8, 0.0))

col = clear_collection("HW_ControlBox")
bpy.ops.object.select_all(action='DESELECT')

# Find system root to parent correctly
root_obj = bpy.data.objects.get("DT_System_Root")
if not root_obj:
    # Try alternate name if version suffix is added
    for o in bpy.data.objects:
        if o.name.startswith("DT_System_Root"):
            root_obj = o
            break

# Floor coordinate in DT_System_Root local space.
# DT_System_Root is at world Z = +0.0699m, so world floor (Z=0) = local Z = -0.0699
FLOOR_Z = -0.0699

# ===========================================================================
# 1. COMPACT CONTROL BOX (logic boards enclosure)
# ===========================================================================
BOX_W, BOX_D, BOX_H = 0.080, 0.035, 0.040
# Sits underneath the frame (exact coordinates from user viewport)
BOX_X = -0.087786
BOX_Y =  0.29454
BOX_Z = -0.041048

case = new_obj("HW_ControlBox_Casing", make_box("cb_case", BOX_W, BOX_D, BOX_H), col)
apply_mat(case, mat_steel)
if root_obj:
    case.parent = root_obj
case.location = (BOX_X, BOX_Y, BOX_Z)

# Top cover door (parented locally to case)
door = new_obj("HW_ControlBox_Door", make_box("cb_door", BOX_W + 0.002, BOX_D + 0.002, 0.003), col)
apply_mat(door, mat_door)
door.parent = case
door.location = (0.0, 0.0, BOX_H * 0.5 + 0.001)

# LEDs on the top cover pointing up
led_names = ["Power", "Status", "Fault"]
led_mats  = [mat_green, mat_blue, mat_yellow]
for i, (name, mat) in enumerate(zip(led_names, led_mats)):
    led = new_obj(f"HW_ControlBox_LED_{name}", make_cylinder(f"cb_led_{name}", 0.002, 0.003, 12), col)
    apply_mat(led, mat)
    led.rotation_euler = (0, 0, 0)
    led.parent = case
    led.location = (-0.020 + i * 0.015, 0.0, BOX_H * 0.5 + 0.002)

# Cable glands on the right face pointing to the right
for i in range(2):
    gland = new_obj(f"HW_ControlBox_Gland_{i}", make_cylinder(f"cb_gland_{i}", 0.004, 0.008, 12), col)
    apply_mat(gland, mat_gland)
    gland.rotation_euler = (0, math.radians(90), 0)
    gland.parent = case
    gland.location = (BOX_W * 0.5 + 0.004, -0.008 + i * 0.016, 0.0)


# ===========================================================================
# 2. POWER SUPPLY ADAPTER BRICK (local floor coordinates)
# ===========================================================================
ADAP_W, ADAP_D, ADAP_H = 0.050, 0.090, 0.030
ADAP_X =  0.040
ADAP_Y =  0.350
ADAP_Z = FLOOR_Z + ADAP_H * 0.5

adap_brick = new_obj("HW_Power_Adapter", make_box("cb_adap", ADAP_W, ADAP_D, ADAP_H), col)
apply_mat(adap_brick, mat_adapter)
if root_obj:
    adap_brick.parent = root_obj
adap_brick.location = (ADAP_X, ADAP_Y, ADAP_Z)

# LED on adapter
adap_led = new_obj("HW_Power_Adapter_LED", make_cylinder("cb_adap_led", 0.0015, 0.002, 8), col)
apply_mat(adap_led, mat_green)
adap_led.parent = adap_brick
adap_led.location = (-0.015, 0.030, ADAP_H * 0.5 + 0.001)


# ===========================================================================
# 3. US 3-PRONG POWER PLUG
# ===========================================================================
PLUG_W, PLUG_D, PLUG_H = 0.018, 0.028, 0.018
PLUG_X =  0.140
PLUG_Y =  0.400
PLUG_Z = FLOOR_Z + PLUG_H * 0.5

plug_body = new_obj("HW_US_Plug_Body", make_box("cb_plug_b", PLUG_W, PLUG_D, PLUG_H), col)
apply_mat(plug_body, mat_adapter)
if root_obj:
    plug_body.parent = root_obj
plug_body.location = (PLUG_X, PLUG_Y, PLUG_Z)

# Prongs
for i, offset_x in enumerate([-0.004, 0.004]):
    prong = new_obj(f"HW_US_Plug_Prong_{i}", make_box(f"cb_prong_{i}", 0.0015, 0.014, 0.005), col)
    apply_mat(prong, mat_prongs)
    prong.parent = plug_body
    prong.location = (offset_x, PLUG_D * 0.5 + 0.007, 0.003)

g_prong = new_obj("HW_US_Plug_Prong_Gnd", make_cylinder("cb_g_prong", 0.0012, 0.015, 8), col)
apply_mat(g_prong, mat_prongs)
g_prong.rotation_euler = (math.radians(90), 0, 0)
g_prong.parent = plug_body
g_prong.location = (0.0, PLUG_D * 0.5 + 0.0075, -0.004)

print("=" * 60)
print("[CONTROL BOX] Parented and built locally.")
print("=" * 60)
