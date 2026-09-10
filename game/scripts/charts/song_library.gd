class_name SongLibrary
extends RefCounted
## Finds every .tja file under data/songs (any depth) and loads the audio.


class Entry:
	extends RefCounted
	var chart: Chart
	var path: String

	func display_title() -> String:
		return chart.title if not chart.title.is_empty() else path.get_file().get_basename()


static func scan(root: String = "") -> Array[Entry]:
	var entries: Array[Entry] = []
	var folder := root if not root.is_empty() else Paths.songs_dir()
	_scan_folder(folder, entries)
	entries.sort_custom(func(a, b): return a.display_title().naturalnocasecmp_to(b.display_title()) < 0)
	return entries


static func _scan_folder(folder: String, entries: Array[Entry]) -> void:
	var dir := DirAccess.open(folder)
	if dir == null:
		return
	dir.list_dir_begin()
	var name := dir.get_next()
	while not name.is_empty():
		var path := folder.path_join(name)
		if dir.current_is_dir():
			if not name.begins_with("."):
				_scan_folder(path, entries)
		elif name.get_extension().to_lower() == "tja":
			var chart := TjaParser.load_file(path)
			if chart != null:
				var entry := Entry.new()
				entry.chart = chart
				entry.path = path
				entries.append(entry)
		name = dir.get_next()
	dir.list_dir_end()


## Loads the audio referenced by a chart (ogg, mp3 or wav).
static func load_audio(chart: Chart) -> AudioStream:
	var path := chart.audio_path()
	if not FileAccess.file_exists(path):
		# Fall back to any audio file with the chart's name.
		for extension in ["ogg", "mp3", "wav"]:
			var candidate: String = chart.chart_path.get_basename() + "." + extension
			if FileAccess.file_exists(candidate):
				path = candidate
				break
	match path.get_extension().to_lower():
		"ogg": return AudioStreamOggVorbis.load_from_file(path)
		"mp3": return AudioStreamMP3.load_from_file(path)
		"wav": return AudioStreamWAV.load_from_file(path)
	push_warning("No audio found for chart " + chart.chart_path)
	return null
