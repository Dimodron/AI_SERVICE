FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY Pipfile Pipfile.lock ./
RUN pip install --no-cache-dir pipenv \
    && pipenv install --system --deploy

COPY settings.toml ./settings.toml
COPY app/ ./app/
WORKDIR /srv/app

EXPOSE 8282
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8282"]
