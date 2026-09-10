extends Node
## Turns keyboard presses and tracker hits into one `drum_hit` signal.
##
## kind: "don" or "ka"; side: "left" or "right".
## unix_time: when the hit physically happened (tracker hits carry their own
## camera timestamp, so camera latency is already accounted for).

signal drum_hit(kind: String, side: String, unix_time: float, strength: float, source: String)

const ACTIONS := ["left_ka", "left_don", "right_don", "right_ka"]


func _ready() -> void:
	TrackerClient.hit_received.connect(_on_tracker_hit)
	process_mode = Node.PROCESS_MODE_ALWAYS


func _unhandled_input(event: InputEvent) -> void:
	if not (event is InputEventKey) or not event.pressed or event.echo:
		return
	for action in ACTIONS:
		if event.keycode == Settings.key_for(action) or event.physical_keycode == Settings.key_for(action):
			var parts: PackedStringArray = action.split("_")
			drum_hit.emit(parts[1], parts[0], Time.get_unix_time_from_system(), 1.0, "keyboard")
			return


func _on_tracker_hit(hit: Dictionary) -> void:
	var offset := float(Settings.get_value("tracker.input_offset_ms", 0.0)) / 1000.0
	drum_hit.emit(str(hit.get("kind", "don")), str(hit.get("side", "left")),
			float(hit.get("t", Time.get_unix_time_from_system())) + offset,
			float(hit.get("strength", 1.0)), "tracker")
