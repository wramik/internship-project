import json
import os
import subprocess
import sys
import tempfile
import time
import csv
import io

import psycopg2
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename


app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_FOLDER = os.environ.get(
    "UPLOAD_FOLDER",
    os.path.join(BASE_DIR, "uploads"),
)
APPLY_ZOOM_SCRIPT = os.path.join(BASE_DIR, "apply_zoom.py")

os.makedirs(VIDEO_FOLDER, exist_ok=True)


def get_db_connection():
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing database environment variables: " + ", ".join(missing)
        )

    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=5,
    )

def read_companies_csv(file):
    content = file.read().decode("utf-8-sig")
    csv_file = io.StringIO(content)

    reader = csv.DictReader(
        csv_file,
        delimiter=";",
        quotechar='"'
    )

    return list(reader)

def validate_companies_csv(rows):
    required_columns = {"Company Name", "Web"}

    if not rows:
        raise ValueError("CSV file is empty")

    actual_columns = set(rows[0].keys())

    missing_columns = required_columns - actual_columns

    if missing_columns:
        raise ValueError(
            f"Missing required columns: {', '.join(sorted(missing_columns))}"
        )

    for row_number, row in enumerate(rows, start=2):
        if not row.get("Company Name", "").strip():
            raise ValueError(
                f"Company Name is empty in CSV row {row_number}"
            )

        if not row.get("Web", "").strip():
            raise ValueError(
                f"Web is empty in CSV row {row_number}"
            )

    return True

def import_companies(rows):
    imported = 0
    updated = 0

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:

            for row in rows:
                cursor.execute(
                    """
                    INSERT INTO companies (
                        company_name,
                        language,
                        country,
                        postcode,
                        city,
                        street,
                        house_no,
                        web
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (company_name)
                    DO UPDATE SET
                        language = EXCLUDED.language,
                        country = EXCLUDED.country,
                        postcode = EXCLUDED.postcode,
                        city = EXCLUDED.city,
                        street = EXCLUDED.street,
                        house_no = EXCLUDED.house_no,
                        web = EXCLUDED.web,
                        updated_at = NOW()
                    RETURNING (xmax = 0) AS inserted;
                    """,
                    (
                        row["Company Name"].strip(),
                        row.get("Language", "").strip() or None,
                        row.get("Country", "").strip() or None,
                        row.get("Postcode", "").strip() or None,
                        row.get("City", "").strip() or None,
                        row.get("Street", "").strip() or None,
                        row.get("HouseNo", "").strip() or None,
                        row["Web"].strip(),
                    ),
                )

                inserted = cursor.fetchone()[0]

                if inserted:
                    imported += 1
                else:
                    updated += 1

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return imported, updated

def init_db():
    max_attempts = int(os.environ.get("DB_CONNECT_RETRIES", "30"))
    retry_delay = float(os.environ.get("DB_CONNECT_DELAY", "2"))

    for attempt in range(1, max_attempts + 1):
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS videos (
                        id SERIAL PRIMARY KEY,
                        company_name TEXT NOT NULL UNIQUE,
                        file_path TEXT NOT NULL,
                        video_url TEXT NOT NULL,
                        processed_video_url TEXT,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    );
                    """
                )
                schema_path = os.path.join(BASE_DIR, "schema.sql")

                with open(schema_path, "r", encoding="utf-8") as schema_file:
                    cursor.execute(schema_file.read())

            conn.commit()
            app.logger.info("PostgreSQL is ready; tables 'videos' and 'companies' exists")
            return
        except psycopg2.OperationalError:
            if attempt == max_attempts:
                app.logger.exception("Could not connect to PostgreSQL")
                raise
            app.logger.warning(
                "PostgreSQL is not ready (attempt %s/%s); retrying in %s sec",
                attempt,
                max_attempts,
                retry_delay,
            )
            time.sleep(retry_delay)
        finally:
            if conn is not None:
                conn.close()


def process_video(video_path, zoom_json_path=None):
    if not os.path.isfile(APPLY_ZOOM_SCRIPT):
        raise FileNotFoundError("apply_zoom.py was not found")

    video_name, extension = os.path.splitext(video_path)
    output_path = video_name + "_zoomed" + (extension or ".webm")

    temporary_json = None

    if zoom_json_path is None:
        temporary_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            encoding="utf-8",
            delete=False,
        )
        json.dump({"events": []}, temporary_file)
        temporary_file.close()
        zoom_json_path = temporary_file.name
        temporary_json = temporary_file.name

    command = [
        sys.executable,
        APPLY_ZOOM_SCRIPT,
        video_path,
        zoom_json_path,
        "--o",
        output_path,
    ]

    try:
        subprocess.run(command, check=True)

        if not os.path.isfile(output_path):
            raise RuntimeError("apply_zoom.py did not create the output video")

        return output_path
    finally:
        if temporary_json and os.path.isfile(temporary_json):
            os.remove(temporary_json)


@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "video web service", "status": "running"}), 200


@app.route("/health", methods=["GET"])
def health():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
    finally:
        conn.close()
    return jsonify({"status": "ok", "database": "ok"}), 200


@app.route("/companies", methods=["GET"])
def get_companies():
    status = request.args.get("status")

    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            if status is not None:
                try:
                    status = int(status)
                except ValueError:
                    return jsonify({"error": "Status must be an integer"}), 400

                cursor.execute(
                    "SELECT * FROM companies WHERE status = %s ORDER BY id",
                    (status,)
                )
            else:
                cursor.execute(
                    "SELECT * FROM companies ORDER BY id"
                )

            rows = cursor.fetchall()

            columns = [column[0] for column in cursor.description]

            companies = [
                dict(zip(columns, row))
                for row in rows
            ]

            return jsonify(companies), 200

    finally:
        conn.close()

@app.route("/companies/<company_name>", methods=["GET"])
def get_company(company_name):
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM companies WHERE company_name = %s",
                (company_name,)
            )

            row = cursor.fetchone()

            if row is None:
                return jsonify({"error": "Company not found"}), 404

            columns = [column[0] for column in cursor.description]

            company = dict(zip(columns, row))

            return jsonify(company), 200

    finally:
        conn.close()

@app.route("/upload", methods=["POST"])
def upload_video():
    if "video" not in request.files:
        return jsonify({"error": "Video file is missing"}), 400

    if "company_name" not in request.form:
        return jsonify({"error": "Company name is missing"}), 400

    video = request.files["video"]
    zoom_json = request.files.get("zoom_json")
    company_name = request.form["company_name"].strip()

    if not company_name:
        return jsonify({"error": "Company name is empty"}), 400

    if not video.filename:
        return jsonify({"error": "Invalid filename"}), 400

    filename = secure_filename(video.filename)
    if not filename:
        return jsonify({"error": "Invalid filename"}), 400

    zoom_json_path = None

    if zoom_json is not None:
        if (
            not zoom_json.filename
            or not zoom_json.filename.lower().endswith(".zoom.json")
        ):
            return jsonify({"error": "Invalid zoom JSON filename"}), 400

        try:
            zoom_data = json.load(zoom_json.stream)
            if not isinstance(zoom_data, (dict, list)):
                raise ValueError("Zoom JSON must contain an object or a list")

            if isinstance(zoom_data, dict) and not isinstance(
                zoom_data.get("events", []), list
            ):
                raise ValueError("Zoom JSON field 'events' must contain a list")

            zoom_json.stream.seek(0)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            return jsonify({"error": f"Invalid zoom JSON: {error}"}), 400

    os.makedirs(VIDEO_FOLDER, exist_ok=True)
    save_path = os.path.join(VIDEO_FOLDER, filename)
    video.save(save_path)

    if zoom_json is not None:
        video_name, _ = os.path.splitext(filename)
        zoom_json_path = os.path.join(
            VIDEO_FOLDER,
            f"{video_name}.zoom.json",
        )
        zoom_json.save(zoom_json_path)

    video_url = request.host_url.rstrip("/") + f"/media/{filename}"

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO videos (
                    company_name,
                    file_path,
                    video_url,
                    processed_video_url
                )
                VALUES (%s, %s, %s, NULL)
                ON CONFLICT (company_name) DO UPDATE SET
                    file_path = EXCLUDED.file_path,
                    video_url = EXCLUDED.video_url,
                    processed_video_url = NULL,
                    updated_at = NOW();
                """,
                (company_name, save_path, video_url),
            )
        conn.commit()
    finally:
        conn.close()

    try:
        processed_path = process_video(save_path, zoom_json_path)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        app.logger.exception("Video processing failed")
        return jsonify(
            {
                "error": "Video uploaded, but processing failed",
                "details": str(error),
                "video_url": video_url,
            }
        ), 500

    processed_filename = os.path.basename(processed_path)
    processed_video_url = (
        request.host_url.rstrip("/") + f"/media/{processed_filename}"
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE videos
                SET processed_video_url = %s,
                    updated_at = NOW()
                WHERE company_name = %s;
                """,
                (processed_video_url, company_name),
            )
        conn.commit()
    finally:
        conn.close()

    return jsonify(
        {
            "message": "Video uploaded and processed successfully",
            "company_name": company_name,
            "video_url": video_url,
            "processed_video_url": processed_video_url,
        }
    ), 201

@app.route("/upload_companies", methods=["GET", "POST"])
def upload_companies():
    if request.method == "GET":
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Upload companies CSV</title>
        </head>
        <body>
            <h2>Upload companies CSV</h2>
            <form method="POST" enctype="multipart/form-data">
                <input type="file" name="file" accept=".csv" required>
                <button type="submit">Upload</button>
            </form>
        </body>
        </html>
        """

    try:
        file = request.files.get("file")

        if file is None or not file.filename:
            return jsonify({"error": "CSV file is required"}), 400

        rows = read_companies_csv(file)
        validate_companies_csv(rows)

        imported, updated = import_companies(rows)

        return jsonify({
            "imported": imported,
            "updated": updated,
            "total_rows": len(rows)
        }), 200

    except (ValueError, UnicodeDecodeError) as error:
        return jsonify({"error": str(error)}), 400

    except Exception:
        app.logger.exception("CSV import failed")
        return jsonify({"error": "CSV import failed"}), 500

@app.route("/get_video_url", methods=["GET"])
def get_video_url():
    company_name = request.args.get("company_name", "").strip()
    if not company_name:
        return jsonify({"error": "Company name is missing"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT video_url, processed_video_url
                FROM videos
                WHERE company_name = %s;
                """,
                (company_name,),
            )
            row = cursor.fetchone()
    finally:
        conn.close()

    if row is None:
        return jsonify({"error": "Video not found"}), 404

    return jsonify(
        {
            "company_name": company_name,
            "video_url": row[0],
            "processed_video_url": row[1],
        }
    ), 200


@app.route("/media/<path:filename>", methods=["GET"])
def get_video(filename):
    return send_from_directory(VIDEO_FOLDER, filename)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=False)
