class_name ControllerModel
extends Node3D
## A PS Move drawn where the tracker says it is, pointing where its gyro says.
##
## The handle points along the controller's own axis, so the model tips and
## rolls exactly as the real one in your hand does.  Drop a `controller.glb`
## into data/models to replace the built-in shape; it should be modelled with
## the handle along +Y and the sphere at the top, which is how the tracker
## describes it.

const SPHERE_RADIUS := 0.0225
const HANDLE_LENGTH := 0.16
const HANDLE_RADIUS := 0.021

var controller_id := 0
var colour := Color.MAGENTA
var visible_to_camera := false
var has_orientation := false

var _sphere: MeshInstance3D
var _handle: MeshInstance3D
var _glow: OmniLight3D
var _trail: ImmediateMesh
var _trail_points: Array[Vector3] = []
var _custom: Node3D


func _init(id: int, led_colour: Color) -> void:
	controller_id = id
	colour = led_colour


func _ready() -> void:
	var custom_path := Paths.data_dir().path_join("models/controller.glb")
	if FileAccess.file_exists(custom_path):
		_custom = Pov3D.load_model(custom_path)
	if _custom != null:
		add_child(_custom)
	else:
		_build_default_shape()
	_glow = OmniLight3D.new()
	_glow.light_color = colour
	_glow.light_energy = 1.1
	_glow.omni_range = 0.7
	_glow.position = Vector3(0, HANDLE_LENGTH, 0)
	add_child(_glow)
	var trail_instance := MeshInstance3D.new()
	_trail = ImmediateMesh.new()
	trail_instance.mesh = _trail
	trail_instance.material_override = Pov3D.unshaded_material(colour, 0.5)
	trail_instance.top_level = true          # the trail lives in world space
	add_child(trail_instance)


func _build_default_shape() -> void:
	_handle = MeshInstance3D.new()
	var handle_mesh := CylinderMesh.new()
	handle_mesh.top_radius = HANDLE_RADIUS
	handle_mesh.bottom_radius = HANDLE_RADIUS * 1.15
	handle_mesh.height = HANDLE_LENGTH
	_handle.mesh = handle_mesh
	_handle.position = Vector3(0, HANDLE_LENGTH / 2.0, 0)
	_handle.material_override = Pov3D.plastic_material(Color(0.10, 0.10, 0.12))
	add_child(_handle)

	var trigger := MeshInstance3D.new()
	var trigger_mesh := BoxMesh.new()
	trigger_mesh.size = Vector3(HANDLE_RADIUS * 1.4, 0.03, HANDLE_RADIUS * 0.8)
	trigger.mesh = trigger_mesh
	trigger.position = Vector3(0, HANDLE_LENGTH * 0.72, -HANDLE_RADIUS)
	trigger.material_override = Pov3D.plastic_material(Color(0.22, 0.22, 0.25))
	add_child(trigger)

	_sphere = MeshInstance3D.new()
	var sphere_mesh := SphereMesh.new()
	sphere_mesh.radius = SPHERE_RADIUS
	sphere_mesh.height = SPHERE_RADIUS * 2.0
	sphere_mesh.radial_segments = 24
	sphere_mesh.rings = 12
	_sphere.mesh = sphere_mesh
	_sphere.position = Vector3(0, HANDLE_LENGTH, 0)
	_sphere.material_override = Pov3D.glowing_material(colour)
	add_child(_sphere)


## Place the model from one tracker state entry.
func apply_state(entry: Dictionary) -> void:
	visible_to_camera = bool(entry.get("visible", false))
	has_orientation = bool(entry.get("imu", false))
	var world: Array = entry.get("world", [0, 0, 0])
	# The sphere is the thing the tracker knows the position of, and it sits at
	# the top of the handle, so the model hangs below it.
	var sphere_position := Vector3(float(world[0]), float(world[1]), float(world[2]))
	var quaternion := Quaternion.IDENTITY
	var quat: Array = entry.get("quat", [])
	if quat.size() == 4:
		# The tracker sends (w, x, y, z); Godot's constructor takes (x, y, z, w).
		quaternion = Quaternion(float(quat[1]), float(quat[2]), float(quat[3]), float(quat[0])).normalized()
	var basis := Basis(quaternion)
	global_transform = Transform3D(basis, sphere_position - basis.y * HANDLE_LENGTH)
	var dim := 1.0 if visible_to_camera else 0.25
	modulate_alpha(dim)
	_push_trail(sphere_position)


func modulate_alpha(alpha: float) -> void:
	if _glow:
		_glow.light_energy = 1.1 * alpha
	if _sphere:
		_sphere.material_override = Pov3D.glowing_material(colour, alpha)


func _push_trail(point: Vector3) -> void:
	_trail_points.append(point)
	if _trail_points.size() > 24:
		_trail_points.remove_at(0)
	_trail.clear_surfaces()
	if _trail_points.size() < 2:
		return
	_trail.surface_begin(Mesh.PRIMITIVE_LINE_STRIP)
	for index in range(_trail_points.size()):
		var fade := float(index) / float(_trail_points.size())
		_trail.surface_set_color(Color(colour.r, colour.g, colour.b, fade * 0.7))
		_trail.surface_add_vertex(_trail_points[index])
	_trail.surface_end()
