FROM python:3.11-slim

WORKDIR /app

# 安裝系統依賴（aubio、soundfile、FluidSynth 高音質轉檔）
RUN apt-get update && apt-get install -y \
    libaubio-dev \
    libsndfile1 \
    fluidsynth \
    fluid-soundfont-gm \
    && rm -rf /var/lib/apt/lists/*

# FluidSynth 用的 GM 音色庫路徑
ENV SOUNDFONT_PATH=/usr/share/sounds/sf2/FluidR3_GM.sf2

# 複製 requirements 並安裝 Python 依賴
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用程式碼
COPY app/ ./app/

# 建立 /tmp 目錄
RUN mkdir -p /tmp

# 暴露端口
EXPOSE 8080

# 啟動應用
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
