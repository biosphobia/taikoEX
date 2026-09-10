extends Control
## osu! input mode: the tracker types keyboard keys for every drum hit so
## you can play osu!taiko (or anything else) with the virtual drums.
## This screen just switches the mode on/off, shows the mapping and a live
## hit log.  You can also start the tracker alone with `--osu`.

var enabled := false
var toggle_button: Button
var log_label: Label
var status_label: Label
var pad_view: Control
var _log: Array[String] = []
var _key_fields: Dictionary = {}


func _ready() -> void:
	UiKit.background(self)
	var root := VBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.offset_left = 40
	root.offset_top = 30
	root.offset_right = -40
	root.offset_bottom = -30
	root.add_theme_constant_override("separation", 10)
	add_child(root)
	root.add_child(UiKit.title("osu! input mode"))
	root.add_child(UiKit.label("While enabled, every pad hit is typed as a key (works in osu!taiko with the default D F J K layout). Keep this window or the tracker running, then alt-tab to osu!.", true))
	status_label = UiKit.label("")
	root.add_child(status_label)

	var columns := UiKit.row(root, 30)
	var left := UiKit.column(columns)
	toggle_button = UiKit.button("Enable osu! mode", _toggle, 260)
	left.add_child(toggle_button)
	left.add_child(UiKit.heading("Key mapping"))
	for pad in ["left_ka", "left_don", "right_don", "right_ka"]:
		_key_fields[pad] = UiKit.text_field(left, pad, "", func(v): _set_key(pad, v))
	UiKit.spin(left, "Key hold time (ms)", 5, 200, 1, 35, func(v): TrackerClient.set_config({"osu": {"key_hold_ms": v}}))
	UiKit.spacer(left)
	left.add_child(UiKit.button("Back  (Esc)", func(): UiKit.go_to("res://scenes/main_menu.tscn"), 260))

	var right := UiKit.column(columns)
	right.add_child(UiKit.heading("Live hits"))
	log_label = UiKit.label("")
	log_label.custom_minimum_size = Vector2(400, 300)
	right.add_child(log_label)

	TrackerClient.hit_received.connect(_on_hit)
	TrackerClient.send_command({"cmd": "get_config"}, _on_config)
	_refresh()


func _on_config(reply: Dictionary) -> void:
	var osu: Dictionary = reply.get("config", {}).get("osu", {})
	enabled = bool(osu.get("enabled", false))
	for pad in _key_fields:
		_key_fields[pad].text = str(osu.get("keys", {}).get(pad, ""))
	_refresh()


func _toggle() -> void:
	enabled = not enabled
	TrackerClient.set_osu_mode(enabled)
	_refresh()


func _set_key(pad: String, key: String) -> void:
	TrackerClient.send_command({"cmd": "set_osu", "enabled": enabled, "keys": {pad: key}, "save": true})


func _on_hit(hit: Dictionary) -> void:
	_log.push_front("%s  (%s hand, %.1f m/s)" % [hit.get("pad", "?"), "left" if int(hit.get("controller", 0)) == 0 else "right", float(hit.get("speed", 0))])
	if _log.size() > 14:
		_log.resize(14)
	log_label.text = "\n".join(_log)


func _refresh() -> void:
	toggle_button.text = "Disable osu! mode" if enabled else "Enable osu! mode"
	status_label.text = ("osu! mode is ON - keys are being sent" if enabled else "osu! mode is off") + \
			("" if TrackerClient.connected else "   (tracker not connected)")


func _process(_delta: float) -> void:
	if Engine.get_process_frames() % 30 == 0:
		_refresh()


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		UiKit.go_to("res://scenes/main_menu.tscn")
