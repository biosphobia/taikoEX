extends Node
## Walks through the whole game automatically.  Used for runtime testing and
## to record demo footage:
##
##   godot --path game res://tests/demo_driver.tscn -- ++song_seconds=30 ++sim
##
## ++song_seconds=N  stop the song after N seconds
## ++sim             drive a simulated tracker (camera backend "simulated"):
##                   it performs the world calibration by moving a virtual
##                   controller to the three calibration spots, hits pads on
##                   the calibration screen, then plays the chart through the
##                   full camera -> 3D -> pad pipeline during the song.
## ++autoplay        let the game play itself instead (no tracker needed)

var song_seconds := 1e9
var use_sim := false
var phase := ""
var phase_time := 0.0
var _step := 0
var _sim_timer := 0.0
var _sim_pad := 0
var _pad_ids: Array[String] = []
var _hits_seen := 0


func _ready() -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("++song_seconds="):
			song_seconds = float(arg.get_slice("=", 1))
		elif arg == "++sim":
			use_sim = true
		elif arg == "++autoplay":
			Settings.set_value("gameplay.autoplay", true, false)
	TrackerClient.hit_received.connect(func(hit):
		_hits_seen += 1
		if _hits_seen <= 12:
			print("[demo] tracker hit ", hit.get("pad"), " ", hit.get("speed"), " m/s"))
	_reparent_to_root.call_deferred()


## Survive scene changes by hanging under the root instead of the current scene.
func _reparent_to_root() -> void:
	var root := get_tree().root
	get_parent().remove_child(self)
	root.add_child(self)
	_enter("menu", "res://scenes/main_menu.tscn")


func _enter(name: String, scene: String) -> void:
	phase = name
	phase_time = 0.0
	_step = 0
	print("[demo] -> ", name)
	UiKit.go_to(scene)


func _process(delta: float) -> void:
	phase_time += delta
	match phase:
		"menu":
			if phase_time > 2.5:
				_enter("calibration", "res://scenes/calibration.tscn")
		"calibration":
			_drive_calibration(delta)
			if phase_time > 20.0:
				_enter("select", "res://scenes/song_select.tscn")
		"select":
			if phase_time > 3.0:
				var entries := SongLibrary.scan()
				if entries.is_empty():
					push_error("[demo] no songs found")
					get_tree().quit(1)
					return
				GameSession.chart = entries[0].chart
				GameSession.course_name = entries[0].chart.courses[0].name
				_enter("play", "res://scenes/gameplay.tscn")
		"play":
			var gameplay := get_tree().current_scene
			if gameplay == null or not gameplay.has_method("_song_time_at"):
				return
			if use_sim and not Settings.get_value("gameplay.autoplay", false) and not gameplay.get_meta("sim_started", false):
				gameplay.set_meta("sim_started", true)
				var start: float = Time.get_unix_time_from_system() - gameplay.song_time
				TrackerClient.send_command({"cmd": "sim_play_chart", "path": GameSession.chart.chart_path, "start_time": start}, _print_reply)
			if gameplay.song_time > song_seconds and not gameplay.finished:
				gameplay._finish()
			if gameplay.finished:
				phase = "results"
				phase_time = 0.0
				print("[demo] results: ", JSON.stringify(load("res://scripts/gameplay/results.gd").last))
		"results":
			if phase_time > 4.0:
				_enter("pov", "res://scenes/pov_view.tscn")
		"pov":
			_hit_pads(delta)
			if phase_time > 8.0:
				_enter("osu", "res://scenes/osu_mode.tscn")
		"osu":
			_hit_pads(delta)
			if phase_time > 6.0:
				print("[demo] done, tracker hits seen: ", _hits_seen)
				get_tree().quit(0)


## Calibration screen: capture the three world points, then hit pads on each tab.
func _drive_calibration(delta: float) -> void:
	if not use_sim:
		return
	var screen := get_tree().current_scene
	if screen == null or screen.get("tabs") == null or not screen.get("_built"):
		return
	var steps := [
		# Learn the room first, then the distance scale, then the axes - the
		# same order the calibration screen and the docs put them in.
		[1.0, func(): screen.tabs.current_tab = 1],
		[1.2, func(): TrackerClient.send_command({"cmd": "learn_background"}, _print_reply)],
		[2.4, func(): screen.tabs.current_tab = 3],
		[2.6, func(): TrackerClient.send_command({"cmd": "calibrate_distance_reset"})],
		[2.8, func(): _distance_sample(0.7)],
		[4.0, func(): _distance_sample(1.5)],
		[5.2, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": [0, 0, 0]})],
		[6.4, func(): TrackerClient.send_command({"cmd": "world_capture", "point": "origin", "controller": 0}, _print_reply)],
		[6.8, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": [0.4, 0, 0]})],
		[8.0, func(): TrackerClient.send_command({"cmd": "world_capture", "point": "right", "controller": 0}, _print_reply)],
		[8.4, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": [0, 0, -0.4]})],
		[9.6, func(): TrackerClient.send_command({"cmd": "world_capture", "point": "forward", "controller": 0}, _print_reply)],
		[10.0, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": [-0.25, 0.25, 0]})],
		[10.2, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 1, "position": [0.25, 0.25, 0]})],
		[10.6, func(): screen.tabs.current_tab = 4],
		[14.0, func(): screen.tabs.current_tab = 2],
	]
	while _step < steps.size() and phase_time >= steps[_step][0]:
		steps[_step][1].call()
		_step += 1
	if phase_time > 10.8:
		_hit_pads(delta)


## Makes the simulated controllers hit pads in turn so the screens show activity.
func _hit_pads(delta: float) -> void:
	if not use_sim:
		return
	_sim_timer += delta
	if _sim_timer > 0.45:
		_sim_timer = 0.0
		if _pad_ids.is_empty():
			TrackerClient.send_command({"cmd": "get_pads"}, func(reply):
				for pad in reply.get("pads", []):
					_pad_ids.append(str(pad.get("id", ""))))
			return
		var pad: String = _pad_ids[_sim_pad % _pad_ids.size()]
		TrackerClient.send_command({"cmd": "sim_hit", "pad": pad, "controller": _sim_pad % 2,
				"at": Time.get_unix_time_from_system() + 0.25})
		_sim_pad += 1


## Hold the controller a known distance in front of the lens and take a sample.
func _distance_sample(distance: float) -> void:
	TrackerClient.send_command({"cmd": "sim_truth"}, func(reply):
		var camera: Dictionary = reply.get("camera", {})
		var position: Array = camera.get("position", [0, 0, 0])
		var forward: Array = camera.get("forward", [0, 0, 1])
		var point := [
			float(position[0]) + float(forward[0]) * distance,
			float(position[1]) + float(forward[1]) * distance,
			float(position[2]) + float(forward[2]) * distance,
		]
		TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": point})
		await get_tree().create_timer(0.8).timeout
		TrackerClient.send_command({"cmd": "calibrate_distance", "controller": 0,
				"distance_m": distance}, _print_reply))


func _print_reply(reply: Dictionary) -> void:
	print("[demo] reply: ", JSON.stringify(reply).left(160))
