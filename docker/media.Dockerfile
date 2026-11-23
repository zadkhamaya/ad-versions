# trixie (Debian 13) ships ffmpeg 7.x; bookworm's 5.1 has no drawtext `text_align`, which media/sheet.py uses
FROM python:3.12-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
# faster-whisper's model downloads here, on the persisted data volume: it survives a container recreate (first run needs internet once)
ENV HF_HOME=/data/hf
WORKDIR /app
COPY pyproject.toml constraints.txt ./
# dependencies only (the app runs from /app, not as an installed package), so this layer caches across code changes;
# constraints.txt pins the indirect ones too (a pip freeze of the tested venv), so the image is the same one tested
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/req.txt \
 && pip install --no-cache-dir -r /tmp/req.txt -c constraints.txt
COPY media ./media
COPY brand ./brand
COPY config ./config
RUN useradd -u 1000 -m app
USER app
CMD ["uvicorn", "media.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
