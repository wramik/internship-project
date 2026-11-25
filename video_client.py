
import os
import requests
import urllib.parse
from pathlib import Path


def UploadVideo(server_url, video_path, company_name):
    if not os.path.isfile(video_path):
        print(f"Video file not found: {video_path}")
        return None

    url = f"{server_url.rstrip('/')}/upload"

    # Ищем sidecar рядом с видео:
    # Tech Corp.webm -> Tech Corp.zoom.json
    zoom_json_path = Path(video_path).with_suffix(".zoom.json")

    try:
        with open(video_path, "rb") as video_file:
            files = {
                "video": (
                    os.path.basename(video_path),
                    video_file,
                    "video/webm"
                )
            }

            if zoom_json_path.is_file():
                zoom_file = open(zoom_json_path, "rb")
                files["zoom_json"] = (
                    zoom_json_path.name,
                    zoom_file,
                    "application/json"
                )
            else:
                zoom_file = None

            try:
                response = requests.post(
                    url,
                    files=files,
                    data={"company_name": company_name},
                    timeout=120
                )
            finally:
                if zoom_file is not None:
                    zoom_file.close()

        response.raise_for_status()
        result = response.json()

        video_url = result.get("video_url")
        processed_video_url = result.get("processed_video_url")

        if zoom_json_path.is_file():
            print(f"Zoom events uploaded: {zoom_json_path}")
        else:
            print("No zoom JSON found; video will be processed without zoom.")

        if not processed_video_url:
            print("Server did not return processed video URL")
            return None

        print(f"Video uploaded: {video_url}")
        print(f"Processed video: {processed_video_url}")

        return processed_video_url

    except requests.RequestException as error:
        print(f"Video upload failed: {error}")
        return None


def GetVideoURL(server_url, company_name):
    url = f"{server_url.rstrip('/')}/get_video_url"

    try:
        response = requests.get(
            url,
            params={"company_name": company_name},
            timeout=30
        )

        response.raise_for_status()
        result = response.json()

        processed_video_url = result.get("processed_video_url")

        if not processed_video_url:
            print("Server did not return processed video URL")
            return None

        print(f"Processed video URL: {processed_video_url}")
        return processed_video_url

    except requests.RequestException as error:
        print(f"Failed to get video URL: {error}")
        return None
