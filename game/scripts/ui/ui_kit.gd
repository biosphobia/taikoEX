class_name UiKit
extends RefCounted
## Small helpers to build menus in code.  Every screen in the game uses
## these, so changing the look here changes it everywhere.

const FONT_SIZE_TITLE := 40
const FONT_SIZE_HEADING := 22
const FONT_SIZE_BODY := 16


static func title(text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", FONT_SIZE_TITLE)
	label.add_theme_color_override("font_color", GameSkin.color("text"))
	return label


static func heading(text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", FONT_SIZE_HEADING)
	label.add_theme_color_override("font_color", GameSkin.color("good"))
	return label


static func label(text: String, dim: bool = false) -> Label:
	var node := Label.new()
	node.text = text
	node.add_theme_font_size_override("font_size", FONT_SIZE_BODY)
	node.add_theme_color_override("font_color", GameSkin.color("text_dim" if dim else "text"))
	node.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return node


static func button(text: String, on_pressed: Callable, min_width: float = 220.0) -> Button:
	var node := Button.new()
	node.text = text
	node.custom_minimum_size = Vector2(min_width, 40)
	node.add_theme_font_size_override("font_size", FONT_SIZE_BODY + 2)
	node.pressed.connect(on_pressed)
	return node


static func row(parent: Control, spacing: int = 12) -> HBoxContainer:
	var box := HBoxContainer.new()
	box.add_theme_constant_override("separation", spacing)
	parent.add_child(box)
	return box


static func column(parent: Control, spacing: int = 8) -> VBoxContainer:
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", spacing)
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	parent.add_child(box)
	return box


static func spacer(parent: Control, height: float = 12.0) -> Control:
	var node := Control.new()
	node.custom_minimum_size = Vector2(0, height)
	parent.add_child(node)
	return node


## A labelled slider with a live value read-out.  `on_change(value)` is called
## when the user releases the slider (or on every change if `live` is true).
static func slider(parent: Control, text: String, minimum: float, maximum: float, step: float,
		value: float, on_change: Callable, live: bool = false) -> HSlider:
	var box := row(parent)
	var name := label(text)
	name.custom_minimum_size.x = 200
	box.add_child(name)
	var node := HSlider.new()
	node.min_value = minimum
	node.max_value = maximum
	node.step = step
	node.value = value
	node.custom_minimum_size.x = 220
	node.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(node)
	var readout := label(_format(value), true)
	readout.custom_minimum_size.x = 70
	box.add_child(readout)
	node.value_changed.connect(func(v): readout.text = _format(v))
	if live:
		node.value_changed.connect(func(v): on_change.call(v))
	else:
		# Only apply once the mouse is released; keyboard / click changes apply at once.
		var dragging := [false]
		node.drag_started.connect(func(): dragging[0] = true)
		node.drag_ended.connect(func(changed): dragging[0] = false; if changed: on_change.call(node.value))
		node.value_changed.connect(func(v): if not dragging[0]: on_change.call(v))
	return node


static func spin(parent: Control, text: String, minimum: float, maximum: float, step: float,
		value: float, on_change: Callable) -> SpinBox:
	var box := row(parent)
	var name := label(text)
	name.custom_minimum_size.x = 200
	box.add_child(name)
	var node := SpinBox.new()
	node.min_value = minimum
	node.max_value = maximum
	node.step = step
	node.value = value
	node.custom_minimum_size.x = 140
	node.value_changed.connect(func(v): on_change.call(v))
	box.add_child(node)
	return node


static func check(parent: Control, text: String, value: bool, on_change: Callable) -> CheckBox:
	var node := CheckBox.new()
	node.text = text
	node.button_pressed = value
	node.add_theme_font_size_override("font_size", FONT_SIZE_BODY)
	node.toggled.connect(func(v): on_change.call(v))
	parent.add_child(node)
	return node


static func option(parent: Control, text: String, options: Array, current: Variant, on_change: Callable) -> OptionButton:
	var box := row(parent)
	var name := label(text)
	name.custom_minimum_size.x = 200
	box.add_child(name)
	var node := OptionButton.new()
	for index in range(options.size()):
		node.add_item(str(options[index]), index)
		if options[index] == current:
			node.select(index)
	node.item_selected.connect(func(index): on_change.call(options[index]))
	box.add_child(node)
	return node


static func text_field(parent: Control, text: String, value: String, on_change: Callable) -> LineEdit:
	var box := row(parent)
	var name := label(text)
	name.custom_minimum_size.x = 200
	box.add_child(name)
	var node := LineEdit.new()
	node.text = value
	node.custom_minimum_size.x = 200
	node.text_submitted.connect(func(v): on_change.call(v))
	node.focus_exited.connect(func(): on_change.call(node.text))
	box.add_child(node)
	return node


static func scroll_panel(parent: Control) -> VBoxContainer:
	var scroll := ScrollContainer.new()
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	parent.add_child(scroll)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(box)
	return box


static func background(parent: Control) -> ColorRect:
	var rect := ColorRect.new()
	rect.color = GameSkin.color("background")
	rect.set_anchors_preset(Control.PRESET_FULL_RECT)
	rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	parent.add_child(rect)
	parent.move_child(rect, 0)
	return rect


static func _format(value: float) -> String:
	return str(int(value)) if is_equal_approx(value, round(value)) else "%.2f" % value


static func go_to(scene_path: String) -> void:
	var tree := Engine.get_main_loop() as SceneTree
	tree.change_scene_to_file.call_deferred(scene_path)
