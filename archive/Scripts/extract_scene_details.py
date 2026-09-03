"""
=============================================================================
  DIGITAL TWIN — BLENDER SCENE EXTRACTOR
  File: extract_scene_details.py
  Run from: Blender Scripting Tab (Text Editor → Run Script)

  Extracts the exact world and local transforms of the conveyor, the floor,
  and related objects, then writes them to 'scene_details.txt'.
=============================================================================
"""

import bpy
import os

# Output file path
desktop_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else "C:/Users/Jehad/OneDrive/Desktop/Digital Twin for Production Line"
output_path = os.path.join(desktop_dir, "scene_details.txt")

with open(output_path, "w") as f:
    f.write("=== BLENDER SCENE DETAILS ===\n\n")
    
    # 1. Global Scene Settings
    f.write("--- Global Settings ---\n")
    f.write(f"Unit System: {bpy.context.scene.unit_settings.system}\n")
    f.write(f"Length Unit: {bpy.context.scene.unit_settings.length_unit}\n")
    f.write(f"Scale Length: {bpy.context.scene.unit_settings.scale_length}\n\n")

    # 2. Selected / Important Objects
    targets = [
        "DT_System_Root",
        "Room_Floor",
        "HW_Sensor_Barrel",
        "HW_Sensor_Bracket_Flange",
        "HW_ControlBox_Casing",
        "MTR_04_EncoderCap"
    ]
    
    f.write("--- Target Objects Transforms ---\n")
    for name in targets:
        # Search exact or prefix matching
        match_obj = None
        for obj in bpy.data.objects:
            if obj.name.startswith(name):
                match_obj = obj
                break
                
        if match_obj:
            f.write(f"Object: {match_obj.name}\n")
            f.write(f"  Parent: {match_obj.parent.name if match_obj.parent else 'None'}\n")
            f.write(f"  Local Location:  {match_obj.location}\n")
            f.write(f"  Local Rotation:  {match_obj.rotation_euler}\n")
            f.write(f"  Local Scale:     {match_obj.scale}\n")
            # Calculate world position
            world_pos = match_obj.matrix_world.translation
            f.write(f"  World Location:  {world_pos}\n")
            f.write("-" * 30 + "\n")
        else:
            f.write(f"Object: {name} (NOT FOUND)\n")
            f.write("-" * 30 + "\n")
            
    # 3. All objects in collections starting with HW_
    f.write("\n--- Hardware Collections Objects ---\n")
    for col_name in ["HW_Sensor", "HW_ControlBox", "HW_Wiring"]:
        col = bpy.data.collections.get(col_name)
        if col:
            f.write(f"Collection: {col.name}\n")
            for obj in col.objects:
                f.write(f"  - {obj.name} (Parent: {obj.parent.name if obj.parent else 'None'}, Local Loc: {obj.location})\n")
        else:
            f.write(f"Collection: {col_name} (NOT FOUND)\n")

print(f"Scene details successfully extracted to: {output_path}")
