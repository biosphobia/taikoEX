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
			if phase_time > 16.0:
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
		[1.0, func(): screen.tabs.current_tab = 3],
		[1.5, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": [0, 0, 0]})],
		[2.5, func(): TrackerClient.send_command({"cmd": "world_capture", "point": "origin", "controller": 0}, _print_reply)],
		[3.0, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": [0.4, 0, 0]})],
		[4.0, func(): TrackerClient.send_command({"cmd": "world_capture", "point": "right", "controller": 0}, _print_reply)],
		[4.5, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": [0, 0, -0.4]})],
		[5.5, func(): TrackerClient.send_command({"cmd": "world_capture", "point": "forward", "controller": 0}, _print_reply)],
		[6.0, func(): TrackerClient.send_command({"cmd": "sim_goto", "controller": 0, "position": [-0.2, 0.2, 0]})],
		[6.5, func(): screen.tabs.current_tab = 4],
		[10.5, func(): screen.tabs.current_tab = 2],
		[13.0, func(): screen.tabs.current_tab = 1],
	]
	while _step < steps.size() and phase_time >= steps[_step][0]:
		steps[_step][1].call()
		_step += 1
	if phase_time > 6.5:
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


func _print_reply(reply: Dictionary) -> void:
	print("[demo] reply: ", JSON.stringify(reply).left(160))
