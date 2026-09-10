extends Node3D
## The 3D view, from where you stand.
##
## Everything the tracker knows is drawn in *your* frame: the drum where you
## placed it, your controllers where they are and pointing where their gyros
## say, and the PS3 Eye wherever it happens to sit.  The camera can be off to
## one side, up on a shelf or on the floor - the world calibration turns its
## measurements into your left, your right and your up, and this screen shows
## the result.
##
## Keys
##   W / S            move the drum away / towards you
##   A / D            move it left / right
##   Q / E            lower / raise it
##   [ / ]            smaller / bigger
##   arrow keys       look around;  , / .  move your viewpoint back / forward
##   1                put the drum where your left controller is
##   2                put it where your right controller is
##   Tab              switch between your view, a side view and the camera's view
##   Space            re-centre both controllers' heading
##   Enter            save the layout to the tracker

const NUDGE := 0.02          # metres per key press
const SCALE_STEP := 1.05
const VIEWS := ["player", "side", "camera"]

var drum: DrumModel
var camera_marker: CameraMarker
var controllers: Dictionary = {}      # id -> ControllerModel
var pads_revision := -1
var view_mode := "player"
var eye_offset := Vector3(0.0, 0.62, 0.78)
var eye_pitch := -32.0
var eye_yaw := 0.0
var status: Label
var help: Label
var show_rays := true
var show_preview := true
var _rays: MeshInstance3D
var _ray_mesh: ImmediateMesh
var _preview: TextureRect
var _preview_label: Label
var _preview_texture := ImageTexture.new()
var _has_preview := false

@onready var camera: Camera3D = $Camera3D


func _ready() -> void:
	_build_room()
	drum = DrumModel.new()
	add_child(drum)
	camera_marker = CameraMarker.new()
	add_child(camera_marker)
	for entry in [[0, GameSkin.color("hit_left")], [1, GameSkin.color("hit_right")]]:
		var model := ControllerModel.new(int(entry[0]), entry[1])
		add_child(model)
		controllers[int(entry[0])] = model
	_build_rays()
	_build_hud()
	TrackerClient.state_updated.connect(_on_state)
	TrackerClient.hit_received.connect(_on_hit)
	TrackerClient.send_command({"cmd": "get_pads"}, _on_pads)
	_apply_view()


func _build_room() -> void:
	var environment := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = GameSkin.color("background")
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.42, 0.44, 0.5)
	env.ambient_light_energy = 0.55
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	env.fog_enabled = true
	env.fog_light_color = GameSkin.color("background")
	env.fog_density = 0.02
	environment.environment = env
	add_child(environment)

	var key_light := DirectionalLight3D.new()
	key_light.rotation_degrees = Vector3(-55, 35, 0)
	key_light.light_energy = 0.8
	add_child(key_light)

	# A floor grid at the player's feet: it makes distances readable, which is
	# the whole point of showing this in 3D rather than as numbers.
	var grid := MeshInstance3D.new()
	var mesh := ImmediateMesh.new()
	grid.mesh = mesh
	grid.material_override = Pov3D.unshaded_material(Color(1, 1, 1), 0.12)
	grid.position.y = -0.95
	mesh.surface_begin(Mesh.PRIMITIVE_LINES)
	var half := 12
	for index in range(-half, half + 1):
		var offset := index * 0.25
		mesh.surface_add_vertex(Vector3(offset, 0, -half * 0.25))
		mesh.surface_add_vertex(Vector3(offset, 0, half * 0.25))
		mesh.surface_add_vertex(Vector3(-half * 0.25, 0, offset))
		mesh.surface_add_vertex(Vector3(half * 0.25, 0, offset))
	mesh.surface_end()
	add_child(grid)


## Lines from the PS3 Eye to each sphere, with the raw reading marked on them.
##
## They make visible what the tracker is up against: sideways the reading sits
## right on the sphere, but along the line of sight it slides back and forth,
## because that distance is guessed from how big the sphere looks.  The solid
## model is the filtered result, the small marker is the raw measurement.
func _build_rays() -> void:
	_rays = MeshInstance3D.new()
	_ray_mesh = ImmediateMesh.new()
	_rays.mesh = _ray_mesh
	_rays.material_override = Pov3D.unshaded_material(Color.WHITE, 0.5)
	add_child(_rays)


func _update_rays(state: Dictionary) -> void:
	_ray_mesh.clear_surfaces()
	if not show_rays:
		return
	var pose: Dictionary = state.get("camera_pose", {})
	var lens := CameraMarker._vector(pose.get("position", [0, 0, 0]))
	var any := false
	_ray_mesh.surface_begin(Mesh.PRIMITIVE_LINES)
	for entry in state.get("controllers", []):
		if not entry.get("visible", false):
			continue
		any = true
		var colour := GameSkin.color("hit_left" if int(entry.get("id", 0)) == 0 else "hit_right")
		var filtered := CameraMarker._vector(entry.get("world", [0, 0, 0]))
		var raw := CameraMarker._vector(entry.get("raw", entry.get("world", [0, 0, 0])))
		_ray_mesh.surface_set_color(Color(colour.r, colour.g, colour.b, 0.28))
		_ray_mesh.surface_add_vertex(lens)
		_ray_mesh.surface_add_vertex(filtered)
		# A short cross-piece at the raw reading, at right angles to the ray.
		var along := (filtered - lens).normalized()
		var across := along.cross(Vector3.UP).normalized() * 0.035
		_ray_mesh.surface_set_color(Color(1, 1, 1, 0.8))
		_ray_mesh.surface_add_vertex(raw - across)
		_ray_mesh.surface_add_vertex(raw + across)
	_ray_mesh.surface_end()
	if not any:
		_ray_mesh.clear_surfaces()


func _build_hud() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var background := ColorRect.new()
	background.color = Color(0, 0, 0, 0.35)
	background.set_anchors_preset(Control.PRESET_TOP_WIDE)
	background.offset_bottom = 92
	background.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.add_child(background)
	status = UiKit.label("")
	status.set_anchors_preset(Control.PRESET_TOP_WIDE)
	status.offset_left = 20
	status.offset_top = 10
	status.offset_right = -20
	status.offset_bottom = 90
	layer.add_child(status)
	help = UiKit.label("WASD move drum   Q/E height   [ ] size   1/2 place at controller   "
			+ "arrows look   , . zoom   Tab view   G rays   P camera   Space re-centre   Enter save   Esc back", true)
	help.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	help.offset_left = 20
	help.offset_top = -34
	help.offset_right = -20
	help.offset_bottom = -8
	layer.add_child(help)

	# What the PS3 Eye is actually seeing, in the corner.  Placing a drum you
	# cannot see the camera view of is guesswork; this way it is obvious when a
	# hand has wandered out of frame or a lamp is being mistaken for a sphere.
	_preview = TextureRect.new()
	_preview.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	_preview.offset_left = -340
	_preview.offset_top = 100
	_preview.offset_right = -20
	_preview.offset_bottom = 340
	_preview.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_preview.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	layer.add_child(_preview)
	_preview_label = UiKit.label("camera view", true)
	_preview_label.set_anchors_preset(Control.PRESET_TOP_RIGHT)
	_preview_label.offset_left = -340
	_preview_label.offset_top = 78
	_preview_label.offset_right = -20
	_preview_label.offset_bottom = 100
	layer.add_child(_preview_label)
	TrackerClient.preview_frame.connect(_on_preview)
	TrackerClient.send_command({"cmd": "set_preview", "preview_enabled": true, "mode": "camera"})


func _on_preview(image: Image) -> void:
	if _has_preview and _preview_texture.get_size() == Vector2(image.get_size()):
		_preview_texture.update(image)
	else:
		_preview_texture.set_image(image)
		_preview.texture = _preview_texture
	_has_preview = true


# ---------------------------------------------------------------- tracker
func _on_pads(reply: Dictionary) -> void:
	if reply.get("ok", false):
		drum.rebuild(reply.get("pads", []))
		pads_revision = int(reply.get("revision", 0))


func _on_state(state: Dictionary) -> void:
	if int(state.get("pads_revision", 0)) != pads_revision:
		TrackerClient.send_command({"cmd": "get_pads"}, _on_pads)
	for entry in state.get("controllers", []):
		var model: ControllerModel = controllers.get(int(entry.get("id", -1)))
		if model:
			model.apply_state(entry)
	camera_marker.apply_pose(state.get("camera_pose", {}), 60.0)
	_update_rays(state)
	_update_status(state)
	if view_mode == "camera":
		_apply_view()


func _on_hit(hit: Dictionary) -> void:
	drum.flash(str(hit.get("pad", "")))


func _update_status(state: Dictionary) -> void:
	var lines := []
	var calibrated: bool = state.get("world_calibrated", false)
	lines.append("%d fps   %s   view: %s" % [int(state.get("fps", 0)),
			"space calibrated" if calibrated else "NOT calibrated - do the Space tab first", view_mode])
	for entry in state.get("controllers", []):
		var world: Array = entry.get("world", [0, 0, 0])
		var marks := []
		if not entry.get("visible", false):
			marks.append("not seen by camera")
		if not entry.get("imu", false):
			marks.append("no gyro")
		if entry.get("battery", 0) is int and int(entry.get("battery", 0)) > 0:
			marks.append("battery %d" % int(entry["battery"]))
		lines.append("controller %d:  x %+.2f  y %+.2f  z %+.2f m   %s" % [
			int(entry.get("id", 0)), float(world[0]), float(world[1]), float(world[2]), "  ".join(marks)])
	var pose: Dictionary = state.get("camera_pose", {})
	var position: Array = pose.get("position", [0, 0, 0])
	lines.append("PS3 Eye at x %+.2f  y %+.2f  z %+.2f m" % [float(position[0]), float(position[1]), float(position[2])])
	status.text = "\n".join(lines)


# ---------------------------------------------------------------- viewing
func _apply_view() -> void:
	match view_mode:
		"side":
			var centre := _drum_centre()
			camera.global_transform = Transform3D(Basis(), centre + Vector3(1.8, 0.5, 0.1)).looking_at(centre, Vector3.UP)
		"camera":
			# Sit where the PS3 Eye is, looking where it looks.
			if camera_marker.pose.is_empty():
				return
			camera.global_transform = camera_marker.global_transform
		_:
			# Your own head, fixed where the calibrated origin says you stand.
			# It does not follow the drum: the whole point of moving the drum is
			# to see where it ends up in front of you.
			var basis := Basis.from_euler(Vector3(deg_to_rad(eye_pitch), deg_to_rad(eye_yaw), 0))
			camera.global_transform = Transform3D(basis, eye_offset)


func _unhandled_input(event: InputEvent) -> void:
	if not (event is InputEventKey) or not event.pressed:
		return
	match event.keycode:
		KEY_ESCAPE: UiKit.go_to("res://scenes/main_menu.tscn")
		KEY_TAB:
			view_mode = VIEWS[(VIEWS.find(view_mode) + 1) % VIEWS.size()]
			_apply_view()
		KEY_W: _nudge(Vector3(0, 0, -1))
		KEY_S: _nudge(Vector3(0, 0, 1))
		KEY_A: _nudge(Vector3(-1, 0, 0))
		KEY_D: _nudge(Vector3(1, 0, 0))
		KEY_Q: _nudge(Vector3(0, -1, 0))
		KEY_E: _nudge(Vector3(0, 1, 0))
		KEY_BRACKETLEFT: _scale(1.0 / SCALE_STEP)
		KEY_BRACKETRIGHT: _scale(SCALE_STEP)
		KEY_1: _place_at_controller(0)
		KEY_2: _place_at_controller(1)
		KEY_SPACE: TrackerClient.send_command({"cmd": "recenter_orientation"})
		KEY_G: show_rays = not show_rays
		KEY_P:
			show_preview = not show_preview
			_preview.visible = show_preview
			_preview_label.visible = show_preview
		KEY_ENTER, KEY_KP_ENTER: TrackerClient.send_command({"cmd": "save_config"})
		KEY_LEFT: eye_yaw += 4.0; _apply_view()
		KEY_RIGHT: eye_yaw -= 4.0; _apply_view()
		KEY_UP: eye_pitch = clampf(eye_pitch + 3.0, -85.0, 20.0); _apply_view()
		KEY_DOWN: eye_pitch = clampf(eye_pitch - 3.0, -85.0, 20.0); _apply_view()
		KEY_COMMA: eye_offset.z += 0.08; _apply_view()
		KEY_PERIOD: eye_offset.z = maxf(0.2, eye_offset.z - 0.08); _apply_view()


func _nudge(direction: Vector3) -> void:
	TrackerClient.send_command({"cmd": "nudge_pads", "delta": [direction.x * NUDGE, direction.y * NUDGE, direction.z * NUDGE]},
			_after_change)


func _scale(factor: float) -> void:
	TrackerClient.send_command({"cmd": "nudge_pads", "scale": factor}, _after_change)


## Where the drum sits, averaged over its pads.
func _drum_centre() -> Vector3:
	if drum == null or drum.pads.is_empty():
		return Vector3.ZERO
	var centre := Vector3.ZERO
	for pad in drum.pads:
		var c: Array = pad.get("center", [0, 0, 0])
		centre += Vector3(float(c[0]), float(c[1]), float(c[2]))
	return centre / drum.pads.size()


func _place_at_controller(id: int) -> void:
	var model: ControllerModel = controllers.get(id)
	if model == null:
		return
	var target := model.global_transform.origin + model.global_transform.basis.y * ControllerModel.HANDLE_LENGTH
	var delta := target - _drum_centre()
	TrackerClient.send_command({"cmd": "nudge_pads", "delta": [delta.x, delta.y, delta.z]}, _after_change)


func _after_change(reply: Dictionary) -> void:
	if reply.get("ok", false) and reply.has("pads"):
		drum.rebuild(reply["pads"])
		_apply_view()
