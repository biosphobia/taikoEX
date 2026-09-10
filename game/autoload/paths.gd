extends Node
## Where user-editable content lives.
##
## Everything a player may want to change (songs, skins, sounds, settings,
## the tracker) sits in a plain "data" folder that is NOT packed into the
## executable:
##   * running from the editor / source tree: game/data
##   * installed build: the "data" folder next to TaikoEX.exe
## The folder has a .gdignore file so Godot never imports it; files are
## loaded at runtime with load_from_file().

func data_dir() -> String:
	if not OS.has_feature("editor"):
		var beside_exe := OS.get_executable_path().get_base_dir().path_join("data")
		if DirAccess.dir_exists_absolute(beside_exe):
			return beside_exe
	return ProjectSettings.globalize_path("res://data")


func install_dir() -> String:
	if OS.has_feature("editor"):
		return ProjectSettings.globalize_path("res://").rstrip("/")
	return OS.get_executable_path().get_base_dir()


func songs_dir() -> String:
	return data_dir().path_join("songs")


func skins_dir() -> String:
	return data_dir().path_join("skins")


func sounds_dir() -> String:
	return data_dir().path_join("sounds")


func settings_file() -> String:
	return data_dir().path_join("settings.json")


## The tracker executable (build) or the source script (development).
func tracker_command() -> PackedStringArray:
	var built := install_dir().path_join("tracker").path_join("taiko_tracker.exe")
	if FileAccess.file_exists(built):
		return PackedStringArray([built])
	var source := install_dir().path_join("..").path_join("tracker").path_join("run_tracker.py")
	source = source.simplify_path()
	if FileAccess.file_exists(source):
		var python := "python" if OS.get_name() == "Windows" else "python3"
		return PackedStringArray([python, source])
	return PackedStringArray()


func version() -> String:
	var file := FileAccess.open("res://version.txt", FileAccess.READ)
	if file == null:
		return "dev"
	return file.get_as_text().strip_edges()


func read_json(path: String, fallback: Variant = null) -> Variant:
	if not FileAccess.file_exists(path):
		return fallback
	var file := FileAccess.open(path, FileAccess.READ)
	var parsed = JSON.parse_string(file.get_as_text())
	return parsed if parsed != null else fallback


func write_json(path: String, data: Variant) -> void:
	DirAccess.make_dir_recursive_absolute(path.get_base_dir())
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file:
		file.store_string(JSON.stringify(data, "  "))
		file.store_string("\n")
