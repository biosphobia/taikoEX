class_name Chart
extends RefCounted
## In-memory representation of a song chart (loaded from a .tja file).


class Note:
	extends RefCounted
	## TJA note types: 1 don, 2 ka, 3 big don, 4 big ka, 5 roll, 6 big roll, 7 balloon, 9 kusudama.
	var type: int = 1
	var time: float = 0.0          # seconds from audio start
	var end_time: float = 0.0      # rolls / balloons only
	var scroll: float = 1.0        # #SCROLL multiplier
	var bpm: float = 120.0
	var gogo: bool = false
	var balloon_count: int = 0

	# Runtime state used while playing.
	var judged: bool = false
	var judgement: String = ""     # "good", "ok", "bad"
	var roll_hits: int = 0
	var first_hit_side: String = ""
	var first_hit_time: float = 0.0
	var popped: bool = false

	func kind() -> String:
		match type:
			1, 3: return "don"
			2, 4: return "ka"
			5, 6: return "roll"
			7, 9: return "balloon"
		return "don"

	func is_big() -> bool:
		return type == 3 or type == 4 or type == 6

	func is_hittable() -> bool:
		return type in [1, 2, 3, 4]

	func is_long() -> bool:
		return type in [5, 6, 7, 9]


class Barline:
	extends RefCounted
	var time: float = 0.0
	var scroll: float = 1.0
	var bpm: float = 120.0
	var visible: bool = true


class Course:
	extends RefCounted
	var name: String = "Oni"       # Easy, Normal, Hard, Oni, Edit
	var level: int = 0
	var notes: Array[Note] = []
	var barlines: Array[Barline] = []

	func hittable_count() -> int:
		var count := 0
		for note in notes:
			if note.is_hittable():
				count += 1
		return count

	func last_time() -> float:
		var last := 0.0
		for note in notes:
			last = max(last, note.time, note.end_time)
		for bar in barlines:
			last = max(last, bar.time)
		return last


var title := ""
var subtitle := ""
var wave := ""                 # audio file, relative to the chart
var chart_path := ""
var bpm := 120.0
var offset := 0.0
var demo_start := 0.0
var song_volume := 100.0
var courses: Array[Course] = []


func audio_path() -> String:
	return chart_path.get_base_dir().path_join(wave)


func course_named(name: String) -> Course:
	for course in courses:
		if course.name.to_lower() == name.to_lower():
			return course
	return courses[0] if not courses.is_empty() else null


## Fresh copy of a course's notes so a chart can be played more than once.
static func reset_course(course: Course) -> void:
	for note in course.notes:
		note.judged = false
		note.judgement = ""
		note.roll_hits = 0
		note.first_hit_side = ""
		note.first_hit_time = 0.0
		note.popped = false
