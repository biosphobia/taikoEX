extends Control
## Camera, colour, space and pad calibration.  Talks to the tracker over UDP.
##
## Every control changes the tracker's config live and saves it to
## tracker/tracker_config.json.  The tabs follow the calibration order in
## docs/CALIBRATION.md: Camera -> Detection -> Colours -> Space -> Pads.

var config: Dictionary = {}
var preview: PreviewView
var tabs: TabContainer
var status_label: Label
var pad_view: PadView
var mode_label: Label
var controller_id := 0
var _built := false
var _pads_box: VBoxContainer
var _retry_timer := 0.0
var _led_presets: Dictionary = {}     # name -> {led, hsv_min, hsv_max}, from the tracker


func _ready() -> void:
	UiKit.background(self)
	var root := HBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.offset_left = 16
	root.offset_top = 16
	root.offset_right = -16
	root.offset_bottom = -16
	root.add_theme_constant_override("separation", 16)
	add_child(root)

	var left := VBoxContainer.new()
	left.add_theme_constant_override("separation", 6)
	root.add_child(left)
	preview = PreviewView.new()
	preview.sample_requested.connect(_on_sample_at)
	preview.crop_drawn.connect(_on_crop_drawn)
	preview.mask_drawn.connect(_on_mask_drawn)
	left.add_child(preview)
	var tools := UiKit.row(left, 6)
	tools.add_child(UiKit.button("Sample colour", func(): _set_mode("sample"), 130))
	tools.add_child(UiKit.button("Draw crop", func(): _set_mode("crop"), 110))
	tools.add_child(UiKit.button("Draw mask", func(): _set_mode("mask"), 110))
	tools.add_child(UiKit.button("Clear crop", func(): _patch({"processing": {"crop": {"x": 0, "y": 0, "w": 0, "h": 0}}}), 110))
	tools.add_child(UiKit.button("Clear masks", func(): _patch({"processing": {"mask_polygons": []}}), 110))
	mode_label = UiKit.label("Tool: none", true)
	left.add_child(mode_label)
	status_label = UiKit.label("Connecting to tracker...", true)
	left.add_child(status_label)
	var bottom := UiKit.row(left, 6)
	bottom.add_child(UiKit.button("Save tracker config", func(): TrackerClient.send_command({"cmd": "save_config"}), 180))
	bottom.add_child(UiKit.button("Reset to defaults", func(): TrackerClient.send_command({"cmd": "reset_config", "save": true}, _on_config); _built = false, 160))
	bottom.add_child(UiKit.button("Back  (Esc)", func(): UiKit.go_to("res://scenes/main_menu.tscn"), 120))

	tabs = TabContainer.new()
	tabs.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(tabs)

	TrackerClient.state_updated.connect(_on_state)
	TrackerClient.send_command({"cmd": "led_presets"}, func(reply):
		_led_presets = reply.get("presets", {})
		if _built:
			_rebuild_colour_tab())
	TrackerClient.send_command({"cmd": "get_config"}, _on_config)
	TrackerClient.send_command({"cmd": "set_preview", "preview_enabled": true, "mode": "camera"})


func _process(delta: float) -> void:
	if not _built:
		_retry_timer += delta
		if _retry_timer > 1.5:
			_retry_timer = 0.0
			TrackerClient.send_command({"cmd": "get_config"}, _on_config)
			status_label.text = "Tracker not connected. Start tracker/run_tracker.py (or check ports in Settings)."


# ---------------------------------------------------------------- tracker
func _on_config(reply: Dictionary) -> void:
	if not reply.get("ok", false) or not reply.has("config"):
		return
	config = reply["config"]
	preview.config = config
	preview.frame_size = Vector2(float(config["camera"]["width"]), float(config["camera"]["height"]))
	if not _built:
		_build_tabs()
		_built = true
	if pad_view:
		pad_view.pads = config.get("pads", [])
	_rebuild_pad_list()


func _on_state(state: Dictionary) -> void:
	preview.frame_size = Vector2(float(state.get("frame", [640, 480])[0]), float(state.get("frame", [640, 480])[1]))
	var frame: Array = state.get("frame", [640, 480])
	var parts := ["%d fps measured   camera %dx%d says %d fps   asked for %d" % [int(state.get("fps", 0)),
			int(frame[0]), int(frame[1]), int(state.get("fps_camera", 0)), int(state.get("fps_requested", 0))]]
	for controller in state.get("controllers", []):
		var w: Array = controller.get("world", [0, 0, 0])
		var px: Array = controller.get("px", [0, 0, 0])
		if controller.get("visible", false):
			parts.append("C%d: x %.2f y %.2f z %.2f (r %.1f px)%s" % [int(controller["id"]), float(w[0]), float(w[1]), float(w[2]), float(px[2]), " HID" if controller.get("hid", false) else ""])
		else:
			parts.append("C%d: not visible" % int(controller["id"]))
	parts.append("world: " + ("calibrated" if state.get("world_calibrated", false) else "default (%s)" % ",".join(state.get("world_points", []))))
	status_label.text = "\n".join(parts)


## Send a partial config to the tracker and mirror it locally.
func _patch(patch: Dictionary, save: bool = true) -> void:
	_merge_into(config, patch)
	preview.config = config
	TrackerClient.set_config(patch, save)
	if pad_view:
		pad_view.pads = config.get("pads", [])


static func _merge_into(target: Dictionary, patch: Dictionary) -> void:
	for key in patch:
		if patch[key] is Dictionary and target.get(key) is Dictionary:
			_merge_into(target[key], patch[key])
		else:
			target[key] = patch[key]


func _dotted(path: String, value: Variant) -> Dictionary:
	var parts := path.split(".")
	var result := {parts[-1]: value}
	for i in range(parts.size() - 2, -1, -1):
		result = {parts[i]: result}
	return result


func _value(path: String, fallback: Variant = 0) -> Variant:
	var node: Variant = config
	for part in path.split("."):
		if node is Dictionary and node.has(part):
			node = node[part]
		else:
			return fallback
	return node if node != null else fallback


func _set_path(path: String, value: Variant) -> void:
	_patch(_dotted(path, value))


func _set_mode(mode: String) -> void:
	preview.mode = mode
	mode_label.text = {"sample": "Tool: click the sphere of controller %d in the preview" % controller_id,
			"crop": "Tool: drag a rectangle to crop the search area",
			"mask": "Tool: click polygon corners, right click to finish"}.get(mode, "Tool: none")


func _on_sample_at(x: int, y: int) -> void:
	TrackerClient.send_command({"cmd": "sample_colour", "controller": controller_id, "x": x, "y": y, "save": true}, func(reply):
		if reply.get("ok", false):
			var controllers: Array = config["controllers"]
			for c in controllers:
				if int(c["id"]) == controller_id:
					c["hsv_min"] = reply["hsv_min"]
					c["hsv_max"] = reply["hsv_max"]
			_rebuild_colour_tab()
	)


func _on_crop_drawn(rect: Rect2i) -> void:
	_patch({"processing": {"crop": {"x": rect.position.x, "y": rect.position.y, "w": rect.size.x, "h": rect.size.y}}})
	_set_mode("none")


func _on_mask_drawn(points: Array) -> void:
	var polygons: Array = config["processing"].get("mask_polygons", []).duplicate()
	polygons.append(points)
	_patch({"processing": {"mask_polygons": polygons}})


# ---------------------------------------------------------------- tabs
func _build_tabs() -> void:
	for child in tabs.get_children():
		child.queue_free()
	_build_camera_tab()
	_build_detection_tab()
	_colour_tab = _tab("Colours")
	_rebuild_colour_tab()
	_build_space_tab()
	_build_pads_tab()


var _colour_tab: VBoxContainer


func _tab(name: String) -> VBoxContainer:
	var panel := MarginContainer.new()
	panel.name = name
	panel.add_theme_constant_override("margin_left", 10)
	panel.add_theme_constant_override("margin_right", 10)
	panel.add_theme_constant_override("margin_top", 10)
	tabs.add_child(panel)
	return UiKit.scroll_panel(panel)


func _build_camera_tab() -> void:
	var box := _tab("Camera")
	box.add_child(UiKit.label("Camera changes below need 'Apply camera' (re-opens the device). Controls further down apply live.", true))
	var pending := {}
	UiKit.option(box, "Backend", ["opencv", "pseye", "video", "simulated"], _value("camera.backend", "opencv"), func(v): pending["backend"] = v)
	UiKit.spin(box, "Camera index", 0, 15, 1, _value("camera.index", 0), func(v): pending["index"] = int(v))
	UiKit.option(box, "Resolution", ["320x240", "640x480"], "%dx%d" % [int(_value("camera.width", 640)), int(_value("camera.height", 480))], func(v):
		var wh: PackedStringArray = v.split("x")
		pending["width"] = int(wh[0])
		pending["height"] = int(wh[1]))
	UiKit.spin(box, "FPS", 15, 187, 1, _value("camera.fps", 60), func(v): pending["fps"] = int(v))
	UiKit.check(box, "Flip horizontal", _value("camera.flip_horizontal", false), func(v): pending["flip_horizontal"] = v)
	UiKit.check(box, "Flip vertical", _value("camera.flip_vertical", false), func(v): pending["flip_vertical"] = v)
	UiKit.option(box, "Rotate", [0, 90, 180, 270], int(_value("camera.rotate_degrees", 0)), func(v): pending["rotate_degrees"] = v)
	UiKit.text_field(box, "Video file (video backend)", str(_value("camera.video_path", "")), func(v): pending["video_path"] = v)
	UiKit.text_field(box, "Pixel format (opencv, e.g. MJPG; empty for the PS3 Eye)", str(_value("camera.fourcc", "")), func(v): pending["fourcc"] = v)
	var apply_row := UiKit.row(box)
	apply_row.add_child(UiKit.button("Apply camera", func(): _patch({"camera": pending.duplicate()}); pending.clear(), 200))
	apply_row.add_child(UiKit.button("Fastest mode", _find_fastest_mode, 200))
	box.add_child(UiKit.label("Fastest mode tries the modes listed under camera.fast_modes, quickest first, measures what the driver really delivers and keeps the first one that does. A PS3 Eye reaches 187 fps at 320x240; the calibration carries over, because every pixel setting is scaled with the resolution.", true))
	box.add_child(UiKit.heading("Live camera controls"))
	box.add_child(UiKit.label("Lower exposure and gain until the room goes dark and only the spheres stay bright.", true))
	UiKit.check(box, "Auto exposure", bool(_value("camera.controls.auto_exposure", 0)), func(v): _camera_control("auto_exposure", 1 if v else 0))
	UiKit.slider(box, "Exposure", 0, 255, 1, float(_value("camera.controls.exposure", 40)), func(v): _camera_control("exposure", v), true)
	UiKit.slider(box, "Gain", 0, 100, 1, float(_value("camera.controls.gain", 10)), func(v): _camera_control("gain", v), true)
	UiKit.slider(box, "Brightness", 0, 255, 1, float(_value("camera.controls.brightness", 128)), func(v): _camera_control("brightness", v), true)
	UiKit.slider(box, "Contrast", 0, 255, 1, float(_value("camera.controls.contrast", 128)), func(v): _camera_control("contrast", v), true)
	UiKit.slider(box, "Saturation", 0, 255, 1, float(_value("camera.controls.saturation", 128)), func(v): _camera_control("saturation", v), true)
	UiKit.slider(box, "Sharpness", 0, 255, 1, float(_value("camera.controls.sharpness", 0)), func(v): _camera_control("sharpness", v), true)
	UiKit.check(box, "Auto white balance", bool(_value("camera.controls.auto_white_balance", 0)), func(v): _camera_control("auto_white_balance", 1 if v else 0))
	UiKit.slider(box, "White balance (K)", 2000, 8000, 50, float(_value("camera.controls.white_balance", 4500)), func(v): _camera_control("white_balance", v), true)


func _find_fastest_mode() -> void:
	status_label.text = "Trying camera modes... (the picture pauses for a moment)"
	TrackerClient.send_command({"cmd": "camera_fastest", "save": true}, func(reply):
		if not reply.get("ok", false):
			status_label.text = "Fastest mode failed: %s" % reply.get("error", "")
			return
		var chosen: Dictionary = reply.get("chosen", {})
		_merge_into(config, {"camera": {"width": chosen.get("width", 640), "height": chosen.get("height", 480),
				"fps": chosen.get("fps_requested", 60)}})
		var lines := ["Now %dx%d at %d fps (measured %.0f)" % [int(chosen.get("width", 0)), int(chosen.get("height", 0)),
				int(chosen.get("fps_requested", 0)), float(chosen.get("fps_measured", 0))]]
		for mode in reply.get("modes", []):
			lines.append("  %dx%d asked %d: driver says %.0f, measured %.0f%s" % [int(mode.get("width", 0)),
					int(mode.get("height", 0)), int(mode.get("fps_requested", 0)), float(mode.get("fps_reported", 0)),
					float(mode.get("fps_measured", 0)), "  (" + str(mode["error"]) + ")" if mode.has("error") else ""])
		status_label.text = "\n".join(lines))


func _camera_control(control: String, value: Variant) -> void:
	config["camera"]["controls"][control] = value
	TrackerClient.send_command({"cmd": "set_camera_control", "control": control, "value": value, "save": true})


func _build_detection_tab() -> void:
	var box := _tab("Detection")
	UiKit.option(box, "Preview shows", ["camera", "mask"], "camera", func(v): TrackerClient.send_command({"cmd": "set_preview", "mode": v}))
	UiKit.slider(box, "Preview fps", 1, 60, 1, float(_value("network.preview_fps", 20)), func(v): _set_path("network.preview_fps", int(v)))

	box.add_child(UiKit.heading("Learn the room  (do this first)"))
	box.add_child(UiKit.label("Turns the sphere LEDs off for a moment, sees what in the room still looks like a controller - a lamp, a screen, a poster, sunlight - and masks it away. Everything else you calibrate depends on this, so press it before the Colours and Space tabs, and again whenever you move the camera or change the lighting.", true))
	var learn_row := UiKit.row(box)
	learn_row.add_child(UiKit.button("Learn background", func():
		TrackerClient.send_command({"cmd": "learn_background", "save": true}, func(reply):
			status_label.text = "learning the room..."
			await get_tree().create_timer(1.5).timeout
			TrackerClient.send_command({"cmd": "background_result"}, func(result):
				var found: Dictionary = result.get("result", {})
				status_label.text = "masked %s area(s), %s%% of the frame" % [
					str(found.get("regions", "?")), str(found.get("covered_percent", "?"))]
				TrackerClient.send_command({"cmd": "get_config"}, _on_config))), 200))
	learn_row.add_child(UiKit.button("Clear background", func():
		TrackerClient.send_command({"cmd": "clear_background", "save": true}, _on_simple_reply), 200))
	UiKit.slider(box, "Grow masked areas (px)", 0, 30, 1, float(_value("background.dilate_px", 6)), func(v): _set_path("background.dilate_px", int(v)))
	UiKit.slider(box, "Seconds to watch", 0.2, 3.0, 0.1, float(_value("background.learn_seconds", 0.5)), func(v): _set_path("background.learn_seconds", v))
	box.add_child(UiKit.heading("Blob cleanup"))
	UiKit.slider(box, "Blur (px)", 0, 15, 1, float(_value("processing.blur", 3)), func(v): _set_path("processing.blur", int(v)))
	UiKit.slider(box, "Open iterations (speckles)", 0, 5, 1, float(_value("processing.open_iterations", 1)), func(v): _set_path("processing.open_iterations", int(v)))
	UiKit.slider(box, "Close iterations (gaps)", 0, 8, 1, float(_value("processing.close_iterations", 2)), func(v): _set_path("processing.close_iterations", int(v)))
	UiKit.check(box, "Fill holes (blown-out centre)", _value("processing.fill_holes", true), func(v): _set_path("processing.fill_holes", v))
	UiKit.check(box, "Grab bright white core next to the colour", _value("processing.bright_core", true), func(v): _set_path("processing.bright_core", v))
	UiKit.slider(box, "Bright core: min value", 150, 255, 1, float(_value("processing.bright_core_min_value", 235)), func(v): _set_path("processing.bright_core_min_value", int(v)))
	UiKit.slider(box, "Bright core: max saturation", 0, 255, 1, float(_value("processing.bright_core_max_saturation", 60)), func(v): _set_path("processing.bright_core_max_saturation", int(v)))
	UiKit.slider(box, "Bright core: reach (px)", 0, 40, 1, float(_value("processing.bright_core_reach_px", 12)), func(v): _set_path("processing.bright_core_reach_px", int(v)))
	box.add_child(UiKit.heading("Blob acceptance"))
	UiKit.slider(box, "Min radius (px)", 1, 50, 1, float(_value("processing.min_radius_px", 3)), func(v): _set_path("processing.min_radius_px", v))
	UiKit.slider(box, "Max radius (px)", 10, 300, 1, float(_value("processing.max_radius_px", 150)), func(v): _set_path("processing.max_radius_px", v))
	UiKit.slider(box, "Min circularity", 0.1, 1.0, 0.01, float(_value("processing.min_circularity", 0.45)), func(v): _set_path("processing.min_circularity", v))
	UiKit.slider(box, "Min fill ratio", 0.1, 1.0, 0.01, float(_value("processing.min_fill_ratio", 0.35)), func(v): _set_path("processing.min_fill_ratio", v))
	UiKit.slider(box, "Track reach (px)", 0, 400, 5, float(_value("processing.track_reach_px", 80)), func(v): _set_path("processing.track_reach_px", v))
	UiKit.slider(box, "Radius offset (px)", -3, 3, 0.1, float(_value("processing.radius_offset_px", 0)), func(v): _set_path("processing.radius_offset_px", v))


func _rebuild_colour_tab() -> void:
	var box := _colour_tab
	for child in box.get_children():
		child.queue_free()
	UiKit.option(box, "Editing controller", [0, 1], controller_id, func(v): controller_id = int(v); _rebuild_colour_tab())
	var controllers: Array = config.get("controllers", [])
	var ctrl: Dictionary = {}
	for c in controllers:
		if int(c["id"]) == controller_id:
			ctrl = c
	if ctrl.is_empty():
		box.add_child(UiKit.label("The tracker config has no controller %d. Press 'Reset to defaults' below, or add one under \"controllers\" in tracker_config.json." % controller_id, true))
		return
	box.add_child(UiKit.heading(str(ctrl.get("name", "Controller"))))
	var preset_names: Array = ["(pick a preset)"]
	preset_names.append_array(_led_presets.keys())
	UiKit.option(box, "Sphere colour preset", preset_names, preset_names[0], func(v):
		if not _led_presets.has(v):
			return
		var preset: Dictionary = _led_presets[v]
		ctrl["led"] = preset["led"].duplicate()
		ctrl["hsv_min"] = preset["hsv_min"].duplicate()
		ctrl["hsv_max"] = preset["hsv_max"].duplicate()
		_patch({"controllers": controllers})
		_rebuild_colour_tab())
	var led_row := UiKit.row(box)
	led_row.add_child(UiKit.label("Sphere colour (LED)"))
	var picker := ColorPickerButton.new()
	picker.custom_minimum_size = Vector2(120, 32)
	var led: Array = ctrl.get("led", [255, 255, 255])
	picker.color = Color8(int(led[0]), int(led[1]), int(led[2]))
	picker.color_changed.connect(func(c: Color):
		ctrl["led"] = [c.r8, c.g8, c.b8]
		_patch({"controllers": controllers}))
	led_row.add_child(picker)
	UiKit.slider(box, "LED brightness (all)", 0.1, 1.0, 0.05, float(_value("hid.led_brightness", 1.0)), func(v): _set_path("hid.led_brightness", v))
	box.add_child(UiKit.label("Tip: magenta and cyan track best. Then press 'Sample colour' and click the sphere in the preview, or tune the ranges below (OpenCV hue is 0-179).", true))
	box.add_child(UiKit.button("Sample colour from preview centre", func():
		var c := preview.size / 2.0
		var p := preview.to_frame(c)
		_on_sample_at(int(p.x), int(p.y)), 300))
	var names := ["Hue", "Saturation", "Value"]
	var maxes := [179, 255, 255]
	for i in range(3):
		UiKit.slider(box, names[i] + " min", 0, maxes[i], 1, float(ctrl["hsv_min"][i]), func(v): ctrl["hsv_min"][i] = int(v); _patch({"controllers": controllers}), true)
		UiKit.slider(box, names[i] + " max", 0, maxes[i], 1, float(ctrl["hsv_max"][i]), func(v): ctrl["hsv_max"][i] = int(v); _patch({"controllers": controllers}), true)
	UiKit.slider(box, "Position smoothing", 0.0, 0.9, 0.05, float(ctrl.get("smoothing", 0.35)), func(v): ctrl["smoothing"] = v; _patch({"controllers": controllers}))
	UiKit.text_field(box, "Bind to Bluetooth address", str(ctrl.get("hid_serial", "")), func(v): ctrl["hid_serial"] = v; _patch({"controllers": controllers}))
	box.add_child(UiKit.button("List paired controllers", func():
		TrackerClient.send_command({"cmd": "list_controllers"}, func(reply): status_label.text = JSON.stringify(reply.get("devices", []))), 300))


func _build_space_tab() -> void:
	var box := _tab("Space")
	box.add_child(UiKit.heading("1. Distance scale  (two measurements)"))
	box.add_child(UiKit.label("Hold controller %d a measured distance from the lens - tape measure from the front of the lens to the middle of the sphere - type it and add the sample. Then do it again at a clearly different distance, say 0.7 m and 1.5 m.\n\nTwo are needed because a glowing sphere always measures about a pixel wider than it is, and one distance cannot tell that constant apart from the focal length. With one sample every position you get comes out scaled by roughly ten per cent." % controller_id, true))
	var distance := [0.7]
	UiKit.spin(box, "Measured distance (m)", 0.3, 4.0, 0.01, 0.7, func(v): distance[0] = v)
	var distance_row := UiKit.row(box)
	distance_row.add_child(UiKit.button("Add distance sample", func():
		TrackerClient.send_command({"cmd": "calibrate_distance", "controller": controller_id, "distance_m": distance[0], "save": true}, func(reply):
			if not reply.get("ok", false):
				status_label.text = str(reply.get("error", "failed"))
			elif reply.has("focal_px"):
				status_label.text = "focal %s px, sphere glow %s px  (samples: %s)" % [
					str(reply["focal_px"]), str(reply.get("radius_offset_px", 0)), str(reply.get("samples", []))]
			else:
				status_label.text = "sample taken at %.2f m - now measure a different distance" % distance[0]
			TrackerClient.send_command({"cmd": "get_config"}, _on_config)), 220))
	distance_row.add_child(UiKit.button("Start over", func():
		TrackerClient.send_command({"cmd": "calibrate_distance_reset"}, _on_simple_reply), 140))
	UiKit.spin(box, "Focal length (px)", 100, 2000, 1, float(_value("optics.focal_px", 545)), func(v): _set_path("optics.focal_px", v))
	UiKit.spin(box, "Sphere glow (px)", -3, 6, 0.05, float(_value("optics.radius_offset_px", 0.0)), func(v): _set_path("optics.radius_offset_px", v))
	UiKit.spin(box, "Sphere radius (m)", 0.01, 0.05, 0.0005, float(_value("optics.sphere_radius_m", 0.0225)), func(v): _set_path("optics.sphere_radius_m", v))

	box.add_child(UiKit.heading("2. Playing space (world axes)"))
	box.add_child(UiKit.label("Hold controller %d still at each spot and press capture: ORIGIN = where the drum centre should be, RIGHT = about 40 cm to your right, FORWARD = about 40 cm towards the camera. After the third point the axes are solved." % controller_id, true))
	var caps := UiKit.row(box)
	for point in ["origin", "right", "forward"]:
		caps.add_child(UiKit.button("Capture " + point.to_upper(), func():
			TrackerClient.send_command({"cmd": "world_capture", "point": point, "controller": controller_id, "save": true}, func(reply):
				status_label.text = "captured: %s" % str(reply.get("captured", reply.get("error", "?")))
				if reply.has("world"):
					config["world"] = reply["world"]), 160))
	box.add_child(UiKit.button("Reset world calibration", func(): TrackerClient.send_command({"cmd": "world_reset", "save": true}), 240))

	box.add_child(UiKit.heading("3. Controller orientation"))
	box.add_child(UiKit.label("The gyro and accelerometer give the 3D view the controller's tilt, and time the hits. Hold the controller upright with the sphere at the top and press Set upright so the model matches your real one. Pressing the MOVE button re-points its heading at the camera at any time.", true))
	var imu_row := UiKit.row(box)
	imu_row.add_child(UiKit.button("Set upright", func():
		TrackerClient.send_command({"cmd": "imu_calibrate_upright", "controller": controller_id, "save": true}, func(reply):
			status_label.text = "handle axis %s" % str(reply.get("handle_axis", reply.get("error", "?")))), 160))
	imu_row.add_child(UiKit.button("Re-centre heading", func():
		TrackerClient.send_command({"cmd": "recenter_orientation"}, _on_simple_reply), 180))
	UiKit.check(box, "Use the gyro and accelerometer", _value("imu.enabled", true), func(v): _set_path("imu.enabled", v))

	box.add_child(UiKit.heading("4. Hit detection"))
	UiKit.option(box, "A hit is...", ["stroke", "plane"], str(_value("hits.mode", "stroke")), func(v): _set_path("hits.mode", v))
	box.add_child(UiKit.label("stroke: the bottom of the swing, where your hand turns around. Robust, because it does not rely on the camera judging distance.\nplane: the sphere crossing the pad surface. Sharper, but only as good as the depth estimate - use it with a close camera in front of you.", true))
	UiKit.check(box, "Time hits from the controller's accelerometer", _value("hits.use_accelerometer", true), func(v): _set_path("hits.use_accelerometer", v))
	UiKit.slider(box, "Strike threshold (g)", 0.5, 8.0, 0.1, float(_value("hits.accel_threshold_g", 2.5)), func(v): _set_path("hits.accel_threshold_g", v))
	UiKit.slider(box, "Full-strength strike (g)", 2.0, 15.0, 0.5, float(_value("hits.hard_hit_g", 6.0)), func(v): _set_path("hits.hard_hit_g", v))
	UiKit.slider(box, "Min stroke speed (m/s)", 0.1, 3.0, 0.05, float(_value("hits.min_speed_mps", 0.5)), func(v): _set_path("hits.min_speed_mps", v))
	UiKit.slider(box, "Re-arm lift (m)", 0.005, 0.15, 0.005, float(_value("hits.rearm_height_m", 0.04)), func(v): _set_path("hits.rearm_height_m", v))
	UiKit.slider(box, "Height window (m)", 0.05, 0.6, 0.01, float(_value("hits.height_window_m", 0.25)), func(v): _set_path("hits.height_window_m", v))
	UiKit.slider(box, "Cooldown (s)", 0.0, 0.3, 0.005, float(_value("hits.cooldown_s", 0.05)), func(v): _set_path("hits.cooldown_s", v))
	UiKit.slider(box, "Latency compensation (ms)", 0, 120, 1, float(_value("hits.latency_compensation_ms", 25)), func(v): _set_path("hits.latency_compensation_ms", v))
	UiKit.slider(box, "Latency, accelerometer hits (ms)", -40, 80, 1, float(_value("hits.latency_compensation_imu_ms", 15)), func(v): _set_path("hits.latency_compensation_imu_ms", v))

	box.add_child(UiKit.heading("5. Position smoothing"))
	box.add_child(UiKit.label("A camera measures direction well and distance badly, so the filter smooths distance hard and leaves the sideways axes alone. There is rarely a reason to change these.", true))
	UiKit.check(box, "Smooth positions", _value("fusion.enabled", true), func(v): _set_path("fusion.enabled", v))
	UiKit.check(box, "Feed the accelerometer into the prediction", _value("fusion.use_imu_accel", true), func(v): _set_path("fusion.use_imu_accel", v))
	UiKit.slider(box, "Blob centre noise (px)", 0.05, 2.0, 0.05, float(_value("fusion.pixel_noise_px", 0.35)), func(v): _set_path("fusion.pixel_noise_px", v))
	UiKit.slider(box, "Blob radius noise (px)", 0.05, 2.0, 0.05, float(_value("fusion.radius_noise_px", 0.45)), func(v): _set_path("fusion.radius_noise_px", v))


func _build_pads_tab() -> void:
	var box := _tab("Pads")
	pad_view = PadView.new()
	pad_view.pads = config.get("pads", [])
	box.add_child(pad_view)
	var style := ["single_drum"]
	UiKit.option(box, "Layout", ["single_drum", "four_pads"], "single_drum", func(v): style[0] = v)
	box.add_child(UiKit.label("single_drum: one drum, face for don and rim for ka, and the hand that strikes decides left from right. The default, and the one a camera can judge most reliably.\nfour_pads: left rim, left face, right face, right rim in a row. Easier to aim at, but it needs a closer camera.", true))
	var presets := UiKit.row(box)
	presets.add_child(UiKit.button("Rebuild at controller 0", func():
		TrackerClient.send_command({"cmd": "pad_layout_taiko", "style": style[0], "controller": 0, "save": true}, _after_pad_change), 220))
	presets.add_child(UiKit.button("Rebuild at origin", func():
		TrackerClient.send_command({"cmd": "pad_layout_taiko", "style": style[0], "center": [0, 0, 0], "save": true}, _after_pad_change), 200))
	presets.add_child(UiKit.button("Open the 3D view", func(): UiKit.go_to("res://scenes/pov_view.tscn"), 180))
	box.add_child(UiKit.label("Place a pad: hold the controller where you want it and press the button. Pads are flat discs; normal (0,1,0) means 'hit downwards'.", true))
	_pads_box = VBoxContainer.new()
	box.add_child(_pads_box)
	_rebuild_pad_list()


func _on_simple_reply(reply: Dictionary) -> void:
	status_label.text = str(reply.get("error", "done"))
	TrackerClient.send_command({"cmd": "get_config"}, _on_config)


func _after_pad_change(_reply: Dictionary) -> void:
	TrackerClient.send_command({"cmd": "get_config"}, _on_config)


func _rebuild_pad_list() -> void:
	if _pads_box == null:
		return
	for child in _pads_box.get_children():
		child.queue_free()
	var pads: Array = config.get("pads", [])
	for pad in pads:
		var c: Array = pad.get("center", [0, 0, 0])
		var row := UiKit.row(_pads_box, 6)
		var name := UiKit.label("%s   x %.2f  y %.2f  z %.2f" % [pad.get("id", "?"), float(c[0]), float(c[1]), float(c[2])])
		name.autowrap_mode = TextServer.AUTOWRAP_OFF
		name.custom_minimum_size.x = 250
		row.add_child(name)
		for cid in [0, 1]:
			row.add_child(UiKit.button("Place: C%d" % cid, func():
				TrackerClient.send_command({"cmd": "place_pad", "pad": pad["id"], "controller": cid, "save": true}, _after_pad_change), 90))
		var radius := SpinBox.new()
		radius.min_value = 0.03
		radius.max_value = 0.5
		radius.step = 0.005
		radius.value = float(pad.get("radius", 0.1))
		radius.suffix = " m"
		radius.custom_minimum_size.x = 110
		radius.value_changed.connect(func(v): pad["radius"] = v; _patch({"pads": pads}))
		row.add_child(radius)


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		if preview.mode != "none":
			_set_mode("none")
		else:
			UiKit.go_to("res://scenes/main_menu.tscn")
