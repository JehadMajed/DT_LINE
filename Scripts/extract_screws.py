import bpy
import json
import os
import math
import mathutils

def extract_screw_data():
    # Detect the directory of the current blender file or default to the script's directory/desktop
    if bpy.data.is_saved:
        output_dir = os.path.dirname(bpy.data.filepath)
    else:
        output_dir = os.path.expanduser("~/Desktop")
        
    output_path = os.path.join(output_dir, "extracted_screws.json")
    
    print(f"Starting extraction. Output will be saved to: {output_path}")
    
    screws_data = []
    
    # We look for objects with these keywords in their name
    keywords = ["screw", "bolt", "fastener", "nut", "m3", "m4", "m5", "m6"]
    
    all_meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH']
    
    for obj in all_meshes:
        name_lower = obj.name.lower()
        # Check if the object name matches any of our keywords
        is_screw = any(kw in name_lower for kw in keywords)
        
        if is_screw:
            # Get world matrix components
            world_matrix = obj.matrix_world
            loc, rot, scale = world_matrix.decompose()
            
            # Convert rotation to euler degrees for easier human inspection
            euler_deg = [round(math.degrees(a), 3) for a in rot.to_euler()]
            
            # Bounding box dimensions
            dimensions = [round(d, 6) for d in obj.dimensions]
            
            # Identify standard specifications based on dimensions or name
            inferred_spec = "M4" # Default guess
            if "m3" in name_lower or "motor" in name_lower:
                inferred_spec = "M3"
            elif "m5" in name_lower:
                inferred_spec = "M5"
            elif "m6" in name_lower:
                inferred_spec = "M6"
            else:
                # Guess based on bounding box width (typically X/Y dimension in meters)
                # 3mm shaft diameter -> M3, 4mm -> M4 etc.
                approx_d = min(obj.dimensions.x, obj.dimensions.y) * 1000.0 # in mm
                if approx_d < 3.5:
                    inferred_spec = "M3"
                elif approx_d < 4.5:
                    inferred_spec = "M4"
                elif approx_d < 5.5:
                    inferred_spec = "M5"
                else:
                    inferred_spec = f"Custom (Approx D: {approx_d:.1f}mm)"

            screw_info = {
                "name": obj.name,
                "parent": obj.parent.name if obj.parent else None,
                "inferred_spec": inferred_spec,
                "dimensions_m": {
                    "x": round(obj.dimensions.x, 6),
                    "y": round(obj.dimensions.y, 6),
                    "z": round(obj.dimensions.z, 6),
                },
                "local_transform": {
                    "position": [round(c, 6) for c in obj.location],
                    "rotation_euler_deg": [round(math.degrees(a), 3) for a in obj.rotation_euler],
                    "scale": [round(s, 6) for s in obj.scale]
                },
                "world_transform": {
                    "position": [round(c, 6) for c in loc],
                    "rotation_euler_deg": euler_deg,
                    "quaternion": [round(q, 6) for q in rot],
                    "scale": [round(s, 6) for s in scale]
                },
                "mesh_info": {
                    "vertices_count": len(obj.data.vertices),
                    "polygons_count": len(obj.data.polygons)
                }
            }
            
            screws_data.append(screw_info)
            
    # Write to JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(screws_data, f, indent=4)
        
    print(f"Extraction complete! Extracted {len(screws_data)} screw parts.")
    return output_path, screws_data

if __name__ == "__main__":
    extract_screw_data()
