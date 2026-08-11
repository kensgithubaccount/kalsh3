FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY services ./services
RUN useradd --system --uid 10001 app
USER 10001
CMD ["python", "-m", "services.web_dashboard.run"]
