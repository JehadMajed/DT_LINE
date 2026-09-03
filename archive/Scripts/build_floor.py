"""
=============================================================================
  DIGITAL TWIN — FLOOR PLANE BUILDER
  File: build_floor.py
  Run FIRST from: Blender Scripting Tab

  Creates a flat floor plane at world Z=0 (the real ground).
=============================================================================
"""
import bpy, bmesh
from mathutils import Vector

def add_material(name, color, roughness=0.9):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
    return mat

# Remove old floor if exists
if "Room_Floor" in bpy.data.objects:
    bpy.data.objects.remove(bpy.data.objects["Room_Floor"], do_unlink=True)

# Create flat plane
mesh = bpy.data.meshes.new("floor_m")
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.0)
bm.to_mesh(mesh)
bm.free()

floor = bpy.data.objects.new("Room_Floor", mesh)
bpy.context.scene.collection.objects.link(floor)

# Scale to 1.5m x 1.5m and place at world Z = 0
floor.scale = (1.5, 1.5, 1.0)
floor.location = (0.0, 0.2, 0.0)  # Centered under the conveyor

mat_floor = add_material("Floor_Concrete", (0.25, 0.25, 0.26), roughness=0.95)
floor.data.materials.clear()
floor.data.materials.append(mat_floor)

print("=" * 50)
print("[FLOOR] Room floor placed at world Z = 0")
print("=" * 50)
