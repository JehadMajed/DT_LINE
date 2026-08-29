import bpy
import math

# ==============================================================================
# BLENDER PYTHON SCRIPT: INDUSTRIAL ROOM GENERATOR
# ==============================================================================
# This script builds a closed, realistic factory control room / industrial bay
# around the conveyor belt, complete with walls, concrete floor, structural
# steel columns, ceiling, and overhead glowing LED panels.
# ==============================================================================

def generate_industrial_room():
    print("--------------------------------------------------")
    print("Initializing Industrial Room Generation...")
    
    # 1. Clean up any existing room elements to avoid duplication
    room_objects = [obj for obj in bpy.data.objects if "Room_" in obj.name]
    for obj in room_objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    print(f"Removed {len(room_objects)} old room objects.")

    # Dimensions (snug fit around the conveyor)
    room_w = 1.2   # Width (X-axis) - slightly wider to clear conveyor edges
    room_d = 1.2   # Depth (Y-axis)
    room_h = 0.8   # Height (Z-axis)
    floor_z = -0.13 # Positioned safely below conveyor feet to prevent Z-fighting

    # ==========================================================================
    # MATERIALS CREATION
    # ==========================================================================
    def get_or_create_material(name, color, roughness=0.8, metallic=0.0, emission=False, emission_color=(1,1,1,1), emission_strength=1.0):
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = bpy.data.materials.new(name=name)
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            bsdf = nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs['Base Color'].default_value = color
                bsdf.inputs['Roughness'].default_value = roughness
                bsdf.inputs['Metallic'].default_value = metallic
                if emission:
                    if 'Emission Color' in bsdf.inputs:
                        bsdf.inputs['Emission Color'].default_value = emission_color
                        bsdf.inputs['Emission Strength'].default_value = emission_strength
                    elif 'Emission' in bsdf.inputs:
                        bsdf.inputs['Emission'].default_value = emission_color
        return mat

    floor_mat = get_or_create_material("Room_Floor_Mat", (0.1, 0.11, 0.12, 1.0), roughness=0.4) # Polished dark concrete
    wall_mat = get_or_create_material("Room_Wall_Mat", (0.05, 0.06, 0.07, 1.0), roughness=0.9)   # Dark acoustic panels
    steel_mat = get_or_create_material("Room_Steel_Mat", (0.15, 0.16, 0.17, 1.0), roughness=0.3, metallic=0.8) # Steel girders
    light_mat = get_or_create_material("Room_Light_Mat", (1,1,1,1), roughness=1.0, emission=True, emission_color=(0.0, 0.8, 1.0, 1.0), emission_strength=5.0) # Cyber Cyan Glow Light

    # ==========================================================================
    # 2. CREATE FLOOR (Lowered by 0.01 to prevent Z-fighting with walls)
    # ==========================================================================
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 0, floor_z - 0.01))
    floor = bpy.context.object
    floor.name = "Room_Floor"
    floor.scale = (room_w + 0.1, room_d + 0.1, 1.0)
    floor.data.materials.append(floor_mat)

    # ==========================================================================
    # 3. CREATE WALLS (Outer box)
    # ==========================================================================
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, floor_z + (room_h / 2)))
    walls = bpy.context.object
    walls.name = "Room_Walls"
    walls.scale = (room_w, room_d, room_h)
    walls.data.materials.append(wall_mat)
    
    # Flip normals of walls so lighting works internally
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.flip_normals()
    bpy.ops.object.mode_set(mode='OBJECT')

    # ==========================================================================
    # 4. CREATE STEEL CORNER PILLARS (Columns - offset slightly inwards)
    # ==========================================================================
    offset_x = (room_w / 2) - 0.06 # Move 1cm inwards to prevent Z-fighting with walls
    offset_y = (room_d / 2) - 0.06
    corners = [
        (offset_x, offset_y),
        (-offset_x, offset_y),
        (offset_x, -offset_y),
        (-offset_x, -offset_y)
    ]
    
    for i, (cx, cy) in enumerate(corners):
        bpy.ops.mesh.primitive_cube_add(size=0.1, location=(cx, cy, floor_z + (room_h / 2)))
        col = bpy.context.object
        col.name = f"Room_Column_{i}"
        col.scale = (1.0, 1.0, room_h * 10.0) # Scale tall
        col.data.materials.append(steel_mat)

    # ==========================================================================
    # 5. CREATE CEILING INDUSTRIAL LED LIGHT PANELS (Offset below ceiling)
    # ==========================================================================
    light_positions = [
        (0, 0.4, floor_z + room_h - 0.02),
        (0, -0.4, floor_z + room_h - 0.02)
    ]
    for i, (lx, ly, lz) in enumerate(light_positions):
        bpy.ops.mesh.primitive_plane_add(size=0.2, location=(lx, ly, lz))
        light = bpy.context.object
        light.name = f"Room_CeilingLight_{i}"
        light.scale = (4.0, 0.4, 1.0) # Rectangular strip
        light.data.materials.append(light_mat)

    print("SUCCESS: Industrial room successfully built around conveyor belt!")
    print("--------------------------------------------------")

# Run the generation script
generate_industrial_room()
