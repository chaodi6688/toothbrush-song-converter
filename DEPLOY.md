# 把本项目部署成在线网站(固定公网链接)

## 已上线(在线版,推荐)

**https://chaodi6688.github.io/toothbrush-song-converter/**

- 托管:GitHub Pages(`docs/` 目录,已提交 `.nojekyll`)
- 运行方式:纯浏览器本地计算(Pyodide + numpy + Pillow),文件不上传服务器
- 支持:WAV / MP3 / M4A 音频,PNG / JPG / PDF 乐谱;试听、拖动进度、导出 `.txt` / `.wav`
- 首次打开需下载识别引擎(约 10 MB,浏览器会缓存),之后打开即可直接使用
- 源码:https://github.com/chaodi6688/toothbrush-song-converter

本项目是「Python 标准库 HTTP 服务 + numpy/Pillow」,不需要数据库,数据不落盘,
因此可以直接部署到免费云平台。`app/server.py` 已支持云端注入的 `PORT` 环境变量并监听 `0.0.0.0`。

仓库里已准备好两套免费方案所需的文件:

| 文件 | 用途 |
|---|---|
| `Procfile` / `render.yaml` | Render 部署 |
| `Dockerfile` / `.dockerignore` / `deploy/hf-README.md` | Hugging Face Spaces(Docker)部署 |
| `requirements.txt` | 依赖(numpy、Pillow) |

---

## 方案 A:Hugging Face Spaces(推荐,免费、无需信用卡)

1. 注册/登录 <https://huggingface.co/join>
2. 右上角头像 → **New Space**
   - Space name:例如 `toothbrush-song-converter`
   - Select the Space SDK:**Docker**
   - Hardware:CPU basic(免费)
   - Visibility:**Public**(或 Private,需登录才能访问)
3. 上传项目文件(任选其一):
   - 网页方式:进入 Space 的 **Files** → **Add file** → **Upload files**,把本项目里除
     `work/`、`outputs/`、`*.log` 之外的文件/文件夹全部拖进去(必须包含 `app/`、`Dockerfile`、
     `requirements.txt`)。
   - Git 方式:
     ```bash
     git clone https://huggingface.co/spaces/<你的用户名>/toothbrush-song-converter
     # 把本项目文件复制进该目录(排除 work/ outputs/ *.log)
     git add . && git commit -m "deploy toothbrush converter"
     git push   # 用户名 + Access Token(Settings → Access Tokens,权限选 write)
     ```
4. 把 `deploy/hf-README.md` 的内容复制到仓库根目录 `README.md` 的最顶部
   (其中的 `sdk: docker`、`app_port: 7860` 决定 Space 如何启动)。
5. 等待构建完成(首次约 2–5 分钟),访问:
   `https://<你的用户名>-toothbrush-song-converter.hf.space`

## 方案 B:Render(免费 Web Service,需要 GitHub 账号)

1. 把项目推送到 GitHub 仓库。
2. 登录 <https://render.com> → **New** → **Web Service** → 选择该仓库。
3. 配置(与仓库里的 `render.yaml` 一致):
   - Language:**Python**
   - Build Command:`pip install -r requirements.txt`
   - Start Command:`python app/server.py`
   - Instance Type:**Free**
   - Health Check Path:`/api/health`
4. 部署完成后访问 `https://<服务名>.onrender.com`。

---

## 注意事项

- **免费实例会休眠**:Render 免费实例约 15 分钟无访问会休眠,再次访问需等待约 50 秒冷启动;
  Hugging Face Spaces 免费实例长时间无访问也可能休眠。
- **上传体积**:本项目默认允许音频 ≤ 50 MB、图片 ≤ 20 MB;请确认平台的请求体上限不低于该值。
- **MP3 / FLAC / M4A**:需要 `ffmpeg`。云平台镜像中可在 `Dockerfile` 里取消对应 `apt-get install ffmpeg`
  注释后重新部署(体积会增加约 100 MB);否则请上传 WAV。
- **隐私**:页面文案可自行调整;服务本身不保存上传文件,处理在内存中完成。

## 本地运行(备选)

- 双击 `start.bat`:前台启动,自动打开浏览器。
- 或运行 `run_hidden.vbs`:后台隐藏启动(带自动重启看门狗),日志写入 `server.log`。
- 启动文件夹中的「牙刷歌曲转换器」快捷方式会在登录后自动运行它。
