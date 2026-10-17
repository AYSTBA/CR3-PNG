#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drag-and-drop web UI for cr3_to_png.py — PNG or JPEG into Downloads.

    python cr3_web.py

The page uploads one file at a time. The body is streamed to a temporary
file (never held fully in RAM), converted by cr3_to_png.py and written
atomically into <Downloads>/CR3输出. Progress is streamed back as NDJSON
and shown as a per-file progress bar.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

try:
    import cr3_to_png as conv
except ImportError:
    sys.exit(
        "错误: 找不到 cr3_to_png.py。\n"
        "      请把 cr3_web.py 和 cr3_to_png.py 放在同一个文件夹里再运行。"
    )

MAX_UPLOAD = 512 * 1024 * 1024  # 512 MB, a CR3 is ~30 MB
UPLOAD_CHUNK = 1024 * 1024
OUTPUT_SUBDIR = "CR3输出"
JPEG_QUALITY = 95
CONVERT_LOCK = threading.Lock()  # libraw/postprocess is serialised on purpose

DOWNLOAD_DIR: Optional[Path] = None
np = png = rawpy = None


def default_downloads() -> Path:
    home = Path.home()
    for name in ("Downloads", "下载"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    target = home / "Downloads"
    target.mkdir(parents=True, exist_ok=True)
    return target


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_stem(original: str) -> str:
    base = os.path.basename(original.replace("\\", "/"))
    stem = os.path.splitext(base)[0]
    stem = _INVALID_CHARS.sub("_", stem).strip(" .")
    return (stem or "image")[:120]


def _target_path(original: str, ext: str, overwrite: bool) -> Tuple[Path, bool]:
    stem = _safe_stem(original)
    target = DOWNLOAD_DIR / f"{stem}{ext}"
    existed = target.exists()
    if overwrite:
        return target, existed
    index = 2
    while target.exists():
        target = DOWNLOAD_DIR / f"{stem} ({index}){ext}"
        index += 1
    return target, False


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CR3 → PNG / JPG</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; padding: 34px 16px 48px; display: grid; place-items: start center;
         color: #1f2430; background: radial-gradient(1100px 520px at 50% -10%, #eef2ff, #f7f8fb 60%);
         font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
  main { width: min(680px, 100%); }
  h1 { margin: 0 0 6px; font-size: 22px; letter-spacing: .2px; }
  .sub { margin: 0 0 20px; color: #5b6472; font-size: 14px; }
  .sub code { padding: 2px 6px; border-radius: 6px; background: #eceef5; font-size: 12.5px; word-break: break-all; }
  .drop { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 38px 20px;
          border: 2px dashed #c3cad9; border-radius: 16px; background: #fff; cursor: pointer; text-align: center;
          transition: border-color .15s, background .15s, transform .15s; }
  .drop:hover { border-color: #8e9ab5; }
  .drop.over { border-color: #4c6ef5; background: #f3f6ff; transform: scale(.995); }
  .drop svg { width: 38px; height: 38px; stroke: #4c6ef5; fill: none; stroke-width: 1.6; }
  .drop b { font-size: 15px; font-weight: 600; }
  .drop small { color: #7a8294; font-size: 12.5px; }
  .settings { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-top: 14px; }
  .segment { display: inline-flex; gap: 2px; padding: 3px; border-radius: 10px; background: #eceef5; }
  .segment button { border: 0; background: transparent; padding: 6px 10px; border-radius: 7px; color: #5b6472;
                    font: inherit; font-size: 12.5px; cursor: pointer; }
  .segment button.on { background: #fff; color: #2a3140; font-weight: 600; box-shadow: 0 1px 2px rgba(20, 30, 60, .1); }
  .check { display: flex; align-items: center; gap: 6px; color: #5b6472; font-size: 13px; user-select: none; }
  .check input { width: 15px; height: 15px; accent-color: #4c6ef5; }
  #list { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }
  .row { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; font-size: 13px;
         border: 1px solid #e5e8f0; border-radius: 10px; background: #fff; animation: in .16s ease-out; }
  @keyframes in { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
  .dot { width: 9px; height: 9px; margin-top: 5px; border-radius: 50%; background: #c3cad9; flex: none; }
  .busy .dot { background: #4c6ef5; animation: pulse 1s infinite; }
  .ok .dot { background: #22a06b; }
  .err .dot { background: #e5484d; }
  @keyframes pulse { 50% { opacity: .3; } }
  .body { flex: 1; min-width: 0; }
  .line { display: flex; align-items: center; gap: 10px; }
  .name { font-weight: 500; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .status { margin-left: auto; color: #77809a; font-size: 12px; flex: none; }
  .ok .status { color: #1f7a52; }
  .err .status { color: #c0353a; }
  .err .name { color: #c0353a; }
  .bar { height: 4px; margin-top: 7px; border-radius: 2px; background: #edf0f6; overflow: hidden; }
  .bar i { display: block; width: 0; height: 100%; border-radius: 2px;
           background: linear-gradient(90deg, #4c6ef5, #7b8ff7); transition: width .25s ease; }
  .busy .bar i { background-image: linear-gradient(90deg, #4c6ef5, #9db0ff, #4c6ef5);
                 background-size: 200% 100%; animation: flow 1.1s linear infinite; }
  @keyframes flow { from { background-position: 0 0; } to { background-position: -200% 0; } }
  .err .bar i { background: #e5484d; }
  footer { display: flex; gap: 12px; align-items: center; min-height: 32px; margin-top: 18px; }
  footer span { color: #7a8294; font-size: 12.5px; }
  button { padding: 7px 12px; border: 1px solid #d4d9e6; border-radius: 8px; background: #fff;
           color: #2a3140; font: inherit; font-size: 13px; cursor: pointer; }
  button:hover { background: #f4f6fb; }
  .hint { margin-top: 26px; color: #9aa1b2; font-size: 12px; text-align: center; }
</style>
</head>
<body>
<main>
  <h1>CR3 → PNG / JPG</h1>
  <p class="sub">拖入 .CR3，自动保存到 <code>__DOWNLOADS__</code></p>

  <label class="drop" id="drop">
    <input id="picker" type="file" accept=".cr3,.CR3" multiple hidden>
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" stroke-linecap="round"/>
    </svg>
    <b>把 CR3 拖到这里</b>
    <small>或点击选择文件，可一次多个 · 一张一张转换</small>
  </label>

  <div class="settings">
    <div class="segment" id="formats">
      <button type="button" data-format="png" class="on">PNG · 16-bit</button>
      <button type="button" data-format="jpg">JPG · 8-bit</button>
    </div>
    <label class="check"><input type="checkbox" id="linear"> 线性归档</label>
    <label class="check"><input type="checkbox" id="overwrite"> 同名覆盖</label>
  </div>

  <div id="list"></div>

  <footer>
    <button id="open" hidden>打开下载文件夹</button>
    <span id="summary"></span>
  </footer>

  <p class="hint">转换全部在本机完成 · 默认覆盖同名文件，取消勾选则自动加 (2)、(3)</p>
</main>

<script>
const drop = document.getElementById('drop');
const picker = document.getElementById('picker');
const list = document.getElementById('list');
const summary = document.getElementById('summary');
const openBtn = document.getElementById('open');
const linearBox = document.getElementById('linear');
const overwriteBox = document.getElementById('overwrite');
const formats = document.getElementById('formats');

let format = 'png';
let chain = Promise.resolve();
let okCount = 0;
let badCount = 0;
const timers = new WeakMap();

function storeGet(key, fallback) {
  try {
    const value = localStorage.getItem('cr3web.' + key);
    return value === null ? fallback : value;
  } catch (error) {
    return fallback;
  }
}

function storeSet(key, value) {
  try { localStorage.setItem('cr3web.' + key, value); } catch (error) { /* ignore */ }
}

function setFormat(next) {
  format = next === 'jpg' ? 'jpg' : 'png';
  [...formats.querySelectorAll('button')].forEach((button) => {
    button.classList.toggle('on', button.dataset.format === format);
  });
  storeSet('format', format);
}

formats.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-format]');
  if (button) setFormat(button.dataset.format);
});
linearBox.addEventListener('change', () => storeSet('linear', linearBox.checked ? '1' : '0'));
overwriteBox.addEventListener('change', () => storeSet('overwrite', overwriteBox.checked ? '1' : '0'));

setFormat(storeGet('format', 'png'));
linearBox.checked = storeGet('linear', '0') === '1';
overwriteBox.checked = storeGet('overwrite', '1') === '1';

['dragenter', 'dragover'].forEach((type) => drop.addEventListener(type, (event) => {
  event.preventDefault();
  drop.classList.add('over');
}));
['dragleave', 'dragend', 'drop'].forEach((type) => drop.addEventListener(type, (event) => {
  event.preventDefault();
  drop.classList.remove('over');
}));
drop.addEventListener('drop', (event) => addFiles([...event.dataTransfer.files]));
picker.addEventListener('change', () => {
  addFiles([...picker.files]);
  picker.value = '';
});
openBtn.addEventListener('click', () => {
  fetch('/open', { method: 'POST' }).catch(() => {});
});

function addFiles(files) {
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith('.cr3')) {
      const row = makeRow(file.name, '跳过：不是 .cr3');
      fail(row, '不是 .cr3，已跳过');
      continue;
    }
    const row = makeRow(file.name, '排队中…');
    chain = chain.then(() => convertOne(file, row));
  }
  updateSummary();
}

function makeRow(name, status) {
  const row = document.createElement('div');
  row.className = 'row busy';
  const dot = document.createElement('span');
  dot.className = 'dot';
  const body = document.createElement('div');
  body.className = 'body';
  const line = document.createElement('div');
  line.className = 'line';
  const nameEl = document.createElement('span');
  nameEl.className = 'name';
  nameEl.textContent = name;
  const statusEl = document.createElement('span');
  statusEl.className = 'status';
  statusEl.textContent = status;
  line.append(nameEl, statusEl);
  const bar = document.createElement('div');
  bar.className = 'bar';
  bar.append(document.createElement('i'));
  body.append(line, bar);
  row.append(dot, body);
  list.append(row);
  return row;
}

function setProgress(row, pct, text) {
  row.querySelector('.bar i').style.width = Math.max(0, Math.min(100, pct)) + '%';
  if (text !== undefined) row.querySelector('.status').textContent = text;
}

function startTrickle(row, from, to) {
  stopTrickle(row);
  let pct = from;
  const timer = setInterval(() => {
    pct = Math.min(to, pct + 0.7);
    setProgress(row, pct, '解码 / 去马赛克… · ' + Math.round(pct) + '%');
  }, 300);
  timers.set(row, timer);
}

function stopTrickle(row) {
  const timer = timers.get(row);
  if (timer) {
    clearInterval(timer);
    timers.delete(row);
  }
}

function convertOne(file, row) {
  return new Promise((resolve) => {
    const params = new URLSearchParams({
      name: file.name,
      format: format,
      linear: linearBox.checked ? '1' : '0',
      overwrite: overwriteBox.checked ? '1' : '0',
    });
    const xhr = new XMLHttpRequest();
    const parser = { offset: 0, final: false };
    xhr.open('POST', '/convert?' + params.toString());

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const ratio = event.total ? event.loaded / event.total : 0;
      setProgress(row, 3 + ratio * 27, '上传 ' + Math.round(ratio * 100) + '%');
    };
    xhr.onprogress = () => consume(xhr.responseText, parser, row);
    xhr.onload = () => {
      consume(xhr.responseText, parser, row);
      if (xhr.status >= 400) {
        fail(row, errorFromBody(xhr.responseText) || ('HTTP ' + xhr.status));
      } else if (!parser.final) {
        fail(row, '服务端没有返回结果');
      }
      resolve();
    };
    xhr.onerror = () => {
      fail(row, '网络错误，请确认服务还在运行');
      resolve();
    };
    xhr.send(file);
  });
}

function consume(text, parser, row) {
  let index;
  while ((index = text.indexOf('\\n', parser.offset)) !== -1) {
    const line = text.slice(parser.offset, index).trim();
    parser.offset = index + 1;
    if (!line) continue;
    let event;
    try {
      event = JSON.parse(line);
    } catch (error) {
      continue;
    }
    applyEvent(event, parser, row);
  }
}

function applyEvent(event, parser, row) {
  if (event.ok === true) {
    parser.final = true;
    finishOk(row, event);
    return;
  }
  if (event.ok === false) {
    parser.final = true;
    fail(row, event.error || '转换失败');
    return;
  }
  stopTrickle(row);
  setProgress(row, event.pct || 0, (event.stage || '转换中…') + ' · ' + Math.round(event.pct || 0) + '%');
  if ((event.stage || '').indexOf('解码') === 0) {
    startTrickle(row, event.pct || 30, 62);
  }
}

function finishOk(row, data) {
  stopTrickle(row);
  row.classList.remove('busy');
  row.classList.add('ok');
  row.querySelector('.name').textContent = data.name;
  setProgress(row, 100);
  row.querySelector('.status').textContent =
    (data.overwritten ? '已覆盖 · ' : '') + data.width + '×' + data.height + ' · ' + mb(data.bytes);
  okCount += 1;
  row.scrollIntoView({ block: 'nearest' });
  updateSummary();
}

function fail(row, message) {
  stopTrickle(row);
  row.classList.remove('busy');
  row.classList.add('err');
  row.querySelector('.status').textContent = message;
  badCount += 1;
  updateSummary();
}

function mb(bytes) {
  return (bytes / 1048576).toFixed(1) + ' MB';
}

function updateSummary() {
  const parts = [];
  if (okCount) parts.push('已保存 ' + okCount + ' 张');
  if (badCount) parts.push('失败/跳过 ' + badCount + ' 张');
  summary.textContent = parts.join(' · ');
  openBtn.hidden = okCount === 0;
}

function errorFromBody(text) {
  try {
    const data = JSON.parse(text);
    return data && data.error ? data.error : '';
  } catch (error) {
    return text ? text.slice(0, 200) : '';
  }
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CR3Web/2.0"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if urlparse(self.path).path in ("/", "/index.html"):
            self._send_html(PAGE.replace("__DOWNLOADS__", html.escape(str(DOWNLOAD_DIR))))
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/convert":
            self._handle_convert(parse_qs(parsed.query))
        elif parsed.path == "/open":
            self._handle_open()
        else:
            self.close_connection = True
            self._send_json(404, {"ok": False, "error": "not found"})

    # ------------------------------------------------------------------ API

    def _handle_convert(self, query) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self.close_connection = True
            self._send_json(411, {"ok": False, "error": "缺少 Content-Length"})
            return
        if length > MAX_UPLOAD:
            self.close_connection = True
            self._send_json(413, {"ok": False, "error": "文件太大（上限 512MB）"})
            return

        fmt = (query.get("format") or ["png"])[0].lower()
        if fmt not in ("png", "jpg"):
            self.close_connection = True
            self._send_json(400, {"ok": False, "error": f"未知格式: {fmt}"})
            return
        linear = (query.get("linear") or ["0"])[0].lower() in ("1", "true", "yes")
        overwrite = (query.get("overwrite") or ["0"])[0].lower() in ("1", "true", "yes")
        original = (query.get("name") or ["image.CR3"])[0]

        try:
            temp_path = self._spool_body(length)
        except Exception as exc:
            self.close_connection = True
            self._send_json(400, {"ok": False, "error": conv._reason(exc)})
            return

        streamed = False
        try:
            self._stream_start()
            streamed = True
            self._convert(temp_path, original, fmt, linear, overwrite)
        except Exception as exc:  # one bad file must not kill the server
            reason = conv._reason(exc)
            print(f"[fail] {original}: {reason}")
            if streamed:
                try:
                    self._stream_send({"ok": False, "error": reason})
                    self._stream_end()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    self.close_connection = True
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass

    def _convert(self, temp_path: Path, original: str, fmt: str, linear: bool, overwrite: bool) -> None:
        started = time.perf_counter()
        output_bps = 16 if fmt == "png" else 8

        self._stream_send({"pct": 30, "stage": "解码 / 去马赛克…"})
        with CONVERT_LOCK:
            with rawpy.imread(str(temp_path)) as raw:
                rgb = np.asarray(
                    conv.render_rgb(raw, linear, rawpy, output_bps=output_bps)
                )
            height, width = rgb.shape[:2]
            target, existed = _target_path(original, "." + fmt, overwrite)

            if fmt == "png":
                self._stream_send({"pct": 66, "stage": "编码 PNG…"})
                last = {"pct": 64}

                def progress(fraction: float) -> None:
                    pct = 66 + int(fraction * 32)
                    if pct - last["pct"] >= 2:
                        last["pct"] = pct
                        self._stream_send({"pct": pct, "stage": "编码 PNG…"})

                conv.write_png16(target, rgb, png, progress=progress, compression=0)
            else:
                self._stream_send({"pct": 82, "stage": "编码 JPG…"})
                conv.write_jpeg(target, rgb, quality=JPEG_QUALITY)
            size = target.stat().st_size
            del rgb

        print(
            f"[ok] {original} -> {target.name}  {width}x{height}  "
            f"{size / 1048576:.1f}MB  {time.perf_counter() - started:.1f}s"
        )
        self._stream_send(
            {
                "ok": True,
                "pct": 100,
                "stage": "完成",
                "name": target.name,
                "width": width,
                "height": height,
                "bytes": size,
                "overwritten": existed,
                "format": fmt,
            }
        )
        self._stream_end()

    def _handle_open(self) -> None:
        try:
            if os.name == "nt":
                os.startfile(str(DOWNLOAD_DIR))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(DOWNLOAD_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(DOWNLOAD_DIR)])
            self._send_json(200, {"ok": True})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": conv._reason(exc)})

    # -------------------------------------------------------------- helpers

    def _spool_body(self, length: int) -> Path:
        """Stream the request body to a temp file instead of RAM."""
        fd, name = tempfile.mkstemp(prefix="cr3web_", suffix=".cr3")
        path = Path(name)
        try:
            remaining = length
            with os.fdopen(fd, "wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                    if not chunk:
                        raise OSError("上传中断")
                    handle.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return path

    def _stream_start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _stream_send(self, payload) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.wfile.write(b"%x\r\n" % len(data))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _stream_end(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send_html(self, text: str) -> None:
        self._send(200, "text/html; charset=utf-8", text.encode("utf-8"))

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main(argv=None) -> int:
    global DOWNLOAD_DIR, np, png, rawpy

    parser = argparse.ArgumentParser(
        prog="cr3_web.py",
        description="拖拽式 CR3 转换网页：PNG / JPG 直接写入下载文件夹里的 CR3输出 子目录。",
    )
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=None,
        help="基准输出目录，默认系统下载文件夹；会在里面创建 CR3输出 子目录",
    )
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    np, png, rawpy = conv._load_deps()

    base_dir = (args.download_dir or default_downloads()).expanduser()
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        DOWNLOAD_DIR = base_dir / OUTPUT_SUBDIR
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"错误: 无法创建输出目录 {base_dir}: {exc}", file=sys.stderr)
        return 1

    httpd = None
    for port in range(args.port, args.port + 20):
        try:
            httpd = QuietThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        print(f"错误: 端口 {args.port}-{args.port + 19} 都被占用", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"CR3 → PNG/JPG 已启动: {url}")
    print(f"输出目录: {DOWNLOAD_DIR}")
    print("把 .CR3 拖进页面即可，按 Ctrl+C 停止服务")

    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
