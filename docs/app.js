(() => {
  "use strict";

  const MAX = { image: 20 * 1024 * 1024, audio: 50 * 1024 * 1024 };
  const EXTS = {
    image: [".png", ".jpg", ".jpeg", ".pdf"],
    audio: [".wav", ".mp3", ".flac", ".m4a"],
  };

  const $ = (id) => document.getElementById(id);
  const state = {
    type: null,           // 'image' | 'audio'
    file: null,
    result: null,
    audioCtx: null,
    buffer: null,
    listenMode: "converted",
    source: null,
    playing: false,
    startedAt: 0,
    rafId: null,
    seekOffset: 0,   // 当前播放位置(秒),用于拖动跳转与暂停续播
    dragging: false,
  };

  const imageCard = $("imageCard"), audioCard = $("audioCard");
  const imageInput = $("imageInput"), audioInput = $("audioInput");
  const bar = $("progressBar");

  // ---------------- 文件选择 ----------------
  function selectFile(type, file) {
    if (!file) return;
    const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
    if (!EXTS[type].includes(ext)) {
      setStatus(`格式不支持:${ext}。请上传 ${EXTS[type].join(" / ")} 文件。`, "error");
      return;
    }
    if (file.size > MAX[type]) {
      setStatus(`文件超过 ${MAX[type] / 1024 / 1024} MB 上限。`, "error");
      return;
    }
    state.type = type;
    state.file = file;
    state.result = null;
    imageCard.classList.toggle("selected", type === "image");
    audioCard.classList.toggle("selected", type === "audio");
    const chip = type === "image" ? $("imageChip") : $("audioChip");
    chip.textContent = `${file.name} (${(file.size / 1024).toFixed(0)} KB)`;
    chip.hidden = false;
    $("resultSection").hidden = true;
    $("playerSection").hidden = true;
    $("exportSection").hidden = true;
    stopPlayback();
    setStatus("文件已就绪,点击「开始识别并转换」。", "ok");
    $("convertBtn").disabled = false;
  }

  imageCard.addEventListener("click", () => imageInput.click());
  audioCard.addEventListener("click", () => audioInput.click());
  imageInput.addEventListener("change", (e) => selectFile("image", e.target.files[0]));
  audioInput.addEventListener("change", (e) => selectFile("audio", e.target.files[0]));

  ["dragenter", "dragover"].forEach((ev) => {
    [imageCard, audioCard].forEach((card) =>
      card.addEventListener(ev, (e) => { e.preventDefault(); card.classList.add("active"); })
    );
  });
  ["dragleave", "drop"].forEach((ev) => {
    [imageCard, audioCard].forEach((card) =>
      card.addEventListener(ev, (e) => { e.preventDefault(); card.classList.remove("active"); })
    );
  });
  imageCard.addEventListener("drop", (e) => selectFile("image", e.dataTransfer.files[0]));
  audioCard.addEventListener("drop", (e) => selectFile("audio", e.dataTransfer.files[0]));

  // 内置示例:在浏览器里合成《小星星》前两句
  function buildSampleWav() {
    const semitones = [0, 0, 7, 7, 9, 9, 7, -1, 5, 5, 4, 4, 2, 2, 0, -1];
    const sr = 22050;
    let total = 0;
    for (const st of semitones) total += st === -1 ? 0.5 : 0.25;
    const data = new Float32Array(Math.ceil(total * sr));
    let offset = 0;
    for (const st of semitones) {
      const dur = st === -1 ? 0.5 : 0.25;
      const n = Math.round(dur * sr);
      if (st >= 0) {
        const freq = 440 * Math.pow(2, (60 - 69 + st) / 12);
        const inc = (2 * Math.PI * freq) / sr;
        const fade = Math.min(Math.round(0.015 * sr), Math.floor(n / 2));
        let phase = 0;
        for (let i = 0; i < n; i++) {
          let env = 1;
          if (i < fade) env = i / fade;
          else if (i > n - fade) env = (n - i) / fade;
          data[offset + i] = 0.35 * env * Math.sin(phase);
          phase += inc;
        }
      }
      offset += n;
    }
    return encodePcmWav([data], sr);
  }

  $("sampleBtn").addEventListener("click", () => {
    try {
      const blob = buildSampleWav();
      const file = new File([blob], "小星星_示例.wav", { type: "audio/wav" });
      selectFile("audio", file);
    } catch (err) {
      setStatus("示例音频生成失败:" + err.message, "error");
    }
  });

  // ---------------- 转换 ----------------
  function setStatus(msg, cls) {
    const el = $("status");
    el.textContent = msg;
    el.className = "status" + (cls ? " " + cls : "");
  }

  // ---------------- 浏览器内识别引擎(Pyodide Worker) ----------------
  const engineState = { worker: null, seq: 0, pending: new Map() };

  function setEngineStatus(stage, detail) {
    const el = $("engineStatus");
    if (!el) return;
    if (stage === "engine") el.textContent = "识别引擎:加载中…" + (detail ? " " + detail : "");
    else if (stage === "ready") el.textContent = "识别引擎:已就绪(全部在本机浏览器处理)";
    else if (stage === "working") el.textContent = "识别引擎:处理中…" + (detail ? " " + detail : "");
    el.classList.toggle("ok", stage === "ready");
  }

  function engineWorker() {
    if (!engineState.worker) {
      const w = new Worker("worker.js");
      w.onmessage = (ev) => {
        const msg = ev.data || {};
        if (msg.type === "status") {
          setEngineStatus(msg.stage, msg.detail);
          if (msg.stage !== "ready") setStatus(msg.detail || "处理中…", "");
          return;
        }
        const pending = engineState.pending.get(msg.id);
        if (!pending) return;
        engineState.pending.delete(msg.id);
        if (msg.ok) pending.resolve(msg.result);
        else pending.reject(new Error(msg.error || "处理失败"));
      };
      w.onerror = (ev) => {
        setStatus("识别引擎出错:" + (ev.message || "请刷新页面重试"), "error");
      };
      engineState.worker = w;
    }
    return engineState.worker;
  }

  function askEngine(job, transfer) {
    const w = engineWorker();
    job.id = ++engineState.seq;
    return new Promise((resolve, reject) => {
      engineState.pending.set(job.id, {
        resolve: (v) => { setEngineStatus("ready"); resolve(v); },
        reject: (e) => { setEngineStatus("ready"); reject(e); },
      });
      w.postMessage(job, transfer || []);
    });
  }

  function extOf(name) {
    return "." + (name.split(".").pop() || "").toLowerCase();
  }

  function fileStem(name) {
    return name.replace(/\.[^.]+$/, "");
  }

  // 非 WAV 音频(MP3/FLAC/M4A)先用浏览器解码,再转成 WAV 交给识别算法
  async function toWavArrayBuffer(file) {
    if (extOf(file.name) === ".wav") return await file.arrayBuffer();
    const raw = await file.arrayBuffer();
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    let decoded;
    try {
      decoded = await ctx.decodeAudioData(raw.slice(0));
    } finally {
      try { await ctx.close(); } catch (e) { /* ignore */ }
    }
    const mono = new Float32Array(decoded.length);
    const chCount = decoded.numberOfChannels;
    for (let c = 0; c < chCount; c++) {
      const ch = decoded.getChannelData(c);
      for (let i = 0; i < ch.length; i++) mono[i] += ch[i] / chCount;
    }
    return await encodePcmWav([mono], decoded.sampleRate).arrayBuffer();
  }

  // PDF 乐谱:用 pdf.js 渲染成 PNG 页面,再交给乐谱识别
  async function pdfToPngPages(file) {
    const lib = window.pdfjsLib;
    if (!lib) throw new Error("PDF 组件未加载,请刷新页面后重试");
    lib.GlobalWorkerOptions.workerSrc = "vendor/pdf.worker.min.js";
    const data = new Uint8Array(await file.arrayBuffer());
    const pdf = await lib.getDocument({ data }).promise;
    const pages = [];
    const count = Math.min(pdf.numPages, 10);
    for (let i = 1; i <= count; i++) {
      const page = await pdf.getPage(i);
      const viewport = page.getViewport({ scale: 2 });
      const canvas = document.createElement("canvas");
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      const ctx2d = canvas.getContext("2d");
      await page.render({ canvasContext: ctx2d, viewport }).promise;
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("PDF 页面渲染失败");
      pages.push(await blob.arrayBuffer());
    }
    return pages;
  }

  async function convertInBrowser(type, file, params) {
    const songName = fileStem(file.name);
    if (type === "audio") {
      const buffer = await toWavArrayBuffer(file);
      return askEngine({
        kind: "audio",
        songName,
        filename: "upload.wav",
        params,
        buffer,
      }, [buffer]);
    }
    if (extOf(file.name) === ".pdf") {
      const pages = await pdfToPngPages(file);
      return askEngine({
        kind: "pages",
        songName,
        filename: file.name,
        params,
        pages,
      }, pages);
    }
    const buffer = await file.arrayBuffer();
    return askEngine({
      kind: "image",
      songName,
      filename: file.name,
      params,
      buffer,
    }, [buffer]);
  }

  $("convertBtn").addEventListener("click", async () => {
    if (!state.file) {
      setStatus("请先选择音频或图片文件。", "error");
      return;
    }
    const btn = $("convertBtn");
    btn.disabled = true;
    setStatus("准备中…", "");
    try {
      const params = {
        target_duration: $("targetDuration").value,
        freq_min: $("freqMin").value,
        freq_max: $("freqMax").value,
        bpm: $("bpm").value || "",
        loop: $("loopToggle").checked,
        trim_rests: $("loopToggle").checked,
        preserve_pitch: $("pitchToggle").checked,
        merge_rests: $("mergeRestsToggle").checked,
      };
      const data = await convertInBrowser(state.type, state.file, params);
      state.result = data;
      renderResult(data);
      setStatus("转换完成 ✔ 可试听或下载。", "ok");
      $("resultSection").hidden = false;
      $("playerSection").hidden = false;
      $("exportSection").hidden = false;
      $("resultSection").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      setStatus(err.message || "处理失败,请重试。", "error");
    } finally {
      btn.disabled = false;
    }
  });

  // ---------------- 渲染结果 ----------------
  function renderResult(r) {
    const bpmLabel = {
      user: `(手动)`,
      auto: `(自动估计)`,
      default: `(默认)`,
    }[r.bpm_source] || "";
    const stats = [
      ["识别音符", r.note_count],
      ["BPM", `${r.bpm.toFixed(0)} ${bpmLabel}`],
      ["识别音域", `${r.pitch_min_hz}-${r.pitch_max_hz} Hz`],
      ["映射后音域", `${r.min_freq}-${r.max_freq} Hz`],
      ["播放事件", r.event_count],
      ["其中休止", r.rest_count],
      ["总时长", r.total_duration.toFixed(3) + " s"],
      ["平均置信度", (r.avg_confidence * 100).toFixed(0) + "%"],
      ["目标时长", r.metadata.freq_window ? "120.000 s" : "120.000 s"],
    ];
    $("resultStats").innerHTML = stats
      .map(([k, v]) => `<div class="stat"><span class="k">${k}</span><span class="v">${v}</span></div>`)
      .join("");

    const dur = $("durationBadge");
    dur.textContent = r.duration_ok ? "✔ 总时长校验:通过 (±0.5s)" : "✘ 总时长校验:未通过";
    dur.className = "badge " + (r.duration_ok ? "ok" : "fail");
    const fr = $("freqBadge");
    if (r.preserve_pitch) {
      fr.textContent = "ℹ 已保持原音调:未执行频率映射";
      fr.className = "badge";
      fr.style.background = "#fef3c7";
      fr.style.color = "#92400e";
    } else {
      fr.textContent = r.freq_range_ok ? "✔ 频率范围校验:全部 ∈ 窗口" : "✘ 频率范围校验:存在越界";
      fr.className = "badge " + (r.freq_range_ok ? "ok" : "fail");
    }

    const msgs = [];
    r.messages.forEach((m) => msgs.push(`<p>ℹ ${m}</p>`));
    if (r.loop_point) msgs.push(`<p class="info">${r.loop_point}</p>`);
    if (!r.freq_range_ok) msgs.push(`<p>⚠ 频率越界,请检查频率窗口配置。</p>`);
    if (!r.preserve_pitch && (r.messages || []).some((m) => m.includes("变调") || m.includes("压缩"))) {
      msgs.push(`<p>⚠ 原曲音域 ${r.pitch_min_hz}-${r.pitch_max_hz} Hz 超出电机窗口,已映射为 ${r.min_freq}-${r.max_freq} Hz(高音被压低)。如需保持原音调,请在高级设置勾选「保持原音调」后重新转换。</p>`);
    }
    $("messages").innerHTML = msgs.join("");

    $("noteCountLabel").textContent = r.note_count;
    $("notesBody").innerHTML = (r.recognized_notes || [])
      .map((n, i) => {
        const freq = n.pitch > 0 ? n.pitch.toFixed(1) : "休止(0)";
        return `<tr><td>${i + 1}</td><td>${freq}</td><td>${n.start.toFixed(3)}</td><td>${n.duration.toFixed(3)}</td><td>${(n.confidence * 100).toFixed(0)}%</td></tr>`;
      })
      .join("");

    $("eventCountLabel").textContent = r.event_count;
    $("seqPreview").textContent = r.sequence
      .slice(0, 120)
      .map(([f, d]) => `(${f}, ${d.toFixed(3)})`)
      .join("\n") + (r.sequence.length > 120 ? "\n…(共 " + r.sequence.length + " 行)" : "");

    $("totalTime").textContent = r.total_duration.toFixed(1) + "s";
  }

  // ---------------- 试听播放(Web Audio API) ----------------
  function currentTotal() {
    const r = state.result;
    if (!r) return 0;
    return state.listenMode === "recognized" ? recognizedDuration(r) : r.total_duration;
  }

  function stopPlayback() {
    if (state.source) {
      try { state.source.onended = null; state.source.stop(); } catch (e) { /* ignore */ }
      state.source = null;
    }
    if (state.audioCtx) {
      try { state.audioCtx.suspend(); } catch (e) { /* ignore */ }
    }
    state.playing = false;
    state.seekOffset = 0;
    if (state.rafId) cancelAnimationFrame(state.rafId);
    $("playBtn").disabled = false;
    $("pauseBtn").disabled = true;
    $("stopBtn").disabled = true;
    updateProgress(0);
  }

  function buildBuffer(events) {
    const sr = 44100;
    const total = events.reduce((a, [, d]) => a + d, 0);
    const nTotal = Math.ceil(total * sr);
    const ctx = new AudioContext({ sampleRate: sr });
    const buffer = ctx.createBuffer(1, nTotal, sr);
    const data = buffer.getChannelData(0);
    const fade = Math.round(0.008 * sr); // 8ms 淡入淡出
    let offset = 0;
    for (const [freq, dur] of events) {
      const n = Math.max(1, Math.round(dur * sr));
      if (freq > 0) {
        const inc = (2 * Math.PI * freq) / sr;
        let phase = 0;
        for (let i = 0; i < n && offset + i < nTotal; i++) {
          let env = 1;
          if (i < fade) env = i / fade;
          else if (i > n - fade) env = Math.max(0, (n - i) / fade);
          // 基频 + 二次/三次谐波:更接近真实乐器的明亮音色,
          // 避免纯正弦在高音区听感发闷、偏低。
          const base =
            Math.sin(phase) +
            0.55 * Math.sin(2 * phase) +
            0.3 * Math.sin(3 * phase);
          data[offset + i] = base * 0.24 * env;
          phase += inc;
        }
      }
      offset += n;
    }
    return { ctx, buffer, total };
  }

  // 把多声道 Float32 数据编码为标准 WAV(16bit PCM)
  function encodePcmWav(channels, sampleRate) {
    const numCh = channels.length;
    const sr = sampleRate;
    const len = channels[0].length;
    const blockAlign = numCh * 2;
    const dataSize = len * blockAlign;
    const ab = new ArrayBuffer(44 + dataSize);
    const view = new DataView(ab);
    const writeStr = (off, s) => {
      for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
    };
    writeStr(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeStr(8, "WAVE");
    writeStr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, numCh, true);
    view.setUint32(24, sr, true);
    view.setUint32(28, sr * blockAlign, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true);
    writeStr(36, "data");
    view.setUint32(40, dataSize, true);

    let off = 44;
    for (let i = 0; i < len; i++) {
      for (let c = 0; c < numCh; c++) {
        const s = Math.max(-1, Math.min(1, channels[c][i]));
        view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
        off += 2;
      }
    }
    return new Blob([ab], { type: "audio/wav" });
  }

  // 把 AudioBuffer 编码为标准 WAV(16bit PCM),用于下载
  function encodeWav(buffer) {
    const chans = [];
    for (let c = 0; c < buffer.numberOfChannels; c++) chans.push(buffer.getChannelData(c));
    return encodePcmWav(chans, buffer.sampleRate);
  }

  // 需要时合成试听音频(播放与下载共用同一份)
  async function ensureBuffer() {
    const r = state.result;
    if (!r) return false;
    if (!state.buffer || !state.audioCtx) {
      const built = buildBuffer(currentEvents(r));
      state.audioCtx = built.ctx;
      state.buffer = built.buffer;
    }
    return true;
  }

  function updateProgress(t) {
    const r = state.result;
    if (!r) return;
    const total = currentTotal();
    if (!total) return;
    const pct = Math.min(100, (t / total) * 100);
    $("progressFill").style.width = pct + "%";
    $("progressThumb").style.left = pct + "%";
    $("progressTime").textContent = t.toFixed(1) + "s";
    bar.setAttribute("aria-valuenow", Math.round(pct).toString());
    bar.setAttribute("aria-valuetext", t.toFixed(1) + "s / " + total.toFixed(1) + "s");
  }

  function recognizedDuration(r) {
    return (r.recognized_notes || []).reduce((a, n) => a + n.duration, 0);
  }

  function currentEvents(r) {
    if (state.listenMode === "recognized") {
      return (r.recognized_notes || []).map((n) => [n.pitch, n.duration]);
    }
    return r.sequence;
  }

  // 试听内容切换:重新合成
  document.querySelectorAll('input[name="listenMode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      state.listenMode = radio.value;
      stopPlayback();
      state.buffer = null;
      state.audioCtx = null;
      updateProgress(0);
      if (state.result) {
        $("totalTime").textContent =
          (state.listenMode === "recognized" ? recognizedDuration(state.result) : state.result.total_duration).toFixed(1) + "s";
      }
    });
  });

  function startSourceAt(offset) {
    const r = state.result;
    const ctx = state.audioCtx;
    if (!r || !ctx || !state.buffer) return;
    const bufDur = state.buffer.duration;
    const total = currentTotal();
    const off = bufDur > 0 ? offset % bufDur : 0;
    const source = ctx.createBufferSource();
    source.buffer = state.buffer;
    source.loop = $("playerLoop").checked;
    source.connect(ctx.destination);
    source.onended = () => {
      if (state.playing) stopPlayback();
    };
    state.source = source;
    source.start(0, off);
    state.playing = true;
    state.startedAt = ctx.currentTime - off;
    $("playBtn").disabled = true;
    $("pauseBtn").disabled = false;
    $("stopBtn").disabled = false;
    const tick = () => {
      if (!state.playing) return;
      const t = ctx.currentTime - state.startedAt;
      const shown = source.loop && total > 0 ? t % total : t;
      state.seekOffset = shown;
      updateProgress(shown);
      if (!source.loop && t >= total) {
        stopPlayback();
        return;
      }
      state.rafId = requestAnimationFrame(tick);
    };
    state.rafId = requestAnimationFrame(tick);
  }

  // ---------------- 可拖动进度条 ----------------
  let dragFraction = 0;

  function fractionFromEvent(e) {
    const rect = bar.getBoundingClientRect();
    const x = rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0;
    return Math.max(0, Math.min(1, x));
  }

  function previewDrag(fraction) {
    const total = currentTotal();
    if (!total) return;
    const t = fraction * total;
    const pct = Math.min(100, fraction * 100);
    $("progressFill").style.width = pct + "%";
    $("progressThumb").style.left = pct + "%";
    $("progressTime").textContent = t.toFixed(1) + "s";
    bar.setAttribute("aria-valuenow", Math.round(pct).toString());
    bar.setAttribute("aria-valuetext", t.toFixed(1) + "s / " + total.toFixed(1) + "s");
  }

  function commitSeek(fraction) {
    const total = currentTotal();
    if (!total) return;
    const offset = Math.max(0, Math.min(total - 0.02, fraction * total));
    state.seekOffset = offset;
    const wasPlaying = state.playing;
    if (state.source) {
      try { state.source.onended = null; state.source.stop(); } catch (e) { /* ignore */ }
      state.source = null;
    }
    if (state.rafId) cancelAnimationFrame(state.rafId);
    if (state.audioCtx && wasPlaying) {
      startSourceAt(offset);
    } else {
      updateProgress(offset);
    }
  }

  bar.addEventListener("pointerdown", (e) => {
    if (!state.result) return;
    e.preventDefault();
    state.dragging = true;
    bar.classList.add("dragging");
    try { bar.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    dragFraction = fractionFromEvent(e);
    previewDrag(dragFraction);
  });
  bar.addEventListener("pointermove", (e) => {
    if (!state.dragging) return;
    dragFraction = fractionFromEvent(e);
    previewDrag(dragFraction);
  });
  const endDrag = (e) => {
    if (!state.dragging) return;
    state.dragging = false;
    bar.classList.remove("dragging");
    try { bar.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    commitSeek(dragFraction);
  };
  bar.addEventListener("pointerup", endDrag);
  bar.addEventListener("pointercancel", endDrag);

  bar.addEventListener("keydown", (e) => {
    if (!state.result) return;
    const total = currentTotal();
    if (!total) return;
    let target = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") target = Math.min(total, state.seekOffset + 5);
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") target = Math.max(0, state.seekOffset - 5);
    else if (e.key === "Home") target = 0;
    else if (e.key === "End") target = Math.max(0, total - 0.02);
    if (target < 0) return;
    e.preventDefault();
    commitSeek(target / total);
  });

  // ---------------- 播放 / 暂停 / 停止 ----------------
  $("playBtn").addEventListener("click", async () => {
    const r = state.result;
    if (!r) return;
    if (!state.buffer || !state.audioCtx) {
      setStatus("正在合成试听音频…", "");
      await new Promise((resolve) => setTimeout(resolve, 20));
      try {
        await ensureBuffer();
      } catch (err) {
        setStatus("试听合成失败:" + err.message, "error");
        return;
      }
      setStatus("转换完成 ✔ 可试听或下载。", "ok");
    }
    const ctx = state.audioCtx;
    await ctx.resume();
    startSourceAt(state.seekOffset);
  });

  // 下载试听音频为 WAV
  $("downloadWavBtn").addEventListener("click", async () => {
    const r = state.result;
    if (!r) return;
    const btn = $("downloadWavBtn");
    btn.disabled = true;
    setStatus("正在生成 WAV 文件…", "");
    try {
      await ensureBuffer();
      const blob = encodeWav(state.buffer);
      const song = (r.song_name || "歌曲").replace(/[\\/:*?"<>|]/g, "");
      const name =
        state.listenMode === "recognized"
          ? `${song}_识别原旋律.wav`
          : (r.filename || `${song}_牙刷版.txt`).replace(/\.txt$/i, ".wav");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus(
        `已导出 WAV:${name}(${state.buffer.duration.toFixed(1)}s,44.1kHz / 16bit 单次播放)。`,
        "ok"
      );
    } catch (err) {
      setStatus("导出 WAV 失败:" + err.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  $("pauseBtn").addEventListener("click", async () => {
    if (state.audioCtx && state.playing) {
      await state.audioCtx.suspend();
      state.playing = false;
      cancelAnimationFrame(state.rafId);
      if (state.source) {
        try { state.source.onended = null; state.source.stop(); } catch (e) { /* ignore */ }
        state.source = null;
      }
      $("playBtn").disabled = false;
      $("pauseBtn").disabled = true;
      updateProgress(state.seekOffset);
    }
  });

  $("stopBtn").addEventListener("click", () => {
    stopPlayback();
  });

  // ---------------- 导出 txt ----------------
  $("downloadBtn").addEventListener("click", () => {
    const r = state.result;
    if (!r) return;
    const includeHeader = $("headerToggle").checked;
    const lines = [];
    if (includeHeader) {
      lines.push(`# 歌名: ${r.song_name || "未命名"}`);
      lines.push(`# 总时长: ${r.total_duration.toFixed(3)} s`);
      lines.push(`# 频率范围: ${r.min_freq}-${r.max_freq} Hz`);
      lines.push(`# BPM: ${r.bpm.toFixed(1)}`);
      lines.push(`# 元组数量: ${r.event_count}`);
      if (r.messages.length) lines.push(`# 转换提示: ${r.messages.join("; ")}`);
      lines.push("#");
    }
    r.sequence.forEach(([f, d]) => lines.push(`(${f}, ${d.toFixed(3)})`));
    const blob = new Blob(["\ufeff" + lines.join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = r.filename || "歌曲_牙刷版.txt";
    a.click();
    URL.revokeObjectURL(a.href);
    setStatus("已生成并下载 txt 文件(纯数据模式,UTF-8)。", "ok");
  });

  // ---------------- 重新上传 ----------------
  $("resetBtn").addEventListener("click", () => {
    state.file = null;
    state.result = null;
    state.type = null;
    imageInput.value = "";
    audioInput.value = "";
    imageCard.classList.remove("selected");
    audioCard.classList.remove("selected");
    $("imageChip").hidden = true;
    $("audioChip").hidden = true;
    $("resultSection").hidden = true;
    $("playerSection").hidden = true;
    $("exportSection").hidden = true;
    $("convertBtn").disabled = true;
    stopPlayback();
    setStatus("", "");
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
})();
