class_name Lane
extends Control
## Draws the scrolling note lane.  Everything here is pure drawing; the
## timing logic lives in gameplay.gd, which sets `song_time` every frame.

var course: Chart.Course
var song_time := 0.0
var gogo := false
var note_speed := 1.0

var _judge_x := 260.0
var _lane_height := 110.0
var _measure_width := 700.0
var _note_radius := 34.0
var _big_radius := 48.0
var _hit_flash := {"left": 0.0, "right": 0.0}


func _ready() -> void:
	_judge_x = GameSkin.size("judge_x")
	_lane_height = GameSkin.size("lane_height")
	_measure_width = GameSkin.size("measure_width")
	_note_radius = GameSkin.size("note_radius")
	_big_radius = GameSkin.size("big_note_radius")
	custom_minimum_size.y = _lane_height


func flash(side: String) -> void:
	_hit_flash[side] = 1.0


func _process(delta: float) -> void:
	for side in _hit_flash:
		_hit_flash[side] = max(0.0, _hit_flash[side] - delta * 6.0)
	queue_redraw()


## Pixels per second for a note: one 4/4 measure at 120 BPM spans measure_width.
func pixels_per_second(bpm: float, scroll: float) -> float:
	return _measure_width * bpm / 240.0 * scroll * note_speed


func x_for(time: float, bpm: float, scroll: float) -> float:
	return _judge_x + (time - song_time) * pixels_per_second(bpm, scroll)


func _draw() -> void:
	var width := size.x
	var center_y := _lane_height / 2.0
	# Lane background.
	var lane_colour := GameSkin.color("lane")
	if gogo:
		lane_colour = lane_colour.lerp(GameSkin.color("gogo"), 0.25)
	var lane_texture := GameSkin.texture("lane")
	if lane_texture:
		draw_texture_rect(lane_texture, Rect2(0, 0, width, _lane_height), false)
	else:
		draw_rect(Rect2(0, 0, width, _lane_height), GameSkin.color("lane_border"))
		draw_rect(Rect2(0, 6, width, _lane_height - 12), lane_colour)
	# Judge circle.
	draw_circle(Vector2(_judge_x, center_y), _big_radius + 4, GameSkin.color("judge_circle"))
	draw_arc(Vector2(_judge_x, center_y), _note_radius, 0, TAU, 48, GameSkin.color("judge_circle_outline"), 3.0)
	for side in _hit_flash:
		if _hit_flash[side] > 0.0:
			var colour := GameSkin.color("hit_left" if side == "left" else "hit_right")
			colour.a = _hit_flash[side] * 0.6
			draw_circle(Vector2(_judge_x, center_y), _big_radius + 6, colour)
	if course == null:
		return
	# Bar lines.
	for bar in course.barlines:
		if not bar.visible:
			continue
		var x := x_for(bar.time, bar.bpm, bar.scroll)
		if x < _judge_x - 100 or x > width + 10:
			continue
		draw_line(Vector2(x, 8), Vector2(x, _lane_height - 8), GameSkin.color("barline"), 2.0)
	# Notes, drawn last-to-first so earlier notes sit on top.
	for index in range(course.notes.size() - 1, -1, -1):
		var note: Chart.Note = course.notes[index]
		_draw_note(note, center_y, width)


func _draw_note(note: Chart.Note, center_y: float, width: float) -> void:
	var x := x_for(note.time, note.bpm, note.scroll)
	var radius := _big_radius if note.is_big() else _note_radius
	if note.is_long():
		var x_end := x_for(note.end_time, note.bpm, note.scroll)
		if x_end < -radius or x > width + radius:
			return
		if note.kind() == "roll":
			var colour := GameSkin.color("roll")
			draw_rect(Rect2(x, center_y - radius * 0.7, max(0.0, x_end - x), radius * 1.4), colour)
			draw_circle(Vector2(x_end, center_y), radius * 0.7, colour)
			_draw_round_note(Vector2(x, center_y), radius, colour, "roll_head")
		else:
			if note.popped:
				return
			var head_x: float = clamp(x, _judge_x, x_end) if x < _judge_x else x
			_draw_round_note(Vector2(head_x, center_y), radius, GameSkin.color("balloon"), "balloon")
			var remaining := note.balloon_count - note.roll_hits
			draw_string(ThemeDB.fallback_font, Vector2(head_x - 10, center_y + 8), str(remaining),
					HORIZONTAL_ALIGNMENT_CENTER, -1, 20, GameSkin.color("text"))
		return
	if note.judged and note.judgement != "bad":
		return
	if x < -radius or x > width + radius:
		return
	var colour := GameSkin.color(note.kind())
	var texture_name := note.kind() + ("_big" if note.is_big() else "")
	_draw_round_note(Vector2(x, center_y), radius, colour, texture_name)


func _draw_round_note(position: Vector2, radius: float, colour: Color, texture_name: String) -> void:
	var texture := GameSkin.texture(texture_name)
	if texture:
		draw_texture_rect(texture, Rect2(position - Vector2(radius, radius), Vector2(radius * 2, radius * 2)), false)
		return
	draw_circle(position, radius, GameSkin.color("note_outline"))
	draw_circle(position, radius - 4, colour)
	draw_circle(position + Vector2(-radius * 0.25, -radius * 0.25), radius * 0.22, GameSkin.color("note_inner"))
