extends Control
## Title screen.

var status_label: Label
var update_button: Button


func _ready() -> void:
	UiKit.background(self)
	var box := VBoxContainer.new()
	box.set_anchors_preset(Control.PRESET_CENTER)
	box.grow_horizontal = Control.GROW_DIRECTION_BOTH
	box.grow_vertical = Control.GROW_DIRECTION_BOTH
	box.add_theme_constant_override("separation", 12)
	box.alignment = BoxContainer.ALIGNMENT_CENTER
	add_child(box)
	var title := UiKit.title("TaikoEX")
	title.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	box.add_child(title)
	var subtitle := UiKit.label("Taiko with PS Move controllers and a PS3 Eye", true)
	subtitle.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	box.add_child(subtitle)
	UiKit.spacer(box, 20)
	box.add_child(UiKit.button("Play", func(): UiKit.go_to("res://scenes/song_select.tscn"), 300))
	box.add_child(UiKit.button("Camera & drum calibration", func(): UiKit.go_to("res://scenes/calibration.tscn"), 300))
	box.add_child(UiKit.button("osu! input mode", func(): UiKit.go_to("res://scenes/osu_mode.tscn"), 300))
	box.add_child(UiKit.button("Settings", func(): UiKit.go_to("res://scenes/settings_menu.tscn"), 300))
	box.add_child(UiKit.button("Quit", func(): get_tree().quit(), 300))
	UiKit.spacer(box, 20)
	update_button = UiKit.button("Restart to install update", func(): Updater.apply_and_restart(), 300)
	update_button.visible = Updater.state == Updater.State.READY
	box.add_child(update_button)

	status_label = UiKit.label("", true)
	status_label.set_anchors_preset(Control.PRESET_BOTTOM_WIDE)
	status_label.offset_left = 20
	status_label.offset_top = -60
	status_label.offset_bottom = -20
	add_child(status_label)
	Updater.status_changed.connect(func(_text): _refresh_status())
	Updater.update_ready.connect(func(): update_button.visible = true)
	TrackerClient.connection_changed.connect(func(_connected): _refresh_status())
	_refresh_status()


func _refresh_status() -> void:
	var tracker := "tracker connected" if TrackerClient.connected else "tracker not connected (keyboard: D F J K)"
	status_label.text = "Version %s   |   %s   |   %s" % [Paths.version(), tracker, Updater.status_text]


func _process(_delta: float) -> void:
	if Engine.get_process_frames() % 30 == 0:
		_refresh_status()
