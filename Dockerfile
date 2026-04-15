FROM python:3.12-slim AS base

# Install system dependencies: ffmpeg (required by yt-dlp) and yt-dlp
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    pip install --no-cache-dir yt-dlp && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --create-home app

WORKDIR /app

# Install Python dependencies first (cache layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy source code
COPY src/ ./src/

# Create data directory for SQLite
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app

ENTRYPOINT ["python", "-m", "soccergoals.main"]
