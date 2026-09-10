extends Control
## The actual game: plays the song, judges hits, keeps score.
##
## Time-line: song_time is seconds since the audio started (negative during
## the count-in).  Hits arrive as unix timestamps from InputRouter and are
## converted with `_song_time_at(unix_time)`, so tracker latency does not
## shift the judgement.

const RESULTS_SCENE := "res://scenes/results.tscn"

var course: Chart.Course
var chart: Chart
var song_time := -2.0
var started := false
var finished := false
var autoplay := false

# Score keeping.
var score := 0
var combo := 0
var max_combo := 0
var counts := {"good": 0, "ok": 0, "bad": 0}
var roll_hits := 0
var gauge := 0.0                # 0..1

# Timing windows in seconds (from settings).
var good_window := 0.025
var ok_window := 0.075
var bad_window := 0.108
var pair_window := 0.06
var roll_gap := 0.02
var audio_offset := 0.0
var sync_to_audio := true

var _next_hittable := 0         # index of the first note that can still be judged
var _hit_deltas: Array[float] = []   # hit time - note time, for the timing statistics on the results screen
var _last_roll_hit_time := -1.0
var _clock_anchor_wall := -1.0
var _clock_anchor_song := 0.0
var _audio_correction := 0.0
var _last_raw_audio_position := -1.0
var _popup_text := ""
var _popup_colour := Color.WHITE
var _popup_timer := 0.0
var _autoplay_index := 0

@onready var lane: Lane = $Lane
@onready var drum: DrumVisual = $Drum
@onready var music: AudioStreamPlayer = $Music
@onready var sfx_don: AudioStreamPlayer = $SfxDon
@onready var sfx_ka: AudioStreamPlayer = $SfxKa
@onready var sfx_pop: AudioStreamPlayer = $SfxPop
@onready var score_label: Label = $Hud/Score
@onready var combo_label: Label = $Hud/Combo
@onready var title_label: Label = $Hud/Title
@onready var popup_label: Label = $Hud/Popup
@onready var gauge_bar: ProgressBar = $Hud/Gauge
@onready var debug_label: Label = $Hud/Debug


func _ready() -> void:
	chart = GameSession.chart
	course = GameSession.course()
	if chart == null or course == null:
		push_error("No song selected")
		UiKit.go_to("res://scenes/song_select.tscn")
		return
	Chart.reset_course(course)
	_load_settings()
	UiKit.background(self)
	lane.course = course
	lane.note_speed = Settings.get_value("gameplay.note_speed", 1.0)
	lane.position.y = GameSkin.size("lane_y")
	title_label.text = chart.title + "  [" + course.name + "]"
	music.stream = SongLibrary.load_audio(chart)
	music.volume_db = linear_to_db(clamp(float(Settings.get_value("audio.music_volume", 0.8)) * chart.song_volume / 100.0, 0.0001, 1.0))
	for player in [sfx_don, sfx_ka, sfx_pop]:
		player.volume_db = linear_to_db(clamp(float(Settings.get_value("audio.sfx_volume", 1.0)), 0.0001, 1.0))
	sfx_don.stream = GameSkin.sounds.get("don")
	sfx_ka.stream = GameSkin.sounds.get("ka")
	sfx_pop.stream = GameSkin.sounds.get("balloon_pop")
	song_time = -float(Settings.get_value("gameplay.countdown_seconds", 2.0))
	autoplay = Settings.get_value("gameplay.autoplay", false)
	_clock_anchor_song = song_time
	InputRouter.drum_hit.connect(_on_drum_hit)
	_update_hud()


func _load_settings() -> void:
	good_window = float(Settings.get_value("judgement.good_ms")) / 1000.0
	ok_window = float(Settings.get_value("judgement.ok_ms")) / 1000.0
	bad_window = float(Settings.get_value("judgement.bad_ms")) / 1000.0
	pair_window = float(Settings.get_value("judgement.big_note_pair_ms")) / 1000.0
	roll_gap = float(Settings.get_value("judgement.roll_min_gap_ms")) / 1000.0
	audio_offset = float(Settings.get_value("audio.offset_ms")) / 1000.0
	sync_to_audio = bool(Settings.get_value("audio.sync_to_audio_clock", true))


# ---------------------------------------------------------------- timing
func _process(delta: float) -> void:
	if finished:
		return
	_advance_clock(delta)
	lane.song_time = song_time
	lane.gogo = _current_gogo()
	_miss_overdue_notes()
	if autoplay:
		_run_autoplay()
	_popup_timer = max(0.0, _popup_timer - delta)
	popup_label.modulate.a = clamp(_popup_timer * 2.0, 0.0, 1.0)
	if started and song_time > course.last_time() + 2.0 and not music.playing:
		_finish()
	if Settings.get_value("gameplay.show_debug", false):
		debug_label.text = "t=%.3f  tracker=%s  fps=%d" % [song_time, "on" if TrackerClient.connected else "off", Engine.get_frames_per_second()]


func _advance_clock(delta: float) -> void:
	# The song clock runs off the OS wall clock (immune to frame-time smoothing)
	# and is slowly steered towards the audio playback position so it can
	# never drift away from what you hear.  When recording a movie
	# (--write-movie) frames are not real time, so the clock counts frames.
	if OS.has_feature("movie"):
		_clock_anchor_song += delta
	song_time = _song_time_now()
	if not started:
		if song_time >= 0.0:
			started = true
			if music.stream != null:
				music.play(song_time)
		return
	if not music.playing or not sync_to_audio:
		return
	var raw_position := music.get_playback_position()
	if is_equal_approx(raw_position, _last_raw_audio_position):
		return   # the mixer has not produced a new reading yet; do not trust it
	_last_raw_audio_position = raw_position
	var audio_time := raw_position + AudioServer.get_time_since_last_mix() - AudioServer.get_output_latency() + audio_offset
	var error := audio_time - song_time
	if abs(error) > 0.25:
		_audio_correction += error          # something big happened (stall, seek): snap
	else:
		_audio_correction += error * 0.05   # gentle steering


## Current song time, evaluated right now (not the value cached last frame).
func _song_time_now() -> float:
	if OS.has_feature("movie"):
		return _clock_anchor_song + _audio_correction
	var wall := Time.get_ticks_usec() / 1000000.0
	if _clock_anchor_wall < 0.0:
		_clock_anchor_wall = wall
		_clock_anchor_song = song_time
	return _clock_anchor_song + (wall - _clock_anchor_wall) + _audio_correction


## Song time at which a hit with the given unix timestamp happened.
func _song_time_at(unix_time: float) -> float:
	return _song_time_now() - (Time.get_unix_time_from_system() - unix_time)


func _current_gogo() -> bool:
	var gogo := false
	for note in course.notes:
		if note.time > song_time:
			break
		gogo = note.gogo
	return gogo


# ---------------------------------------------------------------- input
func _on_drum_hit(kind: String, side: String, unix_time: float, strength: float, _source: String) -> void:
	if finished:
		return
	_apply_hit(kind, side, _song_time_at(unix_time), strength)


func _apply_hit(kind: String, side: String, hit_time: float, _strength: float) -> void:
	(sfx_don if kind == "don" else sfx_ka).play()
	drum.hit(kind, side)
	lane.flash(side)
	if _next_hittable >= course.notes.size():
		return
	# Long notes (rolls / balloons) swallow every hit while they are active.
	for index in range(_next_hittable, course.notes.size()):
		var note: Chart.Note = course.notes[index]
		if note.time > hit_time + bad_window:
			break
		if note.is_long() and not note.judged and hit_time >= note.time and hit_time <= note.end_time:
			_hit_long_note(note, kind, hit_time)
			return
	# Otherwise the nearest un-judged hittable note in range decides.
	var target: Chart.Note = null
	for index in range(_next_hittable, course.notes.size()):
		var note: Chart.Note = course.notes[index]
		if note.judged or not note.is_hittable():
			continue
		if note.time > hit_time + bad_window:
			break
		if abs(note.time - hit_time) <= bad_window:
			target = note
			break
	if target == null:
		return
	_hit_deltas.append(hit_time - target.time)
	if Settings.get_value("gameplay.show_debug", false):
		print("[timing] note %.3f hit %.3f delta %+.0f ms kind %s/%s" % [target.time, hit_time, (hit_time - target.time) * 1000.0, target.kind(), kind])
	if target.kind() != kind:
		_judge(target, "bad")
		return
	if target.is_big():
		if target.first_hit_side.is_empty():
			target.first_hit_side = side
			target.first_hit_time = hit_time
			# Wait for the other hand a little before judging as a single hit.
			get_tree().create_timer(pair_window).timeout.connect(func(): _finish_big_note(target, false))
			return
		if target.first_hit_side != side and hit_time - target.first_hit_time <= pair_window and not target.judged:
			_finish_big_note(target, true)
		return
	_judge(target, _window_for(abs(target.time - hit_time)))


func _finish_big_note(note: Chart.Note, both_hands: bool) -> void:
	if note.judged:
		return
	var judgement := _window_for(abs(note.time - note.first_hit_time))
	_judge(note, judgement, both_hands)


func _window_for(delta: float) -> String:
	if delta <= good_window:
		return "good"
	if delta <= ok_window:
		return "ok"
	return "bad"


func _hit_long_note(note: Chart.Note, kind: String, hit_time: float) -> void:
	if hit_time - _last_roll_hit_time < roll_gap:
		return
	_last_roll_hit_time = hit_time
	if note.kind() == "balloon" and kind != "don":
		return
	note.roll_hits += 1
	roll_hits += 1
	score += int(Settings.get_value("scoring.roll_hit", 100))
	if note.kind() == "balloon" and note.roll_hits >= note.balloon_count and not note.popped:
		note.popped = true
		note.judged = true
		score += int(Settings.get_value("scoring.balloon_pop", 5000))
		sfx_pop.play()
		_show_popup("POP!", GameSkin.color("good"))
	_update_hud()


func _judge(note: Chart.Note, judgement: String, big_bonus: bool = false) -> void:
	note.judged = true
	note.judgement = judgement
	counts[judgement] += 1
	var hittable_total := course.hittable_count()
	if judgement == "bad":
		combo = 0
		gauge = max(0.0, gauge - 1.5 / hittable_total)
		_show_popup("MISS", GameSkin.color("bad"))
	else:
		combo += 1
		max_combo = max(max_combo, combo)
		var points := int(Settings.get_value("scoring." + judgement, 0))
		if note.is_big() and big_bonus:
			points *= int(Settings.get_value("scoring.big_multiplier", 2))
		score += points
		gauge = min(1.0, gauge + (1.0 if judgement == "good" else 0.5) * 1.2 / hittable_total)
		_show_popup("GOOD" if judgement == "good" else "OK", GameSkin.color(judgement))
	_advance_next_hittable()
	_update_hud()


func _advance_next_hittable() -> void:
	while _next_hittable < course.notes.size():
		var note: Chart.Note = course.notes[_next_hittable]
		var done := note.judged or (note.is_long() and note.end_time < song_time - bad_window)
		if not done:
			break
		_next_hittable += 1


func _miss_overdue_notes() -> void:
	for index in range(_next_hittable, course.notes.size()):
		var note: Chart.Note = course.notes[index]
		if note.time > song_time - bad_window:
			break
		if note.is_hittable() and not note.judged:
			_judge(note, "bad")
	_advance_next_hittable()


func _run_autoplay() -> void:
	while _autoplay_index < course.notes.size():
		var note: Chart.Note = course.notes[_autoplay_index]
		if note.time > song_time:
			break
		if note.is_hittable():
			_apply_hit(note.kind(), "right" if _autoplay_index % 2 == 0 else "left", note.time, 1.0)
			if note.is_big():
				_apply_hit(note.kind(), "left" if _autoplay_index % 2 == 0 else "right", note.time, 1.0)
		_autoplay_index += 1
	# Drum rolls: keep tapping while one is active.
	for index in range(_next_hittable, course.notes.size()):
		var note: Chart.Note = course.notes[index]
		if note.time > song_time:
			break
		if note.is_long() and not note.judged and song_time <= note.end_time and song_time - _last_roll_hit_time > 0.1:
			_apply_hit("don", "right" if roll_hits % 2 == 0 else "left", song_time, 1.0)


# ---------------------------------------------------------------- hud
func _show_popup(text: String, colour: Color) -> void:
	_popup_text = text
	_popup_colour = colour
	_popup_timer = 0.5
	popup_label.text = text
	popup_label.add_theme_color_override("font_color", colour)


func _update_hud() -> void:
	score_label.text = "%07d" % score
	combo_label.text = str(combo) if combo > 0 else ""
	gauge_bar.value = gauge * 100.0
	var clear_ratio := float(Settings.get_value("scoring.gauge_clear_ratio", 0.8))
	gauge_bar.modulate = GameSkin.color("gauge_clear") if gauge >= clear_ratio else GameSkin.color("gauge")


func _finish() -> void:
	finished = true
	var hittable := course.hittable_count()
	var accuracy := 0.0
	if hittable > 0:
		accuracy = (counts["good"] + counts["ok"] * 0.5) / hittable * 100.0
	var results_script := load("res://scripts/gameplay/results.gd")
	results_script.last = {
		"title": chart.title, "course": course.name, "score": score,
		"good": counts["good"], "ok": counts["ok"], "bad": counts["bad"],
		"max_combo": max_combo, "roll_hits": roll_hits, "accuracy": "%.1f%%" % accuracy,
		"cleared": gauge >= float(Settings.get_value("scoring.gauge_clear_ratio", 0.8)),
		"timing": _timing_summary(),
	}


## "avg +12 ms (spread 18 ms)": positive means you hit late.  Use it to set the audio offset.
func _timing_summary() -> String:
	if _hit_deltas.is_empty():
		return "-"
	var mean := 0.0
	for d in _hit_deltas:
		mean += d
	mean /= _hit_deltas.size()
	var variance := 0.0
	for d in _hit_deltas:
		variance += (d - mean) * (d - mean)
	var spread := sqrt(variance / _hit_deltas.size())
	return "avg %+.0f ms (spread %.0f ms)" % [mean * 1000.0, spread * 1000.0]
	UiKit.go_to(RESULTS_SCENE)


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("ui_cancel"):
		finished = true
		UiKit.go_to("res://scenes/song_select.tscn")
