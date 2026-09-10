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


func _ready() -> void:
	_open_sockets()
	Settings.changed.connect(_on_setting_changed)


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
	tracker_pid = OS.create_process(command[0], args, false)
	if tracker_pid > 0:
		print("Started tracker (pid %d): %s" % [tracker_pid, " ".join(command)])
	else:
		push_warning("Could not start the tracker: " + " ".join(command))


func _exit_tree() -> void:
	if tracker_pid > 0:
		send_command({"cmd": "quit"})
