import argparse
import json
import math
import os
import shutil
import subprocess
import sys


ZOOM_FACTOR = 2.0
ZOOM_DURATION = 0.5
DEFAULT_FPS = 30.0


def load_zoom_events(json_path, return_metadata=False):

    with open(json_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        events = data.get("events", [])
        metadata = data
    elif isinstance(data, list):
        events = data
        metadata = {}
    else:
        raise ValueError("Zoom JSON must contain an object or a list of events.")

    if not isinstance(events, list):
        raise ValueError("Zoom JSON field 'events' must contain a list.")

    validated_events = []
    zoom_active = False

    for event in events:
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if event_type is None:
            event_type = "out" if zoom_active else "in"

        if event_type not in ("in", "out"):
            continue

        try:
            timestamp = float(event.get("t", event.get("timestamp")))
            x = float(event["x"])
            y = float(event["y"])
        except (KeyError, TypeError, ValueError):
            continue

        if not all(math.isfinite(value) for value in (timestamp, x, y)):
            continue

        if timestamp < 0:
            continue

        validated_events.append({
            "type": event_type,
            "timestamp": timestamp,
            "x": x,
            "y": y
        })
        zoom_active = event_type == "in"

    validated_events = sorted(
        validated_events,
        key=lambda event: event["timestamp"]
    )

    if return_metadata:
        return validated_events, metadata

    return validated_events


def build_zoom_intervals(events):

    intervals = []
    current_zoom = None

    for event in events:
        if event["type"] == "in":
            if current_zoom is None:
                current_zoom = event

        elif event["type"] == "out":
            if current_zoom is not None:
                intervals.append({
                    "start": current_zoom["timestamp"],
                    "end": event["timestamp"],
                    "x": current_zoom["x"],
                    "y": current_zoom["y"]
                })

                current_zoom = None

    if current_zoom is not None:
        intervals.append({
            "start": current_zoom["timestamp"],
            "end": None,
            "x": current_zoom["x"],
            "y": current_zoom["y"]
        })

    return intervals


def probe_video(video_path, ffprobe_path):

    command = [
        ffprobe_path,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate",
        "-of", "json",
        video_path
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True
    )

    data = json.loads(result.stdout)

    streams = data.get("streams", [])

    if not streams:
        raise RuntimeError("No video stream found.")

    stream = streams[0]

    width = int(stream["width"])
    height = int(stream["height"])

    frame_rate = stream.get("avg_frame_rate", "0/0")

    try:
        numerator, denominator = frame_rate.split("/")
        fps = float(numerator) / float(denominator)

        if not math.isfinite(fps) or fps <= 0:
            fps = DEFAULT_FPS

    except (ValueError, ZeroDivisionError):
        fps = DEFAULT_FPS

    return width, height, fps


def build_zoom_filter(
        intervals,
        width,
        height,
        fps,
        zoom_factor=ZOOM_FACTOR,
        ramp_sec=ZOOM_DURATION
):

    ramp_frames = max(1, round(ramp_sec * fps))

    zoom_expression = "1"
    x_expression = "iw/2-iw/zoom/2"
    y_expression = "ih/2-ih/zoom/2"

    for interval in reversed(intervals):
        start_frame = round(interval["start"] * fps)

        if interval["end"] is None:
            end_frame = None
            finish_frame = 10**12
            out_start_frame = None

        else:
            end_frame = round(interval["end"] * fps)

            if end_frame <= start_frame:
                continue

            actual_ramp = min(
                ramp_frames,
                max(1, (end_frame - start_frame) // 2)
            )

            out_start_frame = end_frame
            finish_frame = end_frame + actual_ramp

        if end_frame is None:
            progress = f"min(1,(on-{start_frame})/{ramp_frames})"
            eased_progress = f"(0.5-0.5*cos(PI*{progress}))"
            zoom_value = (
                f"1+({zoom_factor}-1)*{eased_progress}"
            )

        else:
            zoom_in_end = start_frame + actual_ramp

            zoom_in_progress = (
                f"min(1,(on-{start_frame})/{actual_ramp})"
            )
            zoom_out_progress = (
                f"min(1,(on-{out_start_frame})/{actual_ramp})"
            )
            zoom_in_eased = (
                f"(0.5-0.5*cos(PI*{zoom_in_progress}))"
            )
            zoom_out_eased = (
                f"(0.5-0.5*cos(PI*{zoom_out_progress}))"
            )

            zoom_value = (
                f"if(lte(on,{zoom_in_end}),"
                f"1+({zoom_factor}-1)*{zoom_in_eased},"
                f"if(lt(on,{out_start_frame}),"
                f"{zoom_factor},"
                f"{zoom_factor}-({zoom_factor}-1)*{zoom_out_eased}))"
            )

        zoom_expression = (
            f"if(between(on,{start_frame},{finish_frame}),"
            f"{zoom_value},{zoom_expression})"
        )

        center_x = interval["x"]
        center_y = interval["y"]

        x_value = (
            f"max(0,min(iw-iw/zoom,"
            f"{center_x}-iw/(2*zoom)))"
        )

        y_value = (
            f"max(0,min(ih-ih/zoom,"
            f"{center_y}-ih/(2*zoom)))"
        )

        x_expression = (
            f"if(between(on,{start_frame},{finish_frame}),"
            f"{x_value},{x_expression})"
        )

        y_expression = (
            f"if(between(on,{start_frame},{finish_frame}),"
            f"{y_value},{y_expression})"
        )

    return (
        f"zoompan="
        f"z='{zoom_expression}':"
        f"x='{x_expression}':"
        f"y='{y_expression}':"
        f"d=1:"
        f"s={width}x{height}:"
        f"fps={fps}"
    )


def build_ffmpeg_command(
        video_path,
        output_path,
        ffmpeg_path,
        video_filter
):

    return [
        ffmpeg_path,
        "-y",
        "-i", video_path,
        "-vf", video_filter,
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libvpx-vp9",
        "-crf", "30",
        "-b:v", "0",
        "-c:a", "copy",
        output_path
    ]


def apply_zoom(
        video_path,
        json_path=None,
        output_path=None,
        dry_run=False,
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe"
):
    if not os.path.isfile(video_path):
        raise FileNotFoundError(
            f"Video not found: {video_path}"
        )

    if output_path is None:
        name, extension = os.path.splitext(video_path)
        output_path = f"{name}_zoomed{extension or '.webm'}"

    # When --json is omitted, look for the sidecar next to the video:
    # recording.webm -> recording.zoom.json.
    # A missing sidecar means that the recording contains no zoom events.
    if json_path is None:
        video_name, _ = os.path.splitext(video_path)
        default_json_path = f"{video_name}.zoom.json"
        json_path = default_json_path if os.path.isfile(default_json_path) else None

    if json_path is not None:
        if not os.path.isfile(json_path):
            raise FileNotFoundError(
                f"Zoom JSON not found: {json_path}"
            )
        events, metadata = load_zoom_events(
            json_path,
            return_metadata=True
        )
    else:
        events, metadata = [], {}

    intervals = build_zoom_intervals(events)

    if not intervals:
        print("No complete zoom intervals found.")

        if dry_run:
            print("No video processing is required.")
            return output_path

        shutil.copy2(video_path, output_path)
        print(f"Video copied to: {output_path}")

        return output_path

    width, height, fps = probe_video(
        video_path,
        ffprobe_path
    )

    # Координаты из qscript заданы в системе viewport.
    # Переводим их в пиксели исходного видео.
    viewport = metadata.get("viewport", {})
    try:
        viewport_width = float(viewport.get("width", width))
        viewport_height = float(viewport.get("height", height))
    except (TypeError, ValueError):
        viewport_width = width
        viewport_height = height

    if viewport_width <= 0 or viewport_height <= 0:
        viewport_width = width
        viewport_height = height

    scale_x = width / viewport_width
    scale_y = height / viewport_height

    for interval in intervals:
        interval["x"] *= scale_x
        interval["y"] *= scale_y

    try:
        zoom_factor = float(metadata.get("zoom_factor", ZOOM_FACTOR))
        ramp_sec = float(metadata.get("ramp_sec", ZOOM_DURATION))
    except (TypeError, ValueError):
        zoom_factor = ZOOM_FACTOR
        ramp_sec = ZOOM_DURATION

    if not math.isfinite(zoom_factor) or zoom_factor < 1:
        zoom_factor = ZOOM_FACTOR

    if not math.isfinite(ramp_sec) or ramp_sec <= 0:
        ramp_sec = ZOOM_DURATION

    video_filter = build_zoom_filter(
        intervals,
        width,
        height,
        fps,
        zoom_factor,
        ramp_sec
    )

    command = build_ffmpeg_command(
        video_path,
        output_path,
        ffmpeg_path,
        video_filter
    )

    print("Zoom intervals:")

    for interval in intervals:
        print(interval)

    print("\nFFmpeg command:")
    print(subprocess.list2cmdline(command))

    if dry_run:
        print("\nDry run enabled. Video was not processed.")
        return output_path

    subprocess.run(
        command,
        check=True
    )

    print(f"\nProcessed video saved to: {output_path}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Apply zoom effects to a recorded video."
    )

    parser.add_argument(
        "video_pos",
        nargs="?",
        help="Path to input WebM video (positional form)."
    )
    parser.add_argument(
        "json_pos",
        nargs="?",
        help="Path to zoom events JSON file (positional form)."
    )
    parser.add_argument(
        "--video",
        dest="video_option",
        help="Path to input WebM video."
    )
    parser.add_argument(
        "--json",
        dest="json_option",
        help=(
            "Path to zoom events JSON file. If omitted, the script looks "
            "for <video-name>.zoom.json next to the video."
        )
    )

    parser.add_argument(
        "-output",
        "--output",
        help="Path to output video."
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print FFmpeg command without processing video."
    )

    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="Path to FFmpeg executable."
    )

    parser.add_argument(
        "--ffprobe",
        default="ffprobe",
        help="Path to FFprobe executable."
    )

    args = parser.parse_args()

    if args.video_option and args.video_pos:
        parser.error("provide the video either as --video or positionally, not both")
    video_path = args.video_option or args.video_pos
    if not video_path:
        parser.error("the input video is required (use --video PATH)")

    if args.json_option and args.json_pos:
        parser.error("provide the JSON either as --json or positionally, not both")
    json_path = args.json_option or args.json_pos

    try:
        apply_zoom(
            video_path=video_path,
            json_path=json_path,
            output_path=args.output,
            dry_run=args.dry_run,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe
        )

    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
