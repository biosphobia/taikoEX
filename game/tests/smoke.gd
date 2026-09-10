extends Node
## Headless smoke test: loads every script and scene, parses the demo chart.
## Run with:  godot --headless --path game res://tests/smoke.tscn

var failures := 0


func _ready() -> void:
	_check_scripts("res://autoload")
	_check_scripts("res://scripts")
	for scene in ["main_menu", "song_select", "settings_menu", "osu_mode", "calibration", "gameplay", "results"]:
		var packed := load("res://scenes/%s.tscn" % scene)
		_expect(packed != null and packed.can_instantiate(), "scene loads: " + scene)
	_check_tja()
	print("smoke test: %d failure(s)" % failures)
	get_tree().quit(1 if failures > 0 else 0)


func _check_scripts(folder: String) -> void:
	for file in DirAccess.get_files_at(folder):
		if file.ends_with(".gd"):
			var script = load(folder.path_join(file))
			_expect(script is GDScript and script.can_instantiate(), "script compiles: " + file)
	for sub in DirAccess.get_directories_at(folder):
		_check_scripts(folder.path_join(sub))


func _check_tja() -> void:
	var text := """TITLE:Test
BPM:120
OFFSET:-1.0
COURSE:Oni
LEVEL:3
BALLOON:4
#START
1020,
3,
#BPMCHANGE 240
1111,
#GOGOSTART
5000000000000080,
7008,
#BRANCHSTART p,50,75
#N
1000,
#M
2000,
#BRANCHEND
#END
"""
	var chart := TjaParser.parse(text)
	_expect(chart.title == "Test", "title parsed")
	_expect(chart.courses.size() == 1, "one course")
	var course := chart.courses[0]
	_expect(course.level == 3, "level parsed")
	var kinds := []
	var times := []
	for note in course.notes:
		kinds.append(note.kind())
		times.append(snapped(note.time, 0.001))
	_expect(kinds == ["don", "ka", "don", "don", "don", "don", "don", "roll", "balloon", "ka"], "note kinds " + str(kinds))
	_expect(times.slice(0, 3) == [1.0, 2.0, 3.0], "first note times " + str(times))
	_expect(times.slice(3, 7) == [5.0, 5.25, 5.5, 5.75], "bpm change applied " + str(times))
	_expect(course.notes[7].end_time > course.notes[7].time, "roll has an end")
	_expect(course.notes[8].balloon_count == 4, "balloon count")
	_expect(course.notes[8].gogo, "gogo flag")
	_expect(course.notes[9].kind() == "ka", "master branch chosen")
	var demo := TjaParser.load_file(ProjectSettings.globalize_path("res://data/songs/demo/demo.tja"))
	_expect(demo != null and demo.courses[0].notes.size() > 50, "demo chart loads")


func _expect(condition: bool, what: String) -> void:
	if condition:
		print("  ok   " + what)
	else:
		failures += 1
		print("  FAIL " + what)
