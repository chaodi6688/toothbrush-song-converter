/* 浏览器内识别/转换引擎:Pyodide + 现有 Python 算法
 * 主线程通过 postMessage 调用,协议:
 *   -> { id, kind: "audio"|"image"|"pages", songName, filename, params, buffer?|pages?: ArrayBuffer[] }
 *   <- { id, ok, result?, error? } / { type: "status", stage, detail? }
 */

const PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";

const PY_FILES = [
  "py/app/__init__.py",
  "py/app/models.py",
  "py/app/converter/__init__.py",
  "py/app/converter/mapper.py",
  "py/app/converter/quantizer.py",
  "py/app/converter/loop.py",
  "py/app/converter/pipeline.py",
  "py/app/recognition/__init__.py",
  "py/app/recognition/audio.py",
  "py/app/recognition/image_omr.py",
  "py/web_entry.py",
];

let enginePromise = null;

function post(msg) {
  self.postMessage(msg);
}

function status(stage, detail) {
  post({ type: "status", stage, detail });
}

async function fetchBytes(url) {
  const resp = await fetch(new URL(url, self.location.href).toString());
  if (!resp.ok) throw new Error(`加载 ${url} 失败(HTTP ${resp.status})`);
  return new Uint8Array(await resp.arrayBuffer());
}

async function createEngine() {
  importScripts(PYODIDE_BASE + "pyodide.js");
  status("engine", "正在加载 Pyodide 运行时…");
  const py = await loadPyodide({ indexURL: PYODIDE_BASE });

  status("engine", "正在加载 numpy / Pillow…");
  await py.loadPackage(["numpy", "pillow"]);

  status("engine", "正在装载识别算法…");
  for (const rel of PY_FILES) {
    const bytes = await fetchBytes(rel);
    const parts = ("/" + rel).split("/");
    parts.pop();
    py.FS.mkdirTree(parts.join("/") || "/");
    py.FS.writeFile("/" + rel, bytes);
  }
  try {
    const font = await fetchBytes("fonts/DejaVuSans.ttf");
    py.FS.mkdirTree("/fonts");
    py.FS.writeFile("/fonts/DejaVuSans.ttf", font);
  } catch (err) {
    // 字体缺失时乐谱图片识别会退化,但不影响音频
    console.warn("字体加载失败:", err);
  }

  py.runPython(`
import json, pathlib, sys, traceback
sys.path.insert(0, "/py")
import web_entry

def _wrap(fn):
    def inner(*args):
        try:
            return fn(*args)
        except Exception as exc:
            return json.dumps({"error": str(exc) or exc.__class__.__name__}, ensure_ascii=False)
    return inner

def _audio(song, filename, path, params):
    data = pathlib.Path(path).read_bytes()
    return _wrap(web_entry.convert_audio_bytes)(song, filename, data, params)

def _image(song, filename, path, params):
    data = pathlib.Path(path).read_bytes()
    return _wrap(web_entry.convert_image_bytes)(song, filename, data, params)

def _pages(song, paths_json, params):
    paths = json.loads(paths_json)
    data = [pathlib.Path(p).read_bytes() for p in paths]
    return _wrap(web_entry.convert_pages_bytes)(song, data, params)
`);

  status("ready", "识别引擎已就绪");
  return py;
}

function engine() {
  if (!enginePromise) enginePromise = createEngine();
  return enginePromise;
}

async function handle(job) {
  const py = await engine();
  const params = JSON.stringify(job.params || {});

  if (job.kind === "audio") {
    py.FS.writeFile("/tmp_upload.bin", new Uint8Array(job.buffer));
    status("working", "正在分析音频(音高跟踪)…");
    return py.globals.get("_audio")(job.songName, job.filename, "/tmp_upload.bin", params);
  }

  if (job.kind === "image") {
    py.FS.writeFile("/tmp_upload.bin", new Uint8Array(job.buffer));
    status("working", "正在识别乐谱图片…");
    return py.globals.get("_image")(job.songName, job.filename, "/tmp_upload.bin", params);
  }

  if (job.kind === "pages") {
    const paths = [];
    job.pages.forEach((buf, i) => {
      const p = `/tmp_page_${i}.png`;
      py.FS.writeFile(p, new Uint8Array(buf));
      paths.push(p);
    });
    status("working", `正在识别乐谱 PDF(${job.pages.length} 页)…`);
    return py.globals.get("_pages")(job.songName, JSON.stringify(paths), params);
  }

  throw new Error("未知的任务类型:" + job.kind);
}

self.onmessage = async (event) => {
  const job = event.data;
  if (!job || !job.id) return;
  try {
    const json = await handle(job);
    const result = JSON.parse(json);
    if (result && result.error) {
      post({ id: job.id, ok: false, error: result.error });
    } else {
      post({ id: job.id, ok: true, result });
    }
  } catch (err) {
    post({ id: job.id, ok: false, error: String((err && err.message) || err) });
  }
};
