FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .
COPY handlers/ handlers/
COPY services/ services/
COPY prompts.py .

RUN mkdir -p images

CMD ["python", "main.py"]
