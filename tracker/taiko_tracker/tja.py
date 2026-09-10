"""Tiny TJA chart reader, just enough for the simulator to "play" a song.

The game has its own full parser (game/scripts/charts/tja_parser.gd); this
one only extracts note times for the first course in the file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChartNote:
    time: float      # seconds from the start of the audio
    kind: str        # "don" or "ka"
    big: bool


def parse_tja_notes(text: str) -> list[ChartNote]:
    bpm = 120.0
    offset = 0.0
    measure_num, measure_den = 4, 4
    notes: list[ChartNote] = []
    in_chart = False
    time = -offset
    measure_lines: list[str] = []
    pending_bpm: list[tuple[int, float]] = []   # (note index within measure, new bpm)

    def flush_measure():
        nonlocal time, bpm
        cells = "".join(measure_lines)
        measure_lines.clear()
        measure_seconds = 60.0 / bpm * 4.0 * measure_num / measure_den
        if not cells:
            time += measure_seconds
            pending_bpm.clear()
            return
        # Walk the cells, applying BPM changes recorded at cell indices.
        cell_time = time
        for index, cell in enumerate(cells):
            for at_index, new_bpm in pending_bpm:
                if at_index == index:
                    bpm = new_bpm
            cell_seconds = 60.0 / bpm * 4.0 * measure_num / measure_den / len(cells)
            if cell in "1234":
                notes.append(ChartNote(cell_time, "don" if cell in "13" else "ka", cell in "34"))
            cell_time += cell_seconds
        time = cell_time
        pending_bpm.clear()

    for raw in text.splitlines():
        line = raw.split("//")[0].strip()
        if not line:
            continue
        if not in_chart:
            upper = line.upper()
            if upper.startswith("BPM:"):
                bpm = float(line.split(":", 1)[1] or 120)
            elif upper.startswith("OFFSET:"):
                offset = float(line.split(":", 1)[1] or 0)
                time = -offset
            elif upper.startswith("#START"):
                in_chart = True
                time = -offset
            continue
        if line.upper() == "#END":
            break
        if line.startswith("#"):
            parts = line.split()
            command = parts[0].upper()
            if command == "#BPMCHANGE" and len(parts) > 1:
                pending_bpm.append((len("".join(measure_lines)), float(parts[1])))
            elif command == "#MEASURE" and len(parts) > 1 and "/" in parts[1]:
                num, den = parts[1].split("/")
                measure_num, measure_den = int(num), int(den)
            continue
        body = "".join(ch for ch in line if ch.isdigit())
        measure_lines.append(body)
        if line.endswith(","):
            flush_measure()
    return notes
