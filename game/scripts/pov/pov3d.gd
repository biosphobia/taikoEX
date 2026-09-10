class_name Pov3D
extends RefCounted
## Small helpers shared by the 3D view: materials, and loading a user model.

static func plastic_material(colour: Color) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = colour
	material.roughness = 0.55
	material.metallic = 0.05
	return material


static func glowing_material(colour: Color, alpha: float = 1.0) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(colour.r, colour.g, colour.b, alpha)
	material.emission_enabled = true
	material.emission = colour
	material.emission_energy_multiplier = 2.2 * alpha
	material.roughness = 0.2
	if alpha < 1.0:
		material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	return material


static func unshaded_material(colour: Color, alpha: float = 1.0) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.vertex_color_use_as_albedo = true
	material.albedo_color = Color(colour.r, colour.g, colour.b, alpha)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	return material


static func translucent_material(colour: Color, alpha: float) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(colour.r, colour.g, colour.b, alpha)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	return material


## Load a .glb the player dropped into data/models, or null if it will not load.
static func load_model(path: String) -> Node3D:
	var state := GLTFState.new()
	var document := GLTFDocument.new()
	var bytes := FileAccess.get_file_as_bytes(path)
	if bytes.is_empty() or document.append_from_buffer(bytes, path.get_base_dir(), state) != OK:
		push_warning("Could not load model " + path)
		return null
	var scene := document.generate_scene(state)
	return scene as Node3D
