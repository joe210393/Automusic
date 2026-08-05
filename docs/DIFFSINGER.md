# DiffSinger 系統代唱（本機安裝）

步驟 6 需要本機的 **DiffSinger**（虛擬歌手）＋ **Seed-VC**（換聲紋）。
雲端 Zeabur 會經 ngrok 把代唱／轉換委託回這台 Mac。

## 目錄結構

預設路徑：`~/diffsinger`（可用環境變數 `DIFFSINGER_DIR` 覆寫）

需要：

```text
~/diffsinger/
  infer_cli.py          # Automusic 呼叫的 CLI（repo 內已附）
  .venv/                # 獨立 Python 環境
  checkpoints/
    0228_opencpop_ds100_rel/model_ckpt_steps_160000.ckpt
    0102_xiaoma_pe/model_ckpt_steps_60000.ckpt
    0109_hifigan_bigpopcs_hop128/model_ckpt_steps_280000.ckpt
  data/binary/opencpop-midi-dp/phone_set.json
```

## 安裝摘要

```bash
git clone https://github.com/MoonInTheRiver/DiffSinger.git ~/diffsinger
cd ~/diffsinger
python3.11 -m venv .venv
.venv/bin/pip install -U 'setuptools<81' pip
.venv/bin/pip install torch torchaudio numpy scipy librosa soundfile \
  PyYAML tqdm pandas jieba pypinyin einops g2pM pretty-midi matplotlib \
  tensorboardX pyloudnorm h5py resampy praat-parselmouth webrtcvad \
  pycwt scikit-image

# 下載預訓練（Opencpop 女聲 + PE + HiFiGAN）
# 見 GitHub Releases: MoonInTheRiver/DiffSinger tag pretrain-model
# - 0228_opencpop_ds100_rel.zip（只解 config.yaml + ckpt）
# - 0102_xiaoma_pe.zip
# - 0109_hifigan_bigpopcs_hop128.zip

# 相容性補丁：新版 scipy 沒有 signal.kaiser
# 在 modules/parallel_wavegan/layers/pqmf.py 改為：
#   try: from scipy.signal import kaiser
#   except ImportError: from scipy.signal.windows import kaiser

# Automusic 的 infer_cli.py 請放到 ~/diffsinger/infer_cli.py
```

## 自測

```bash
cd ~/diffsinger
cat > /tmp/ds_job.json <<'EOF'
{"text":"海边夏天","notes":"C4 | E4 | G4 | A4","notes_duration":"0.4 | 0.4 | 0.4 | 0.5","input_type":"word"}
EOF
PYTHONPATH=. PYTORCH_ENABLE_MPS_FALLBACK=1 \
  .venv/bin/python infer_cli.py --input /tmp/ds_job.json --output /tmp/ds_out.wav
```

## 注意

- 發音為**普通話**（Opencpop）；繁中歌詞仍可唱，腔調偏陸。
- 模型約佔 1.4 GB 磁碟；推論時請保留足夠 RAM。
- LaunchAgent 跑 Automusic 時需設定 `HOME`，才能找到 `~/diffsinger` 與 HuggingFace／Seed-VC 快取。
