class_name TjaParser
extends RefCounted
## Reads .tja chart files (the common community format for Taiko charts).
##
## Supported: TITLE, SUBTITLE, BPM, WAVE, OFFSET, DEMOSTART, SONGVOL, COURSE,
## LEVEL, BALLOON, #START/#END, #BPMCHANGE, #MEASURE, #SCROLL, #DELAY,
## #GOGOSTART/#GOGOEND, #BARLINEOFF/#BARLINEON, and branches (#BRANCHSTART,
## #N/#E/#M, #BRANCHEND) where the master branch is used.

const COURSE_NAMES := {"0": "Easy", "1": "Normal", "2": "Hard", "3": "Oni", "4": "Edit",
		"easy": "Easy", "normal": "Normal", "hard": "Hard", "oni": "Oni", "edit": "Edit", "ura": "Edit"}


static func load_file(path: String) -> Chart:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		push_error("Cannot open chart " + path)
		return null
	var chart := parse(file.get_as_text())
	chart.chart_path = path
	return chart


static func parse(text: String) -> Chart:
	var chart := Chart.new()
	var course_header := {"name": "Oni", "level": 0, "balloons": []}
	var chart_lines: Array[String] = []
	var in_chart := false

	for raw_line in text.split("\n"):
		var line := raw_line.split("//")[0].strip_edges()
		if line.is_empty():
			continue
		if in_chart:
			if line.to_upper().begins_with("#END"):
				in_chart = false
				chart.courses.append(_build_course(chart, course_header, chart_lines))
				chart_lines = []
			else:
				chart_lines.append(line)
			continue
		if line.to_upper().begins_with("#START"):
			in_chart = true
			continue
		var colon := line.find(":")
		if colon < 0:
			continue
		var key := line.substr(0, colon).strip_edges().to_upper()
		var value := line.substr(colon + 1).strip_edges()
		match key:
			"TITLE": chart.title = value
			"SUBTITLE": chart.subtitle = value.trim_prefix("--").trim_prefix("++")
			"BPM": chart.bpm = _to_float(value, 120.0)
			"WAVE": chart.wave = value
			"OFFSET": chart.offset = _to_float(value, 0.0)
			"DEMOSTART": chart.demo_start = _to_float(value, 0.0)
			"SONGVOL": chart.song_volume = _to_float(value, 100.0)
			"COURSE":
				course_header = {"name": COURSE_NAMES.get(value.to_lower(), value), "level": 0, "balloons": []}
			"LEVEL": course_header["level"] = int(_to_float(value, 0.0))
			"BALLOON":
				var counts: Array = []
				for item in value.split(","):
					if not item.strip_edges().is_empty():
						counts.append(int(item.strip_edges()))
				course_header["balloons"] = counts
	if in_chart:
		chart.courses.append(_build_course(chart, course_header, chart_lines))
	if chart.courses.is_empty():
		chart.courses.append(_build_course(chart, course_header, []))
	return chart


static func _build_course(chart: Chart, header: Dictionary, lines: Array[String]) -> Chart.Course:
	var course := Chart.Course.new()
	course.name = header["name"]
	course.level = header["level"]
	var balloons: Array = header["balloons"].duplicate()
	var state := {
		"time": -chart.offset, "bpm": chart.bpm, "scroll": 1.0, "gogo": false,
		"measure_num": 4, "measure_den": 4, "barlines": true,
		"open_roll": null, "measure_notes": "", "measure_commands": [],
	}
	for line in _select_branch(lines):
		if line.begins_with("#"):
			var parts := line.split(" ", false)
			var command := parts[0].to_upper()
			var argument := parts[1] if parts.size() > 1 else ""
			# Commands inside a measure take effect at the note they precede.
			state["measure_commands"].append([state["measure_notes"].length(), command, argument])
			continue
		var digits := ""
		for character in line:
			if character >= "0" and character <= "9":
				digits += character
		state["measure_notes"] += digits
		if line.ends_with(","):
			_flush_measure(course, state, balloons)
	if not state["measure_notes"].is_empty() or not state["measure_commands"].is_empty():
		_flush_measure(course, state, balloons)
	return course


## Applies one measure worth of notes and commands, advancing the clock.
static func _flush_measure(course: Chart.Course, state: Dictionary, balloons: Array) -> void:
	var cells: String = state["measure_notes"]
	var commands: Array = state["measure_commands"]
	var cell_count := cells.length()
	var bar := Chart.Barline.new()
	bar.time = state["time"]
	bar.scroll = state["scroll"]
	bar.bpm = state["bpm"]
	bar.visible = state["barlines"]
	# Commands at cell index 0 (before any note) also affect the bar line.
	for entry in commands:
		if entry[0] == 0:
			_apply_command(state, entry[1], entry[2])
	bar.time = state["time"]
	bar.scroll = state["scroll"]
	bar.bpm = state["bpm"]
	bar.visible = state["barlines"]
	course.barlines.append(bar)

	if cell_count == 0:
		state["time"] += _measure_seconds(state)
	for index in range(cell_count):
		for entry in commands:
			if entry[0] == index and index > 0:
				_apply_command(state, entry[1], entry[2])
		var cell := cells[index]
		var note_time: float = state["time"]
		var type := int(cell)
		if type == 8:
			var open: Chart.Note = state["open_roll"]
			if open != null:
				open.end_time = note_time
				state["open_roll"] = null
		elif type != 0:
			var note := Chart.Note.new()
			note.type = type
			note.time = note_time
			note.scroll = state["scroll"]
			note.bpm = state["bpm"]
			note.gogo = state["gogo"]
			if note.is_long():
				note.end_time = note_time
				state["open_roll"] = note
				if note.kind() == "balloon":
					note.balloon_count = int(balloons.pop_front()) if not balloons.is_empty() else 5
			course.notes.append(note)
		state["time"] += _measure_seconds(state) / cell_count
	state["measure_notes"] = ""
	state["measure_commands"] = []


static func _apply_command(state: Dictionary, command: String, argument: String) -> void:
	match command:
		"#BPMCHANGE": state["bpm"] = _to_float(argument, state["bpm"])
		"#SCROLL": state["scroll"] = _to_float(argument, 1.0)
		"#DELAY": state["time"] += _to_float(argument, 0.0)
		"#GOGOSTART": state["gogo"] = true
		"#GOGOEND": state["gogo"] = false
		"#BARLINEOFF": state["barlines"] = false
		"#BARLINEON": state["barlines"] = true
		"#MEASURE":
			var parts := argument.split("/")
			if parts.size() == 2:
				state["measure_num"] = int(parts[0])
				state["measure_den"] = int(parts[1])


static func _measure_seconds(state: Dictionary) -> float:
	return 60.0 / state["bpm"] * 4.0 * float(state["measure_num"]) / float(state["measure_den"])


## Keeps only one branch of every #BRANCHSTART block (master, else expert, else normal).
static func _select_branch(lines: Array[String]) -> Array[String]:
	var result: Array[String] = []
	var in_branch := false
	var sections := {}
	var current := ""
	for line in lines:
		var upper := line.to_upper()
		if upper.begins_with("#BRANCHSTART"):
			in_branch = true
			sections = {}
			current = ""
			continue
		if in_branch and (upper == "#BRANCHEND" or upper.begins_with("#BRANCHSTART")):
			result.append_array(_pick_section(sections))
			in_branch = false
			continue
		if in_branch and upper in ["#N", "#E", "#M"]:
			current = upper
			sections[current] = []
			continue
		if in_branch:
			if upper in ["#SECTION", "#LEVELHOLD"]:
				continue
			if current.is_empty():
				result.append(line)
			else:
				sections[current].append(line)
		elif upper not in ["#SECTION", "#LEVELHOLD"]:
			result.append(line)
	if in_branch:
		result.append_array(_pick_section(sections))
	return result


static func _pick_section(sections: Dictionary) -> Array[String]:
	var typed: Array[String] = []
	for label in ["#M", "#E", "#N"]:
		if sections.has(label):
			typed.assign(sections[label])
			return typed
	return typed


static func _to_float(value: String, fallback: float) -> float:
	value = value.strip_edges()
	return float(value) if value.is_valid_float() else fallback
