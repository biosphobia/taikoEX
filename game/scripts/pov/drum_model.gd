class_name DrumModel
extends Node3D
## The virtual drum, built from whatever pads the tracker has loaded.
##
## Each pad becomes a disc (or a ring) lying in its own plane, so this works
## for the single-drum layout, the four-pad layout, or anything you invent by
## editing tracker_config.json.  A `drum.glb` in data/models replaces the body.

const SEGMENTS := 64

var pads: Array = []
var _pad_nodes: Dictionary = {}      # pad id -> MeshInstance3D
var _flash: Dictionary = {}          # pad id -> 0..1
var _body: Node3D
var _show_body := true


func rebuild(new_pads: Array) -> void:
	pads = new_pads
	for child in get_children():
		child.queue_free()
	_pad_nodes.clear()
	_build_body()
	for pad in pads:
		var inner := float(pad.get("inner_radius", 0.0))
		var outer := float(pad.get("radius", 0.2))
		var node := MeshInstance3D.new()
		node.mesh = _ring_mesh(inner, outer)
		node.material_override = _pad_material(pad, 0.0)
		# Stack the rings a millimetre apart so they never fight for the same
		# pixels; a taiko's rim really does sit slightly above its skin.
		var lift := 0.001 * (1 + _pad_nodes.size())
		var pad_transform := _pad_transform(pad)
		node.transform = pad_transform.translated(pad_transform.basis.y * lift)
		add_child(node)
		_pad_nodes[str(pad.get("id", ""))] = node
		if inner > 0.0:
			# A raised lip around the outside, so the rim reads as the edge of
			# a drum rather than a plate lying on the floor.
			var lip := MeshInstance3D.new()
			var torus := TorusMesh.new()
			torus.inner_radius = outer * 0.985
			torus.outer_radius = outer * 1.02
			torus.rings = SEGMENTS
			lip.mesh = torus
			lip.transform = pad_transform
			lip.material_override = Pov3D.plastic_material(GameSkin.color("drum_rim"))
			add_child(lip)
		var label := Label3D.new()
		label.text = str(pad.get("name", pad.get("id", "")))
		label.font_size = 40
		label.pixel_size = 0.0007
		label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		label.modulate = GameSkin.color("text_dim")
		# A ring has nothing in the middle to label, so put its name on the edge.
		# Labels go on the near edge so they never sit under a controller.
		var edge := (inner + outer) / 2.0 if inner > 0.0 else outer * 0.55
		label.position = (pad_transform.origin + pad_transform.basis.z * edge
				+ pad_transform.basis.y * 0.03)
		add_child(label)


func _build_body() -> void:
	if not _show_body or pads.is_empty():
		return
	var custom_path := Paths.data_dir().path_join("models/drum.glb")
	if FileAccess.file_exists(custom_path):
		_body = Pov3D.load_model(custom_path)
		if _body != null:
			add_child(_body)
			return
	# A shallow barrel under the widest pad, so the drum reads as an object
	# sitting in the room rather than a floating disc.
	var widest := 0.0
	var centre := Vector3.ZERO
	for pad in pads:
		widest = max(widest, float(pad.get("radius", 0.2)))
		var c: Array = pad.get("center", [0, 0, 0])
		centre += Vector3(float(c[0]), float(c[1]), float(c[2]))
	centre /= max(1, pads.size())
	var barrel := MeshInstance3D.new()
	var mesh := CylinderMesh.new()
	mesh.top_radius = widest * 0.93
	mesh.bottom_radius = widest * 0.80
	mesh.height = 0.30
	mesh.radial_segments = SEGMENTS
	barrel.mesh = mesh
	barrel.position = centre - Vector3(0, 0.152, 0)
	barrel.material_override = Pov3D.plastic_material(GameSkin.color("drum_rim"))
	add_child(barrel)
	_body = barrel


func _pad_transform(pad: Dictionary) -> Transform3D:
	var c: Array = pad.get("center", [0, 0, 0])
	var n: Array = pad.get("normal", [0, 1, 0])
	var normal := Vector3(float(n[0]), float(n[1]), float(n[2])).normalized()
	if normal.length() < 0.5:
		normal = Vector3.UP
	# Build a basis whose Y axis is the pad normal; the disc is drawn in XZ.
	var helper := Vector3.FORWARD if absf(normal.dot(Vector3.FORWARD)) < 0.9 else Vector3.RIGHT
	var x_axis := helper.cross(normal).normalized()
	var z_axis := normal.cross(x_axis).normalized()
	return Transform3D(Basis(x_axis, normal, z_axis), Vector3(float(c[0]), float(c[1]), float(c[2])))


func _pad_material(pad: Dictionary, flash: float) -> StandardMaterial3D:
	# The face takes the pale colour of drum skin, the rim the darker wood of
	# the body, each tinted towards the note colour it stands for so a glance
	# tells you which is which.
	var is_face: bool = pad.get("kind", "don") == "don"
	# The face takes the pale colour of drum skin, the rim the darker wood of
	# the body, each tinted towards the note colour it stands for.
	var base := GameSkin.color("drum_face" if is_face else "drum_rim")
	# Only a hint of the note colour on the rim: cyan over wood turns it grey,
	# and the rim should still read as the side of a drum.  The flash when it is
	# struck carries the rest.
	base = base.lerp(GameSkin.color("don" if is_face else "ka"), 0.22 if is_face else 0.12)
	var lit := base.lerp(GameSkin.color("don" if is_face else "ka"), flash * 0.45)
	# Unshaded, so the skin's colours come out exactly as chosen instead of
	# being tinted by whatever the room lighting and the sphere glows do.
	var material := Pov3D.translucent_material(lit, 0.97)
	if flash > 0.01:
		material.emission_enabled = true
		material.emission = GameSkin.color("don" if is_face else "ka")
		material.emission_energy_multiplier = flash * 0.55
	return material


## A flat annulus in the XZ plane (inner radius 0 gives a plain disc).
func _ring_mesh(inner: float, outer: float) -> ArrayMesh:
	var vertices := PackedVector3Array()
	var normals := PackedVector3Array()
	for step in range(SEGMENTS + 1):
		var angle := TAU * step / SEGMENTS
		var direction := Vector3(cos(angle), 0, sin(angle))
		vertices.append(direction * inner)
		vertices.append(direction * outer)
		normals.append(Vector3.UP)
		normals.append(Vector3.UP)
	var indices := PackedInt32Array()
	for step in range(SEGMENTS):
		var a := step * 2
		indices.append_array([a, a + 1, a + 3, a, a + 3, a + 2])
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = vertices
	arrays[Mesh.ARRAY_NORMAL] = normals
	arrays[Mesh.ARRAY_INDEX] = indices
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return mesh


func flash(pad_id: String) -> void:
	_flash[pad_id] = 1.0


func _process(delta: float) -> void:
	for pad_id in _flash.keys():
		_flash[pad_id] = maxf(0.0, _flash[pad_id] - delta * 5.5)
	for pad in pads:
		var pad_id := str(pad.get("id", ""))
		var node: MeshInstance3D = _pad_nodes.get(pad_id)
		if node:
			node.material_override = _pad_material(pad, _flash.get(pad_id, 0.0))
