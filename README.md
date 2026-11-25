
# Zoom Video Processing

A Camtasia-style zoom effect for recorded videos using FFmpeg.

## Features

- Press `F5` to zoom in/out during recording.
- Supports programmatic zoom via `window.__zoomAt(x, y)`.
- Saves zoom events to a `.zoom.json` sidecar file.
- Applies smooth zoom-in, hold, and zoom-out effects.
- Supports multiple zoom events and open-ended zoom.
- Automatically processes uploaded videos in Docker.
- Stores original and processed video URLs in PostgreSQL.

## Requirements

- Python 3
- Docker and Docker Compose
- FFmpeg

## Run

Start the application:

```bash
docker compose up -d --build
```

Record and upload videos:

```bash
python capture_video.py --server http://localhost:8000
```

Process a video manually:

```bash
python apply_zoom.py --video "video/example.webm"
```

Preview the FFmpeg command without processing:

```bash
python apply_zoom.py --video "video/example.webm" --dry-run
```

Run tests:

```bash
python -m pytest -q
```

Stop the application:

```bash
docker compose down
```

## Company Data Enrichment

### Upload Companies

Open the upload page in your browser:

```text
http://localhost:8000/upload_companies
```

Select a CSV file and upload it.

Companies are imported into PostgreSQL. Re-importing the same CSV does not duplicate records or overwrite enrichment results.

### Enrich Companies

Run the enrichment script:

```bash
docker compose exec web python enrich_companies.py
```

The script automatically selects companies that have not been successfully processed and:

- Opens each company's website.
- Finds the Impressum/Imprint page.
- Extracts visible page text.
- Optimizes the text to reduce token usage.
- Sends the text to DeepSeek for legal data extraction.
- Saves the extracted information and processing status in PostgreSQL.

Each company is processed individually. Errors do not stop the remaining batch.

### Enrich a Specific Company

```bash
docker compose exec web python enrich_companies.py --company "Example GmbH"
```

### Limit the Number of Companies

```bash
docker compose exec web python enrich_companies.py --limit 10
```

### Process Companies by Status

```bash
docker compose exec web python enrich_companies.py --status 3
```

### View Company Data

Open the list of companies:

```text
http://localhost:8000/companies
```

View a specific company:

```text
http://localhost:8000/companies/Example%20GmbH
```

Filter companies by status:

```text
http://localhost:8000/companies?status=0
```
