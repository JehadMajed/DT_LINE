import bpy
import bmesh
import math
import mathutils

# ==============================================================================
# INDUSTRIAL GRADE SCREW REBUILDER (SOCKET HEAD CAP SCREW - SHCS)
# ==============================================================================
# Rebuilds screws in Blender with realistic Hex socket cap profiles.
# Automatically detects orientation, size, and origin offsets of existing meshes.
# ==============================================================================

SCREW_SPECS = {
    "M3": {
        "shaft_d": 3.0,
        "head_d": 5.5,
        "head_h": 3.0,
        "cross_w": 2.5,
        "cross_d": 0.65,
        "socket_depth": 1.3,
        "head_top_chamfer": 0.3,
        "under_head_fillet": 0.15,
        "shaft_length": 8.0
    },
    "M4": {
        "shaft_d": 4.0,
        "head_d": 7.0,
        "head_h": 4.0,
        "cross_w": 3.0,
        "cross_d": 0.8,
        "socket_depth": 2.0,
        "head_top_chamfer": 0.4,
        "under_head_fillet": 0.2,
        "shaft_length": 10.0
    }
}

MM_TO_METERS = 0.001

def detect_screw_orientation(obj):
    """
    Analyzes the vertex distribution of the original mesh to detect:
    1. Longest local axis ('X', 'Y', or 'Z')
    2. Alignment direction (1 if head is at positive end, -1 if negative)
    3. Offset ratio of origin relative to the screw bounding box
    """
    verts = obj.data.vertices
    if not verts:
        return 'Z', 1, 0.5
        
    scale = obj.scale
    # Calculate local positions including scale (handles negative/non-uniform scaling)
    scaled_cos = [mathutils.Vector((v.co.x * scale.x, v.co.y * scale.y, v.co.z * scale.z)) for v in verts]
    
    xs = [co.x for co in scaled_cos]
    ys = [co.y for co in scaled_cos]
    zs = [co.z for co in scaled_cos]
    
    range_x = max(xs) - min(xs)
    range_y = max(ys) - min(ys)
    range_z = max(zs) - min(zs)
    
    ranges = [range_x, range_y, range_z]
    max_range = max(ranges)
    axis_idx = ranges.index(max_range)
    axis = ['X', 'Y', 'Z'][axis_idx]
    
    min_val = min([co[axis_idx] for co in scaled_cos])
    max_val = max([co[axis_idx] for co in scaled_cos])
    orig_range = max_val - min_val
    mid_val = (min_val + max_val) / 2
    
    # Calculate radius perpendicular to the longest axis to detect head vs shaft halves
    perp_indices = [i for i in range(3) if i != axis_idx]
    
    lower_half_dists = []
    upper_half_dists = []
    
    for co in scaled_cos:
        val = co[axis_idx]
        dist = math.sqrt(co[perp_indices[0]]**2 + co[perp_indices[1]]**2)
        if val < mid_val:
            lower_half_dists.append(dist)
        else:
            upper_half_dists.append(dist)
            
    avg_lower = sum(lower_half_dists) / len(lower_half_dists) if lower_half_dists else 0
    avg_upper = sum(upper_half_dists) / len(upper_half_dists) if upper_half_dists else 0
    
    if avg_upper > avg_lower:
        direction = 1
        ratio = (0.0 - min_val) / orig_range if orig_range > 1e-6 else 0.5
    else:
        direction = -1
        ratio = (max_val - 0.0) / orig_range if orig_range > 1e-6 else 0.5
        
    return axis, direction, ratio

def build_cross_head_screw_mesh(obj, spec):
    # 1. Detect orientation and origin placement ratio from original mesh
    axis, direction, ratio = detect_screw_orientation(obj)
    
    # 2. Reset object scale to 1.0 so physical measurements match Blender meters 1:1
    obj.scale = (1.0, 1.0, 1.0)
    
    bm = bmesh.new()

    # Convert specs to meters
    shaft_d = spec["shaft_d"] * MM_TO_METERS
    head_d = spec["head_d"] * MM_TO_METERS
    head_h = spec["head_h"] * MM_TO_METERS
    cross_w = spec["cross_w"] * MM_TO_METERS
    cross_d = spec["cross_d"] * MM_TO_METERS
    socket_depth = spec["socket_depth"] * MM_TO_METERS
    head_top_chamfer = spec["head_top_chamfer"] * MM_TO_METERS
    under_head_fillet = spec["under_head_fillet"] * MM_TO_METERS
    shaft_length = spec["shaft_length"] * MM_TO_METERS

    # Calculate origin offset to match the old mesh anchoring
    # New mesh goes from Z = -shaft_length to Z = head_h
    new_range = shaft_length + head_h
    shift_z = - (-shaft_length + ratio * new_range)

    # 3. Create 2D profile coordinates in the X-Z plane
    profile_verts = []
    
    # Shaft bottom center
    profile_verts.append((0.0, -shaft_length + shift_z))
    # Shaft bottom outer
    profile_verts.append((shaft_d / 2, -shaft_length + shift_z))
    # Shaft wall up to fillet start
    profile_verts.append((shaft_d / 2, -under_head_fillet + shift_z))

    # Fillet arc (from theta = 180 to 90 degrees)
    fillet_cx = shaft_d / 2 + under_head_fillet
    fillet_cz = -under_head_fillet
    fillet_steps = 6
    for i in range(1, fillet_steps):
        theta = math.pi - (i / fillet_steps) * (math.pi / 2)
        x = fillet_cx + under_head_fillet * math.cos(theta)
        z = fillet_cz + under_head_fillet * math.sin(theta)
        profile_verts.append((x, z + shift_z))

    # Fillet end / Under-head start
    profile_verts.append((shaft_d / 2 + under_head_fillet, 0.0 + shift_z))
    # Head under outer corner
    profile_verts.append((head_d / 2, 0.0 + shift_z))
    # Head side up to top chamfer start
    profile_verts.append((head_d / 2, head_h - head_top_chamfer + shift_z))
    # Head top chamfer end
    profile_verts.append((head_d / 2 - head_top_chamfer, head_h + shift_z))
    # Head top center
    profile_verts.append((0.0, head_h + shift_z))

    # Create vertices in bmesh
    verts = [bm.verts.new((x, 0.0, z)) for x, z in profile_verts]

    # Create edges along profile
    edges = []
    for i in range(len(verts) - 1):
        edges.append(bm.edges.new((verts[i], verts[i+1])))

    # Spin the profile 360 degrees around Z axis (32 steps for smooth look)
    bmesh.ops.spin(
        bm,
        geom=edges + verts,
        cent=(0.0, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
        angle=2 * math.pi,
        steps=32,
        use_merge=True
    )

    # Weld overlapping vertices at the center and along the seam
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)

    # Recalculate face normals outward
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)

    # Rotate geometry to align with original screw axis
    src_dir = mathutils.Vector((0, 0, 1))
    if axis == 'X':
        tar_dir = mathutils.Vector((direction, 0, 0))
    elif axis == 'Y':
        tar_dir = mathutils.Vector((0, direction, 0))
    else:
        tar_dir = mathutils.Vector((0, 0, direction))

    rot_q = src_dir.rotation_difference(tar_dir)
    rot_mat = rot_q.to_matrix().to_4x4()
    
    bmesh.ops.rotate(bm, cent=(0.0, 0.0, 0.0), matrix=rot_mat, verts=bm.verts)

    # Push BMesh to a new Blender mesh
    mesh_data = bpy.data.meshes.new(obj.name + "_mesh")
    bm.to_mesh(mesh_data)
    bm.free()

    # Link new mesh and delete old one
    old_mesh = obj.data
    obj.data = mesh_data
    if old_mesh and old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)

    # 4. Create Phillips cross slot cutter
    cutter_bm = bmesh.new()
    
    # Extend cutter 1mm above the head to prevent coplanar boolean issues
    cutter_h = socket_depth + 0.001
    cutter_z = head_h - socket_depth / 2 + 0.0005 + shift_z
    
    # Cross arm 1 (elongated in Y, thin in X)
    c1 = bmesh.ops.create_cube(cutter_bm, size=1.0)
    for v in c1["verts"]:
        v.co.x *= cross_d
        v.co.y *= cross_w
        v.co.z = (v.co.z * cutter_h) + cutter_z
        
    # Cross arm 2 (elongated in X, thin in Y)
    c2 = bmesh.ops.create_cube(cutter_bm, size=1.0)
    for v in c2["verts"]:
        v.co.x *= cross_w
        v.co.y *= cross_d
        v.co.z = (v.co.z * cutter_h) + cutter_z

    # Taper the bottom of the cross cutter for realistic look
    taper_threshold = head_h - (socket_depth * 0.2) + shift_z
    for v in cutter_bm.verts:
        if v.co.z < taper_threshold:
            # Linearly interpolate scale down to the bottom
            bottom_z = head_h - socket_depth + shift_z
            height_pct = (v.co.z - bottom_z) / (taper_threshold - bottom_z) if (taper_threshold - bottom_z) > 1e-6 else 1.0
            scale_factor = 0.2 + 0.8 * height_pct
            v.co.x *= scale_factor
            v.co.y *= scale_factor

    # Rotate cutter to align with screw direction
    bmesh.ops.rotate(cutter_bm, cent=(0.0, 0.0, 0.0), matrix=rot_mat, verts=cutter_bm.verts)
    
    cutter_mesh = bpy.data.meshes.new("cross_cutter_tmp")
    cutter_bm.to_mesh(cutter_mesh)
    cutter_bm.free()
    
    cutter_obj = bpy.data.objects.new("cross_cutter_tmp", cutter_mesh)
    bpy.context.collection.objects.link(cutter_obj)
    
    # Copy world matrix instead of parenting to avoid dependency loops
    cutter_obj.matrix_world = obj.matrix_world

    # Apply Boolean Difference
    mod = obj.modifiers.new(name="CrossCut", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter_obj
    mod.solver = 'EXACT'

    # Update viewport and evaluate the modifiers using depsgraph
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = bpy.data.meshes.new_from_object(obj_eval)

    # Swap mesh to the evaluated mesh with cut applied
    old_mesh = obj.data
    obj.data = mesh_eval
    if old_mesh and old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)

    # Clean up modifiers and temporary cutter object
    obj.modifiers.clear()
    bpy.data.objects.remove(cutter_obj, do_unlink=True)
    bpy.data.meshes.remove(cutter_mesh)

    # Apply Smooth Shading
    for p in obj.data.polygons:
        p.use_smooth = True
        
    # Apply Edge Split modifier to keep sharp angles sharp
    has_split_mod = any(m.type == 'EDGE_SPLIT' for m in obj.modifiers)
    if not has_split_mod:
        split_mod = obj.modifiers.new(name="EdgeSplit", type='EDGE_SPLIT')
        split_mod.split_angle = math.radians(30)
        split_mod.use_edge_angle = True
            
    # Apply Steel Material
    screw_mat = bpy.data.materials.get("Galvanized_Steel_Screw")
    if not screw_mat:
        screw_mat = bpy.data.materials.new(name="Galvanized_Steel_Screw")
        screw_mat.use_nodes = True
        bsdf = screw_mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs['Base Color'].default_value = (0.6, 0.62, 0.65, 1.0)
            bsdf.inputs['Metallic'].default_value = 0.95
            bsdf.inputs['Roughness'].default_value = 0.22
            
    if not obj.data.materials:
        obj.data.materials.append(screw_mat)
    else:
        obj.data.materials[0] = screw_mat

    obj.data.update()
    return obj

def rebuild_single_object(obj):
    if not obj or obj.type != 'MESH':
        return False
        
    name_lower = obj.name.lower()
    is_m3 = "m3" in name_lower or "motor" in name_lower
    spec = SCREW_SPECS["M3"] if is_m3 else SCREW_SPECS["M4"]
    size_label = "M3" if is_m3 else "M4"
    
    try:
        build_cross_head_screw_mesh(obj, spec)
        print(f"Rebuilt {obj.name} as {size_label} Phillips Pan-head Cap Screw.")
        return True
    except Exception as e:
        print(f"Failed to rebuild {obj.name}: {e}")
        return False

# ==============================================================================
# BLENDER PANEL & OPERATOR INTERFACE
# ==============================================================================

class OBJECT_OT_rebuild_selected_screws(bpy.types.Operator):
    bl_idname = "object.rebuild_selected_screws"
    bl_label = "Rebuild Selected Screws"
    bl_description = "Rebuild only the selected mesh objects that look like screws"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        selected_objs = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected_objs:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}
            
        success_count = 0
        for obj in selected_objs:
            if rebuild_single_object(obj):
                success_count += 1
                
        self.report({'INFO'}, f"Successfully rebuilt {success_count} selected screws.")
        return {'FINISHED'}

class OBJECT_OT_rebuild_all_screws(bpy.types.Operator):
    bl_idname = "object.rebuild_all_screws"
    bl_label = "Rebuild All Screws in Scene"
    bl_description = "Search the entire scene and rebuild all objects with 'screw' or 'bolt' in their name"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        # Gather all mesh objects with "screw" or "bolt" in name (case-insensitive)
        screw_objs = []
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                name_l = obj.name.lower()
                if "screw" in name_l or "bolt" in name_l:
                    screw_objs.append(obj)
                    
        if not screw_objs:
            self.report({'WARNING'}, "No screw objects found in the scene.")
            return {'CANCELLED'}
            
        # Ensure we are in object mode
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
            
        success_count = 0
        for obj in screw_objs:
            if rebuild_single_object(obj):
                success_count += 1
                
        self.report({'INFO'}, f"Successfully rebuilt {success_count}/{len(screw_objs)} screws in the scene.")
        return {'FINISHED'}

class VIEW3D_PT_screw_rebuilder(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Digital Twin'
    bl_label = "Screw Rebuilder"
    
    def draw(self, context):
        layout = self.layout
        
        col = layout.column(align=True)
        col.label(text="Rebuild Options:")
        col.operator("object.rebuild_selected_screws", icon='MESH_CYLINDER')
        col.operator("object.rebuild_all_screws", icon='MODIFIER')
        
        # Info Box
        col.separator()
        box = layout.box()
        box.label(text="Specifications Built:")
        box.label(text="M3 (Motor): Shaft 3mm, Head 5.5mm")
        box.label(text="M4 (Standard): Shaft 4mm, Head 7mm")
        box.label(text="Socket: Cross (+) Profile")

# Register/unregister components
classes = (
    OBJECT_OT_rebuild_selected_screws,
    OBJECT_OT_rebuild_all_screws,
    VIEW3D_PT_screw_rebuilder,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    # If run directly, register the classes first
    register()
    
    # Then attempt to rebuild the active selection
    if bpy.context.active_object:
        rebuild_single_object(bpy.context.active_object)
    else:
        print("Script loaded. Use the Sidebar panel 'Digital Twin' in the 3D Viewport to run.")
