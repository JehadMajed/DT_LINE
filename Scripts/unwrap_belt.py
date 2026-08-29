import bpy
import bmesh

# ==============================================================================
# BLENDER PYTHON SCRIPT: CONVEYOR BELT UV UNWRAPPER (CONTEXT OVERRIDE FIX)
# ==============================================================================
# When running operators from the Text Editor, Blender sometimes fails because 
# the active context is the text editor, not the 3D viewport. This script 
# safely overrides the context to the 3D Viewport to perform the unwrap.
# ==============================================================================

def get_3d_viewport_context():
    # Find the 3D Viewport area and region
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        return {'window': window, 'screen': window.screen, 'area': area, 'region': region}
    return None

def unwrap_conveyor_belt():
    print("--------------------------------------------------")
    print("Starting Conveyor Belt UV Unwrap Process...")
    
    obj = bpy.data.objects.get("Main_Belt")
    
    if not obj or obj.type != 'MESH':
        print("ERROR: 'Main_Belt' object not found or is not a mesh.")
        return False
        
    # Make sure we're in Object mode to reset state
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
        
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    
    # Enter Edit Mode
    bpy.ops.object.mode_set(mode='EDIT')
    
    # Get BMesh
    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    
    # Ensure Face select mode
    bm.select_mode = {'FACE'}
    
    # Find a quad to be active
    active_face = None
    for face in bm.faces:
        if len(face.verts) == 4:
            active_face = face
            break
            
    if not active_face:
        print("ERROR: Could not find any quad faces on Main_Belt.")
        bpy.ops.object.mode_set(mode='OBJECT')
        return False
        
    # Select all faces and set active face
    for face in bm.faces:
        face.select = True
        
    bm.faces.active = active_face
    
    # Update the edit mesh!
    bmesh.update_edit_mesh(me)
    
    # Create the context override for the 3D Viewport
    ctx_override = get_3d_viewport_context()
    
    if not ctx_override:
        print("ERROR: Could not find a 3D Viewport in your Blender UI.")
        print("Please make sure you have at least one 3D Viewport open!")
        bpy.ops.object.mode_set(mode='OBJECT')
        return False

    print(f"Active quad face index {active_face.index} selected. Running Follow Active Quads in 3D Viewport Context...")
    
    try:
        # For Blender 3.2+ we use temp_override
        with bpy.context.temp_override(**ctx_override):
            bpy.ops.uv.follow_active_quads(mode='LENGTH_AVERAGE')
        print("SUCCESS: Conveyor belt UVs unwrapped into a continuous linear ribbon!")
    except Exception as e:
        print(f"Failed during Follow Active Quads: {e}")
        
    # Return to Object Mode
    bpy.ops.object.mode_set(mode='OBJECT')
    print("--------------------------------------------------")
    return True

unwrap_conveyor_belt()
