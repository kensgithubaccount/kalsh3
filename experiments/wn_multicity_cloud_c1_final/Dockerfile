FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY cloud_collector.py canary.py checks.py fixtures.py ./
COPY frozen/ ./frozen/
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-u", "cloud_collector.py"]
