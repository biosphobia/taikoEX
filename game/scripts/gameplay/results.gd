extends Control
## Results screen.  gameplay.gd stores its numbers in `last` before switching here.

static var last: Dictionary = {}


func _ready() -> void:
	UiKit.background(self)
	var box := VBoxContainer.new()
	box.set_anchors_preset(Control.PRESET_CENTER)
	box.grow_horizontal = Control.GROW_DIRECTION_BOTH
	box.grow_vertical = Control.GROW_DIRECTION_BOTH
	box.add_theme_constant_override("separation", 10)
	add_child(box)
	box.add_child(UiKit.title(last.get("title", "Results")))
	box.add_child(UiKit.label(str(last.get("course", "")) + "   -   " + ("CLEAR" if last.get("cleared", false) else "FAILED"), true))
	UiKit.spacer(box)
	var grid := GridContainer.new()
	grid.columns = 2
	grid.add_theme_constant_override("h_separation", 40)
	box.add_child(grid)
	for pair in [["Score", "score"], ["Good", "good"], ["Ok", "ok"], ["Miss", "bad"],
			["Max combo", "max_combo"], ["Roll hits", "roll_hits"], ["Accuracy", "accuracy"], ["Timing", "timing"]]:
		grid.add_child(UiKit.label(pair[0], true))
		grid.add_child(UiKit.label(str(last.get(pair[1], 0))))
	UiKit.spacer(box)
	var buttons := UiKit.row(box)
	buttons.add_child(UiKit.button("Play again", func(): UiKit.go_to("res://scenes/gameplay.tscn")))
	buttons.add_child(UiKit.button("Song select", func(): UiKit.go_to("res://scenes/song_select.tscn")))


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel") or event.is_action_pressed("ui_accept"):
		UiKit.go_to("res://scenes/song_select.tscn")
