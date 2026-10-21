FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# 如需支持 MP3 / FLAC / M4A,取消下一行注释(会增大镜像体积)
# RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

ENV PORT=7860
EXPOSE 7860

CMD ["python", "app/server.py"]
