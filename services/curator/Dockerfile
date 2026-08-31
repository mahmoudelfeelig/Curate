FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FEED_PASSPORT_DB_PATH=/var/lib/feed-passport/state.db

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 curator \
    && mkdir -p /var/lib/feed-passport \
    && chown -R curator:curator /var/lib/feed-passport
USER curator

EXPOSE 8000
CMD ["uvicorn", "feed_passport.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
