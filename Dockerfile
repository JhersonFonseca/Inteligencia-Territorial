FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY territorial ./territorial
COPY tests ./tests
COPY herramientas ./herramientas

RUN pip install --no-cache-dir ".[pruebas]"

EXPOSE 8000

# Railway asigna el puerto en $PORT; en local se usa 8000.
CMD ["sh", "-c", "uvicorn territorial.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
