FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    URBAN_SYSTEM_CONFIG=/app/config.json

WORKDIR /build
COPY requirements.txt /build/sigmus/requirements.txt
RUN pip install --no-cache-dir -r /build/sigmus/requirements.txt

COPY --from=observation_processing / /build/urban-observation-processing
COPY . /build/sigmus
RUN pip install --no-cache-dir --no-deps /build/urban-observation-processing \
    && pip install --no-cache-dir --no-deps /build/sigmus \
    && rm -rf /build

WORKDIR /app
RUN useradd --create-home --uid 10001 sigmus \
    && mkdir -p /state /streams \
    && chown -R sigmus:sigmus /state /streams
USER sigmus

CMD ["python", "-m", "database_storage.worker"]
