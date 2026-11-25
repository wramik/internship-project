import json
import pytest

from apply_zoom import (
    load_zoom_events,
    build_zoom_intervals,
    build_zoom_filter,
    apply_zoom,
)


def test_sidecar_parsing(tmp_path):
    path = tmp_path / "events.json"
    path.write_text(json.dumps({
        "events": [
            {"t": 1, "x": 100, "y": 200, "type": "in"},
            {"t": 3, "x": 100, "y": 200, "type": "out"},
        ]
    }), encoding="utf-8")

    events = load_zoom_events(path)

    assert len(events) == 2
    assert events[0]["type"] == "in"
    assert events[1]["type"] == "out"


def test_old_sidecar_without_type(tmp_path):
    path = tmp_path / "events.json"
    path.write_text(json.dumps([
        {"t": 1, "x": 100, "y": 200},
        {"t": 3, "x": 100, "y": 200},
    ]), encoding="utf-8")

    events = load_zoom_events(path)

    assert [event["type"] for event in events] == ["in", "out"]


def test_multiple_in_out_pairs():
    events = [
        {"type": "in", "timestamp": 1, "x": 100, "y": 200},
        {"type": "out", "timestamp": 2, "x": 100, "y": 200},
        {"type": "in", "timestamp": 4, "x": 300, "y": 400},
        {"type": "out", "timestamp": 5, "x": 300, "y": 400},
    ]

    intervals = build_zoom_intervals(events)

    assert len(intervals) == 2
    assert intervals[0] == {
        "start": 1, "end": 2, "x": 100, "y": 200
    }
    assert intervals[1] == {
        "start": 4, "end": 5, "x": 300, "y": 400
    }


def test_open_ended_zoom():
    events = [
        {"type": "in", "timestamp": 2, "x": 150, "y": 250}
    ]

    intervals = build_zoom_intervals(events)

    assert len(intervals) == 1
    assert intervals[0]["start"] == 2
    assert intervals[0]["end"] is None


def test_ramp_frame_calculation():
    result = build_zoom_filter(
        [{"start": 1, "end": 4, "x": 500, "y": 300}],
        1280, 720, 30,
        zoom_factor=2,
        ramp_sec=0.5,
    )

    # 0.5 seconds * 30 FPS = 15 frames
    assert "(on-30)/15" in result
    assert "(on-120)/15" in result
    assert "between(on,30,135)" in result


def test_coordinate_clamping():
    result = build_zoom_filter(
        [{"start": 1, "end": 4, "x": 500, "y": 300}],
        1280, 720, 30,
    )

    assert "max(0,min(iw-iw/zoom" in result
    assert "max(0,min(ih-ih/zoom" in result


def test_viewport_coordinates_are_scaled(tmp_path, monkeypatch):
    video = tmp_path / "input.webm"
    sidecar = tmp_path / "input.zoom.json"
    video.write_bytes(b"video")

    sidecar.write_text(json.dumps({
        "viewport": {"width": 640, "height": 360},
        "events": [
            {"t": 1, "x": 100, "y": 50, "type": "in"},
            {"t": 3, "x": 100, "y": 50, "type": "out"},
        ],
    }), encoding="utf-8")

    monkeypatch.setattr(
        "apply_zoom.probe_video",
        lambda *args: (1280, 720, 30),
    )

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command

    monkeypatch.setattr("apply_zoom.subprocess.run", fake_run)

    apply_zoom(
        str(video),
        str(sidecar),
        str(tmp_path / "output.webm"),
    )

    video_filter = captured["command"][captured["command"].index("-vf") + 1]

    assert "200.0-iw/(2*zoom)" in video_filter
    assert "100.0-ih/(2*zoom)" in video_filter


def test_no_events_copies_video_unchanged(tmp_path):
    video = tmp_path / "input.webm"
    sidecar = tmp_path / "events.json"
    output = tmp_path / "output.webm"

    original = b"test video content"
    video.write_bytes(original)
    sidecar.write_text('{"events": []}', encoding="utf-8")

    result = apply_zoom(str(video), str(sidecar), str(output))

    assert result == str(output)
    assert output.read_bytes() == original


def test_apply_zoom_finds_sidecar_automatically(tmp_path, monkeypatch):
    video = tmp_path / "input.webm"
    sidecar = tmp_path / "input.zoom.json"
    video.write_bytes(b"video")
    sidecar.write_text(json.dumps({
        "events": [
            {"t": 1, "x": 100, "y": 50, "type": "in"},
            {"t": 3, "x": 100, "y": 50, "type": "out"},
        ]
    }), encoding="utf-8")

    monkeypatch.setattr(
        "apply_zoom.probe_video",
        lambda *args: (1280, 720, 30),
    )
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command

    monkeypatch.setattr("apply_zoom.subprocess.run", fake_run)

    apply_zoom(str(video))

    assert "-vf" in captured["command"]
    video_filter = captured["command"][captured["command"].index("-vf") + 1]
    assert video_filter.startswith("zoompan=")


def test_cli_named_video_without_sidecar_copies_unchanged(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "apply_zoom.py"
    video = tmp_path / "input.webm"
    output = tmp_path / "custom_output.webm"
    original = b"original video bytes"
    video.write_bytes(original)

    result = subprocess.run(
        [
            sys.executable, str(script),
            "--video", str(video),
            "--output", str(output),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == original
    assert "Video copied to:" in result.stdout


def test_cli_named_video_and_json_options(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "apply_zoom.py"
    video = tmp_path / "input.webm"
    sidecar = tmp_path / "events.zoom.json"
    video.write_bytes(b"video")
    sidecar.write_text('{"events": []}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, str(script),
            "--video", str(video),
            "--json", str(sidecar),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "No video processing is required." in result.stdout


def test_cli_keeps_legacy_positional_arguments_and_short_output_alias(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "apply_zoom.py"
    video = tmp_path / "input.webm"
    sidecar = tmp_path / "events.zoom.json"
    output = tmp_path / "output.webm"
    video.write_bytes(b"video")
    sidecar.write_text('{"events": []}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, str(script),
            str(video), str(sidecar),
            "--o", str(output),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "No video processing is required." in result.stdout
