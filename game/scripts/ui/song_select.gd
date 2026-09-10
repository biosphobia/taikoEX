extends Control
## Lists every chart under data/songs; pick a song and a course.

var entries: Array[SongLibrary.Entry] = []
var selected := 0
var course_index := 0
var list: ItemList
var info_label: Label
var course_option: OptionButton
var preview: AudioStreamPlayer


func _ready() -> void:
	UiKit.background(self)
	entries = SongLibrary.scan()
	var root := HBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.offset_left = 40
	root.offset_top = 40
	root.offset_right = -40
	root.offset_bottom = -40
	root.add_theme_constant_override("separation", 30)
	add_child(root)

	var left := UiKit.column(root)
	left.add_child(UiKit.title("Song select"))
	left.add_child(UiKit.label("Songs folder: " + Paths.songs_dir(), true))
	list = ItemList.new()
	list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	list.add_theme_font_size_override("font_size", 20)
	for entry in entries:
		list.add_item(entry.display_title())
	list.item_selected.connect(_on_selected)
	list.item_activated.connect(func(_index): _play())
	left.add_child(list)

	var right := UiKit.column(root)
	right.custom_minimum_size.x = 420
	right.size_flags_horizontal = Control.SIZE_SHRINK_END
	info_label = UiKit.label("")
	info_label.custom_minimum_size = Vector2(400, 160)
	right.add_child(info_label)
	course_option = OptionButton.new()
	course_option.item_selected.connect(func(index): course_index = index)
	right.add_child(course_option)
	UiKit.spacer(right)
	right.add_child(UiKit.button("Play  (Enter)", _play, 300))
	right.add_child(UiKit.button("Back  (Esc)", func(): UiKit.go_to("res://scenes/main_menu.tscn"), 300))
	UiKit.spacer(right)
	right.add_child(UiKit.label("Add songs by dropping a folder with a .tja chart and its audio (ogg / mp3 / wav) into the songs folder.", true))

	preview = AudioStreamPlayer.new()
	preview.volume_db = linear_to_db(float(Settings.get_value("audio.music_volume", 0.8)) * 0.6)
	add_child(preview)

	if entries.is_empty():
		info_label.text = "No songs found.\nPut a .tja chart and its audio file in:\n" + Paths.songs_dir()
	else:
		list.select(0)
		_on_selected(0)


func _on_selected(index: int) -> void:
	selected = index
	var chart := entries[index].chart
	var lines := [chart.title]
	if not chart.subtitle.is_empty():
		lines.append(chart.subtitle)
	lines.append("BPM %s" % str(chart.bpm))
	lines.append("")
	for course in chart.courses:
		lines.append("%s  -  level %d  -  %d notes" % [course.name, course.level, course.notes.size()])
	info_label.text = "\n".join(lines)
	course_option.clear()
	for course in chart.courses:
		course_option.add_item(course.name)
	course_index = min(course_index, chart.courses.size() - 1)
	course_option.select(course_index)
	preview.stream = SongLibrary.load_audio(chart)
	if preview.stream != null:
		preview.play(chart.demo_start)


func _play() -> void:
	if entries.is_empty():
		return
	GameSession.chart = entries[selected].chart
	GameSession.course_name = entries[selected].chart.courses[course_index].name
	UiKit.go_to("res://scenes/gameplay.tscn")


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		UiKit.go_to("res://scenes/main_menu.tscn")
	elif event.is_action_pressed("ui_accept"):
		_play()
	elif event.is_action_pressed("ui_right") and course_option.item_count > 0:
		course_index = (course_index + 1) % course_option.item_count
		course_option.select(course_index)
	elif event.is_action_pressed("ui_left") and course_option.item_count > 0:
		course_index = (course_index - 1 + course_option.item_count) % course_option.item_count
		course_option.select(course_index)
