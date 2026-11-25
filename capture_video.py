import csv
import os
import argparse
from video_client import UploadVideo, GetVideoURL

from playwright.sync_api import sync_playwright
import qscript


CSV_FILE = "input.csv"
VIDEO_FOLDER = os.path.abspath("./video")
COLUMNS = [
    "URL",
    "Company Name",
    "Language",
    "Country",
    "Postcode",
    "City",
    "Street",
    "HouseNo",
]

def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="Flask server URL"
    )

    parser.add_argument(
        "--view-seconds",
        type=float,
        default=0,
        help="Video preview duration in seconds"
    )

    args = parser.parse_args()

    if args.view_seconds < 0:
        parser.error("--view-seconds must be >= 0")

    return args


def preview_video(video_url, view_seconds=0):

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=False)

        try:
            page = browser.new_page()

            page.goto(
                video_url,
                wait_until="domcontentloaded"
            )

            if view_seconds > 0:
                page.wait_for_timeout(
                    int(view_seconds * 1000)
                )
            else:
                input("Press Enter to close preview...")

        finally:
            browser.close()



def main():
    args = parse_args()

    os.makedirs(VIDEO_FOLDER, exist_ok=True)
    qscript.PATH_TO_VIDEO_FOLDER = VIDEO_FOLDER

    with open(CSV_FILE, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter=";", quotechar='"')

        if reader.fieldnames is None:
            raise ValueError("CSV file is empty or has no header")

        missing_columns = [column for column in COLUMNS if column not in reader.fieldnames]
        if missing_columns:
            raise ValueError(f"Missing CSV columns: {', '.join(missing_columns)}")

        for row_number, row in enumerate(reader, start=1):
            values = [str(row.get(column) or "") for column in COLUMNS]
            video_path = qscript.CaptureVideo_Magento(*values)

            if video_path:
                print(f"Video saved: {video_path}")

                if args.server:
                    company_name = str(row.get("Company Name") or "").strip()

                    try:
                        video_url = UploadVideo(
                            args.server,
                            video_path,
                            company_name
                        )

                        another_video_url =  GetVideoURL(
                            args.server,
                            company_name,
                        )

                        # Added this check because GetVideoURL() is never used anywhere
                        if video_url == another_video_url:

                            preview_video(
                                video_url,
                                args.view_seconds
                            )
                        else:
                            print("Video upload failed")

                    except Exception as error:
                        print(f"Upload/Preview failed: {error}")

            else:
                print(f"Failed to record row {row_number}: {row.get('Company Name', '')}")



if __name__ == "__main__":
    main()
