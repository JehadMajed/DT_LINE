# Implementation Plan - Option A: Chain Texture Scrolling Animation

This plan describes how to implement the chain movement using texture scrolling (Option A). This method is highly performant, handles transparency beautifully, and bypasses the glTF limitation on Blender curve modifiers.

## User Review Required

> [!IMPORTANT]
> **Blender Action Required**: You will need to add a flat loop mesh (representing the chain path) in Blender and export it with a specific name.
> We recommend name: **`Chain_Loop`**.
>
> **Procedural Chain Texture**: We propose generating the chain texture dynamically in code using HTML5 Canvas inside Babylon.js. This ensures high-resolution rendering, zero loading latency, and easy speed/look customization.

## Open Questions

- What mesh name would you like to use for the chain loop in Blender? (We default to `Chain_Loop`).
- Do you prefer a standard roller chain look (metal links connected by pins) or a plastic chain look? (We default to a premium metallic roller chain).

---

## Proposed Changes

### 1. Blender Modifications (By User)
1. In your Blender file, create a thin, flat loop mesh that wraps around the sprockets/pulleys where the chain runs (similar to how the conveyor belt wraps around the main rollers).
2. Unwrap the UV coordinates of this loop so that:
   - The length of the loop maps along the **U axis** (horizontal).
   - The width of the loop maps along the **V axis** (vertical).
3. Name this mesh **`Chain_Loop`**.
4. Export the updated assembly to `assets/Conveyor_Twin_v1.glb`.

---

### 2. Application Logic Modifications

#### [MODIFY] [app.js](file:///c:/Users/Jehad/OneDrive/Desktop/Digital%20Twin%20for%20Production%20Line/app.js)

We will update the scene loading and render loop to handle the new `Chain_Loop` mesh:

* **Material Setup on Load**:
  When `Chain_Loop` is loaded, we will apply a transparent PBR material with a dynamically drawn procedural roller-chain texture.
  
* **Procedural Texture Generation**:
  We will draw the chain links using 2D canvas operations:
  1. Metallic outer links (rounded capsules).
  2. Darker inner link gaps.
  3. Connecting pins/rollers.
  4. Enable alpha rendering so the background of the chain is fully transparent:
     `pbr.albedoTexture.hasAlpha = true;`
     `pbr.useAlphaFromAlbedoTexture = true;`

* **Scroll Animation**:
  We will add the chain's material to the scrolling list and update its `uOffset` in sync with the RPM speed.

---

## Verification Plan

### Automated Tests
- Build verification and JavaScript lint checks.

### Manual Verification
1. Export the GLB from Blender with `Chain_Loop` mesh.
2. Start the local server and run the application.
3. Verify that the chain:
   - Appears transparent between the links.
   - Moves smoothly around the sprockets/pulleys in the correct direction.
