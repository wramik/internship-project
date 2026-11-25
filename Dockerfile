FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UPLOAD_FOLDER=/app/uploads

RUN apt-get update && apt-get install -y --no-install-recommends \
        apache2 \
        apache2-dev \
        build-essential \
        libpq-dev \
	ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

RUN mod_wsgi-express module-config > /etc/apache2/mods-available/wsgi.load \
    && a2enmod wsgi

COPY . /app
COPY apache-flask.conf /etc/apache2/sites-available/000-default.conf

RUN mkdir -p /app/uploads \
    && chown -R www-data:www-data /app/uploads

EXPOSE 80
CMD ["apache2ctl", "-D", "FOREGROUND"]
