extends Node
## UDP link to the Python tracker (see tracker/ and docs/PROTOCOL.md).
##
## * state packets arrive every camera frame -> `state_updated`
## * hits inside those packets -> `hit_received`
## * commands are sent with `send_command`; replies call back
## * JPEG preview frames -> `preview_frame`

signal state_updated(state: Dictionary)
signal hit_received(hit: Dictionary)
signal preview_frame(image: Image)
signal connection_changed(connected: bool)

const PREVIEW_MAGIC := "TKPV"
const PREVIEW_HEADER_SIZE := 8

var state: Dictionary = {}
var connected := false
var last_state_time := 0.0
var tracker_pid := -1

var _state_socket := PacketPeerUDP.new()
var _command_socket := PacketPeerUDP.new()
var _preview_socket := PacketPeerUDP.new()
var _pending_replies: Dictionary = {}     # command name -> Array[Callable]
var _preview_parts: Dictionary = {}       # frame id -> {index: bytes}
var _launch_attempted := false
var _launches := 0
var _relaunch_at := 0.0
var _quitting := false

const MAX_LAUNCHES := 3
const RELAUNCH_DELAY_S := 4.0


func _ready() -> void:
	_open_sockets()
	Settings.changed.connect(_on_setting_changed)
	# Closing the window must take the tracker down with it, or the next
	# start finds the camera and the ports still held by a process nobody
	# can see.  So the quit is handled here rather than accepted outright.
	get_tree().auto_accept_quit = false


func _notification(what: int) -> void:
	if what == NOTIFICATION_WM_CLOSE_REQUEST and not _quitting:
		_quitting = true
		_shut_down_and_quit()


func _shut_down_and_quit() -> void:
	if tracker_pid > 0 and OS.is_process_running(tracker_pid):
		send_command({"cmd": "quit"})
		var waited := 0.0
		while OS.is_process_running(tracker_pid) and waited < 1.5:
			await get_tree().create_timer(0.1).timeout
			waited += 0.1
		if OS.is_process_running(tracker_pid):
			OS.kill(tracker_pid)
	get_tree().quit()


func _open_sockets() -> void:
	_state_socket.close()
	_command_socket.close()
	_preview_socket.close()
	_state_socket.bind(int(Settings.get_value("tracker.state_port")), "0.0.0.0")
	_preview_socket.bind(int(Settings.get_value("tracker.preview_port")), "0.0.0.0")
	_command_socket.bind(0, "0.0.0.0")
	_command_socket.set_dest_address(Settings.get_value("tracker.host"), int(Settings.get_value("tracker.command_port")))


func _on_setting_changed(path: String, _value: Variant) -> void:
	if path.begins_with("tracker."):
		_open_sockets()


func _process(_delta: float) -> void:
	_poll_state()
	_poll_replies()
	_poll_preview()
	var now := Time.get_unix_time_from_system()
	var alive := now - last_state_time < 1.0
	if alive != connected:
		connected = alive
		connection_changed.emit(connected)
	# Give an already running tracker a moment to show up before starting our own.
	if not connected and not _launch_attempted and Time.get_ticks_msec() > 1500:
		_launch_attempted = true
		_maybe_launch_tracker()
	# A tracker we started that has exited without ever connecting (or after
	# losing the connection) is started again a few times, with its log
	# available for anyone wondering why.
	if tracker_pid > 0 and not connected and not OS.is_process_running(tracker_pid):
		if _relaunch_at == 0.0:
			_relaunch_at = now + RELAUNCH_DELAY_S
			push_warning("The tracker exited: " + log_tail(3))
		elif now >= _relaunch_at:
			_relaunch_at = 0.0
			tracker_pid = -1
			if _launches < MAX_LAUNCHES:
				_maybe_launch_tracker()


func _poll_state() -> void:
	while _state_socket.get_available_packet_count() > 0:
		var packet := _state_socket.get_packet()
		var parsed = JSON.parse_string(packet.get_string_from_utf8())
		if not (parsed is Dictionary):
			continue
		state = parsed
		last_state_time = Time.get_unix_time_from_system()
		state_updated.emit(state)
		for hit in state.get("hits", []):
			hit_received.emit(hit)


func _poll_replies() -> void:
	while _command_socket.get_available_packet_count() > 0:
		var packet := _command_socket.get_packet()
		var parsed = JSON.parse_string(packet.get_string_from_utf8())
		if not (parsed is Dictionary):
			continue
		var name: String = parsed.get("reply", "")
		if _pending_replies.has(name) and not _pending_replies[name].is_empty():
			var callback: Callable = _pending_replies[name].pop_front()
			if callback.is_valid():
				callback.call(parsed)


func _poll_preview() -> void:
	var latest: PackedByteArray = PackedByteArray()
	while _preview_socket.get_available_packet_count() > 0:
		var packet := _preview_socket.get_packet()
		if packet.size() < PREVIEW_HEADER_SIZE or packet.slice(0, 4).get_string_from_ascii() != PREVIEW_MAGIC:
			continue
		var frame_id := (packet[4] << 8) | packet[5]
		var index := packet[6]
		var count := packet[7]
		if not _preview_parts.has(frame_id):
			_preview_parts[frame_id] = {}
		_preview_parts[frame_id][index] = packet.slice(PREVIEW_HEADER_SIZE)
		if _preview_parts[frame_id].size() == count:
			latest = PackedByteArray()
			for i in range(count):
				latest.append_array(_preview_parts[frame_id][i])
			_preview_parts.erase(frame_id)
	if _preview_parts.size() > 8:
		_preview_parts.clear()
	if latest.size() > 0:
		var image := Image.new()
		if image.load_jpg_from_buffer(latest) == OK:
			preview_frame.emit(image)


## Send a command dictionary; `callback` receives the reply dictionary.
func send_command(command: Dictionary, callback: Callable = Callable()) -> void:
	var name: String = command.get("cmd", "")
	if callback.is_valid():
		if not _pending_replies.has(name):
			_pending_replies[name] = []
		_pending_replies[name].append(callback)
	_command_socket.put_packet(JSON.stringify(command).to_utf8_buffer())


## Shorthand for changing tracker settings: patch is merged into the tracker config.
func set_config(patch: Dictionary, save: bool = true, callback: Callable = Callable()) -> void:
	send_command({"cmd": "set_config", "patch": patch, "save": save}, callback)


func set_osu_mode(enabled: bool) -> void:
	send_command({"cmd": "set_osu", "enabled": enabled, "save": true})


func controller_state(id: int) -> Dictionary:
	for controller in state.get("controllers", []):
		if int(controller.get("id", -1)) == id:
			return controller
	return {}


func _maybe_launch_tracker() -> void:
	if not Settings.get_value("tracker.auto_launch", true):
		return
	var command := Paths.tracker_command()
	if command.is_empty():
		push_warning("Tracker not found next to the game; start it by hand (see README).")
		return
	var args := command.slice(1)
	_launches += 1
	if command[0].ends_with(".exe"):
		# A tracker left over from an earlier run, possibly stuck, would keep
		# the camera and the command port.  Nobody else runs this exe.
		OS.execute("taskkill", PackedStringArray(["/F", "/IM", command[0].get_file()]), [], false)
	tracker_pid = OS.create_process(command[0], args, false)
	if tracker_pid > 0:
		print("Started tracker (pid %d): %s" % [tracker_pid, " ".join(command)])
	else:
		push_warning("Could not start the tracker: " + " ".join(command))


## The last lines of the tracker's log, for showing why it is not answering.
func log_tail(lines: int = 4) -> String:
	var path := Paths.tracker_log()
	if not FileAccess.file_exists(path):
		return "no tracker log at %s" % path
	var text := FileAccess.get_file_as_string(path).strip_edges()
	if text.is_empty():
		return "the tracker log is empty"
	var all_lines := text.split("\n")
	return "\n".join(all_lines.slice(maxi(0, all_lines.size() - lines)))


## One line saying whether the tracker is there, and if not, why not.
func status_text() -> String:
	if connected:
		var problems := []
		if not str(state.get("camera_error", "")).is_empty():
			problems.append("camera: " + str(state["camera_error"]))
		return "tracker connected" if problems.is_empty() else "tracker connected, " + ", ".join(problems)
	if tracker_pid > 0 and not OS.is_process_running(tracker_pid):
		return "tracker exited (%s) - %s" % [Paths.tracker_log().get_file(), log_tail(1)]
	if tracker_pid > 0:
		return "tracker starting..."
	if not Settings.get_value("tracker.auto_launch", true):
		return "tracker not connected (auto-launch is off; start it by hand)"
	if Paths.tracker_command().is_empty():
		return "tracker not found next to the game"
	return "tracker not connected"


func _exit_tree() -> void:
	if tracker_pid > 0:
		send_command({"cmd": "quit"})
