FROM python:3.10

ENV PYTHONUNBUFFERED=1
ENV WORKDIR=/bot

WORKDIR $WORKDIR

RUN apt-get update && apt-get install -y build-essential

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]