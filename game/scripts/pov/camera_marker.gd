class_name CameraMarker
extends Node3D
## Shows where the PS3 Eye actually is, in your own frame of reference.
##
## The world calibration works out the camera's position and orientation
## relative to you, so however the camera is placed - off to one side, up on a
## shelf, on the floor pointing up - this marker shows it where it really is,
## with the cone of what it can see.  It makes it obvious when the drum has
## wandered outside the camera's view, or when the camera is so far away that
## the spheres are only a few pixels across.

const FRUSTUM_LENGTH := 2.2

var _lines: ImmediateMesh
var _body: MeshInstance3D
var _label: Label3D
var pose: Dictionary = {}


func _ready() -> void:
	_body = MeshInstance3D.new()
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.09, 0.05, 0.05)
	_body.mesh = mesh
	_body.material_override = Pov3D.plastic_material(Color(0.13, 0.13, 0.15))
	add_child(_body)

	var lens := MeshInstance3D.new()
	var lens_mesh := CylinderMesh.new()
	lens_mesh.top_radius = 0.016
	lens_mesh.bottom_radius = 0.016
	lens_mesh.height = 0.03
	lens.mesh = lens_mesh
	lens.rotation_degrees = Vector3(90, 0, 0)
	lens.position = Vector3(0, 0, -0.035)
	lens.material_override = Pov3D.plastic_material(Color(0.05, 0.07, 0.12))
	_body.add_child(lens)

	var frustum := MeshInstance3D.new()
	_lines = ImmediateMesh.new()
	frustum.mesh = _lines
	frustum.material_override = Pov3D.unshaded_material(Color(0.55, 0.8, 1.0), 0.35)
	add_child(frustum)

	_label = Label3D.new()
	_label.font_size = 40
	_label.pixel_size = 0.0008
	_label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	_label.position = Vector3(0, 0.09, 0)
	_label.modulate = Color(0.65, 0.85, 1.0)
	add_child(_label)


## `pose` is the tracker's camera_pose: position, right, up, forward in world metres.
func apply_pose(new_pose: Dictionary, horizontal_fov_degrees: float) -> void:
	pose = new_pose
	var position := _vector(new_pose.get("position", [0, 0, 0]))
	var forward := _vector(new_pose.get("forward", [0, 0, 1]))
	var up := _vector(new_pose.get("up", [0, 1, 0]))
	var right := _vector(new_pose.get("right", [1, 0, 0]))
	if forward.length() < 0.5:
		return
	# Godot cameras look down -Z, so the basis Z axis is the *backwards* one.
	global_transform = Transform3D(Basis(right.normalized(), up.normalized(), -forward.normalized()), position)
	_label.text = "PS3 Eye  %.1f m" % position.length()
	_draw_frustum(horizontal_fov_degrees)


func _draw_frustum(horizontal_fov_degrees: float) -> void:
	var half_h := tan(deg_to_rad(horizontal_fov_degrees) / 2.0) * FRUSTUM_LENGTH
	var half_v := half_h * 3.0 / 4.0
	var far := Vector3(0, 0, -FRUSTUM_LENGTH)
	var corners := [
		far + Vector3(-half_h, -half_v, 0), far + Vector3(half_h, -half_v, 0),
		far + Vector3(half_h, half_v, 0), far + Vector3(-half_h, half_v, 0),
	]
	_lines.clear_surfaces()
	_lines.surface_begin(Mesh.PRIMITIVE_LINES)
	for corner in corners:
		_lines.surface_add_vertex(Vector3.ZERO)
		_lines.surface_add_vertex(corner)
	for index in range(4):
		_lines.surface_add_vertex(corners[index])
		_lines.surface_add_vertex(corners[(index + 1) % 4])
	_lines.surface_end()


static func _vector(values) -> Vector3:
	if values is Array and values.size() >= 3:
		return Vector3(float(values[0]), float(values[1]), float(values[2]))
	return Vector3.ZERO
