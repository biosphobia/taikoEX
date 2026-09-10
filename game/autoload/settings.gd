extends Node
## Game settings, stored in data/settings.json.
##
## DEFAULTS documents every setting.  The JSON file only needs the values
## you changed; everything else falls back to the defaults below.

signal changed(path: String, value: Variant)

const DEFAULTS := {
	"keys": {
		# Keyboard fallback / testing.  Any key name from OS.get_keycode_string().
		"left_ka": "D", "left_don": "F", "right_don": "J", "right_ka": "K",
	},
	"judgement": {
		"good_ms": 25.0,          # "Good" window (+/- milliseconds)
		"ok_ms": 75.0,            # "Ok" window
		"bad_ms": 108.0,          # anything inside this but outside "Ok" is a miss
		"big_note_pair_ms": 60.0, # both hands within this = big hit
		"roll_min_gap_ms": 20.0,  # ignore drum roll hits closer together than this
	},
	"scoring": {
		"good": 1000, "ok": 500, "big_multiplier": 2, "roll_hit": 100, "balloon_pop": 5000,
		"gauge_clear_ratio": 0.8,
	},
	"audio": {
		"music_volume": 0.8,
		"sfx_volume": 1.0,
		"offset_ms": 0.0,         # positive = notes are judged later (audio lags)
		"sync_to_audio_clock": true,  # steer the song clock by the audio position (off: pure wall clock)
	},
	"gameplay": {
		"note_speed": 1.0,        # multiplies the chart's scroll speed
		"autoplay": false,        # the game plays itself (demo / testing)
		"show_debug": false,
		"countdown_seconds": 2.0, # lead-in before the song starts
	},
	"tracker": {
		"host": "127.0.0.1",
		"state_port": 47820,
		"command_port": 47821,
		"preview_port": 47822,
		"auto_launch": true,      # start the tracker together with the game
		"input_offset_ms": 0.0,   # extra offset for tracker hits only
	},
	"update": {
		"repo": "biosphobia/taikoEX",
		"check_on_startup": true,
		"auto_install": true,     # download + apply without asking
	},
	"skin": "default",
	"video": {"fullscreen": false, "vsync": true},
}

var data: Dictionary = {}


func _ready() -> void:
	load_settings()
	apply_video()


func load_settings() -> void:
	var user_data = Paths.read_json(Paths.settings_file(), {})
	data = _merge(DEFAULTS.duplicate(true), user_data if user_data is Dictionary else {})


func save() -> void:
	Paths.write_json(Paths.settings_file(), data)


## Dotted lookup: Settings.get_value("judgement.good_ms")
func get_value(path: String, fallback: Variant = null) -> Variant:
	var node: Variant = data
	for part in path.split("."):
		if node is Dictionary and node.has(part):
			node = node[part]
		else:
			return fallback
	return node


func set_value(path: String, value: Variant, save_now: bool = true) -> void:
	var parts := path.split(".")
	var node: Dictionary = data
	for i in range(parts.size() - 1):
		if not node.has(parts[i]) or not (node[parts[i]] is Dictionary):
			node[parts[i]] = {}
		node = node[parts[i]]
	node[parts[-1]] = value
	changed.emit(path, value)
	if save_now:
		save()


func apply_video() -> void:
	var mode := DisplayServer.WINDOW_MODE_FULLSCREEN if get_value("video.fullscreen") else DisplayServer.WINDOW_MODE_WINDOWED
	DisplayServer.window_set_mode(mode)
	var vsync := DisplayServer.VSYNC_ENABLED if get_value("video.vsync") else DisplayServer.VSYNC_DISABLED
	DisplayServer.window_set_vsync_mode(vsync)


func key_for(action: String) -> Key:
	var name: String = get_value("keys." + action, "")
	return OS.find_keycode_from_string(name)


static func _merge(base: Dictionary, override: Dictionary) -> Dictionary:
	for key in override:
		if override[key] is Dictionary and base.get(key) is Dictionary:
			base[key] = _merge(base[key], override[key])
		else:
			base[key] = override[key]
	return base
