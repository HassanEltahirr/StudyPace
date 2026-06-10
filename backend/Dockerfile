FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure the data directory exists for SQLite and uploaded source material
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data/source_material /app/data/slide_images /app/data/user_workspaces \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
