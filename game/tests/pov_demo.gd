extends Node
## Scripts the 3D view for a demo recording: look around, move the drum, resize
## it, and cycle the viewpoints.  Run with:
##
##   godot --path game res://tests/pov_demo.tscn -- ++seconds=40

var seconds := 40.0
var elapsed := 0.0
var step := 0
var view: Node3D
var caption := ""


func _ready() -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("++seconds="):
			seconds = float(arg.get_slice("=", 1))
		elif arg.begins_with("++title="):
			caption = arg.substr("++title=".length()).replace("~", " ")
	view = get_parent().get_node_or_null("PovView")
	if not caption.is_empty():
		_show_caption()


func _show_caption() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var label := UiKit.heading(caption)
	label.set_anchors_preset(Control.PRESET_TOP_WIDE)
	label.offset_left = 20
	label.offset_top = 118
	label.offset_right = -360      # clear of the camera view in the corner
	label.offset_bottom = 190
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_color_override("font_color", GameSkin.color("good"))
	layer.add_child(label)


func _process(delta: float) -> void:
	if view == null:
		return
	elapsed += delta
	var script := _script()
	while step < script.size() and elapsed >= script[step][0]:
		script[step][1].call()
		step += 1
	if elapsed > seconds:
		get_tree().quit(0)


func _script() -> Array:
	return [
		[4.0, func(): _tap(KEY_D, 6)],            # slide the drum right...
		[6.0, func(): _tap(KEY_A, 12)],           # ...and back past centre to the left
		[8.5, func(): _tap(KEY_D, 6)],            # centred again
		[10.5, func(): _tap(KEY_E, 5)],           # raise it
		[12.5, func(): _tap(KEY_Q, 5)],           # and lower it back
		[14.5, func(): _tap(KEY_W, 5)],           # push it away
		[16.5, func(): _tap(KEY_S, 5)],           # pull it back
		[18.5, func(): _tap(KEY_BRACKETRIGHT, 4)],  # bigger
		[20.5, func(): _tap(KEY_BRACKETLEFT, 4)],   # and back
		[22.5, func(): _tap(KEY_LEFT, 8)],        # look around
		[25.0, func(): _tap(KEY_RIGHT, 16)],
		[28.0, func(): _tap(KEY_LEFT, 8)],
		[30.0, func(): _tap(KEY_TAB, 1)],         # side view
		[34.0, func(): _tap(KEY_TAB, 1)],         # the camera's own view
		[38.0, func(): _tap(KEY_TAB, 1)],         # back to the player's view
	]


func _tap(keycode: Key, times: int) -> void:
	for index in range(times):
		var event := InputEventKey.new()
		event.keycode = keycode
		event.pressed = true
		view._unhandled_input(event)
