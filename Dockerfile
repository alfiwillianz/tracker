FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py students.tsv* ./
COPY templates templates
COPY static static

VOLUME /data
EXPOSE 5000

# single worker: SQLite + first-run seeding
CMD ["gunicorn", "-b", "0.0.0.0:5000", "-w", "1", "--threads", "4", "app:app"]
