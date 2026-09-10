extends Control
## Game settings screen.  Every control writes straight into Settings
## (data/settings.json).  Tracker / camera settings live in the calibration screen.

var _listening_for: String = ""
var _key_buttons: Dictionary = {}


func _ready() -> void:
	UiKit.background(self)
	var root := VBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.offset_left = 40
	root.offset_top = 30
	root.offset_right = -40
	root.offset_bottom = -30
	add_child(root)
	root.add_child(UiKit.title("Settings"))
	root.add_child(UiKit.label("Stored in " + Paths.settings_file() + " - edit it by hand if you prefer.", true))
	var box := UiKit.scroll_panel(root)

	box.add_child(UiKit.heading("Keyboard (fallback / testing)"))
	var keys := UiKit.row(box)
	for action in InputRouter.ACTIONS:
		var button := UiKit.button("%s: %s" % [action, Settings.get_value("keys." + action)], func(): _listen(action), 200)
		_key_buttons[action] = button
		keys.add_child(button)

	box.add_child(UiKit.heading("Judgement windows (ms)"))
	for pair in [["Good", "judgement.good_ms", 5, 60], ["Ok", "judgement.ok_ms", 20, 150],
			["Bad (miss beyond this)", "judgement.bad_ms", 50, 250], ["Big note two-hand window", "judgement.big_note_pair_ms", 10, 150]]:
		UiKit.slider(box, pair[0], pair[2], pair[3], 1, Settings.get_value(pair[1]), func(v): Settings.set_value(pair[1], v))

	box.add_child(UiKit.heading("Timing"))
	UiKit.slider(box, "Audio offset (ms)", -200, 200, 1, Settings.get_value("audio.offset_ms"), func(v): Settings.set_value("audio.offset_ms", v))
	UiKit.slider(box, "Tracker input offset (ms)", -200, 200, 1, Settings.get_value("tracker.input_offset_ms"), func(v): Settings.set_value("tracker.input_offset_ms", v))

	box.add_child(UiKit.heading("Audio"))
	UiKit.slider(box, "Music volume", 0, 1, 0.05, Settings.get_value("audio.music_volume"), func(v): Settings.set_value("audio.music_volume", v))
	UiKit.slider(box, "Hit sound volume", 0, 1, 0.05, Settings.get_value("audio.sfx_volume"), func(v): Settings.set_value("audio.sfx_volume", v))
	UiKit.check(box, "Keep the song clock locked to the audio position (turn off if the notes drift)", Settings.get_value("audio.sync_to_audio_clock"), func(v): Settings.set_value("audio.sync_to_audio_clock", v))

	box.add_child(UiKit.heading("Gameplay"))
	UiKit.slider(box, "Note speed", 0.5, 3.0, 0.1, Settings.get_value("gameplay.note_speed"), func(v): Settings.set_value("gameplay.note_speed", v))
	UiKit.check(box, "Autoplay (the game plays itself)", Settings.get_value("gameplay.autoplay"), func(v): Settings.set_value("gameplay.autoplay", v))
	UiKit.check(box, "Show debug info during play", Settings.get_value("gameplay.show_debug"), func(v): Settings.set_value("gameplay.show_debug", v))

	box.add_child(UiKit.heading("Video"))
	UiKit.check(box, "Fullscreen", Settings.get_value("video.fullscreen"), func(v): Settings.set_value("video.fullscreen", v); Settings.apply_video())
	UiKit.check(box, "V-Sync", Settings.get_value("video.vsync"), func(v): Settings.set_value("video.vsync", v); Settings.apply_video())

	box.add_child(UiKit.heading("Tracker"))
	UiKit.check(box, "Start the tracker together with the game", Settings.get_value("tracker.auto_launch"), func(v): Settings.set_value("tracker.auto_launch", v))
	UiKit.text_field(box, "Tracker host", Settings.get_value("tracker.host"), func(v): Settings.set_value("tracker.host", v))
	UiKit.spin(box, "State port", 1024, 65535, 1, Settings.get_value("tracker.state_port"), func(v): Settings.set_value("tracker.state_port", int(v)))
	UiKit.spin(box, "Command port", 1024, 65535, 1, Settings.get_value("tracker.command_port"), func(v): Settings.set_value("tracker.command_port", int(v)))
	UiKit.spin(box, "Preview port", 1024, 65535, 1, Settings.get_value("tracker.preview_port"), func(v): Settings.set_value("tracker.preview_port", int(v)))

	box.add_child(UiKit.heading("Updates"))
	UiKit.check(box, "Check GitHub for new builds at start", Settings.get_value("update.check_on_startup"), func(v): Settings.set_value("update.check_on_startup", v))
	UiKit.check(box, "Install updates automatically", Settings.get_value("update.auto_install"), func(v): Settings.set_value("update.auto_install", v))
	UiKit.text_field(box, "GitHub repository", Settings.get_value("update.repo"), func(v): Settings.set_value("update.repo", v))
	box.add_child(UiKit.button("Check now", func(): Updater.check_for_update(), 200))

	UiKit.spacer(root)
	root.add_child(UiKit.button("Back  (Esc)", func(): UiKit.go_to("res://scenes/main_menu.tscn")))


func _listen(action: String) -> void:
	_listening_for = action
	_key_buttons[action].text = "%s: press a key..." % action


func _unhandled_input(event: InputEvent) -> void:
	if not _listening_for.is_empty():
		if event is InputEventKey and event.pressed:
			var name := OS.get_keycode_string(event.keycode)
			Settings.set_value("keys." + _listening_for, name)
			_key_buttons[_listening_for].text = "%s: %s" % [_listening_for, name]
			_listening_for = ""
			get_viewport().set_input_as_handled()
		return
	if event.is_action_pressed("ui_cancel"):
		UiKit.go_to("res://scenes/main_menu.tscn")
