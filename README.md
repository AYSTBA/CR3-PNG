# CR3-PNG

把 Canon CR3 批量/拖拽转换为 **16-bit PNG** 或 **8-bit JPG** 的小工具。提供命令行和本地拖拽网页两条入口，面向 Windows，依赖少、安装即用。

仓库：<https://github.com/AYSTBA/CR3-PNG>

---

## 一、项目简介

### 名称

- 项目：**CR3-PNG**（与仓库名一致）
- 两个脚本：`cr3_to_png.py`（转换核心 + 命令行）、`cr3_web.py`（本地拖拽网页）

### 定位与解决的痛点

- 相机拍完 CR3，想转成 16-bit PNG，通常要开 Lightroom / Photoshop 批量导出，重且慢；
- 用 Pillow 存 16-bit RGB PNG 会被悄悄压成 8-bit（`Image.save()` 的限制），本项目用 **pypng** 写真正的 16-bit RGB；
- 只想“拖一下就好”：网页把文件转好直接放进下载文件夹，没有上传云端、没有“另存为”弹窗；
- 需要归档时保留线性 RAW 数据，需要分享时直接出 JPG。

### 功能一览

| 能力 | 说明 |
|---|---|
| 批量 | 多个文件 / 目录，`--recursive` 递归，保留相对子目录结构 |
| 双格式 | 16-bit RGB PNG（默认）/ 8-bit JPG（质量 95） |
| 双模式 | 成品（sRGB + 相机白平衡 + 自动亮度）/ 线性归档（RAW 色彩空间、gamma 1.0、不自动提亮） |
| 网页 | 拖拽、逐张进度条、同名覆盖开关、自动创建 `CR3输出` 子文件夹 |
| 稳健 | 单张失败不中断并汇总；原子写入；Windows 文件占用自动重试 |
| 速度 | 24MP 单张约 **1.5s** 出图（PNG / JPG），见「性能」 |

### 版本历史

| 版本 | 内容 |
|---|---|
| **1.2.0**（当前） | PNG/JPG 格式切换；同名覆盖（网页默认开）；流式进度条；上传落临时文件；PNG 默认 store 提速约 4 倍；JPEG 走 8-bit 管线；覆盖失败重试 |
| 1.1.0 | 新增 `cr3_web.py` 本地拖拽网页，输出到下载文件夹的 `CR3输出` 子目录 |
| 1.0.0 | 单文件 `cr3_to_png.py`：批量 CR3 → 16-bit PNG，支持递归、跳过/覆盖、失败汇总 |

---

## 二、快速开始

### 环境要求

- Windows 10 / 11（主要测试环境）；Linux / macOS 可运行命令行，网页的“打开下载文件夹”会调用 `open` / `xdg-open`
- Python **3.9+**（实测 3.11.9）
- 依赖库：

| 包 | 用途 |
|---|---|
| `rawpy` | 调用 LibRaw 解码 CR3 |
| `numpy` | 像素数组 |
| `pypng` | 写 16-bit RGB PNG |
| `Pillow` | 仅输出 JPG 时需要（PNG 不使用 Pillow） |

### 安装

```powershell
python -m pip install rawpy pypng numpy pillow
```

### 启动

网页（日常推荐）：

```powershell
cd CR3-PNG
python cr3_web.py
```

启动后自动打开 <http://127.0.0.1:8765>，把 `.CR3` 拖进页面即可。

命令行：

```powershell
python cr3_to_png.py "D:\照片" -o "D:\out" --recursive
```

---

## 三、使用指南

### 网页模式

1. 运行 `python cr3_web.py`（会自动打开浏览器）；
2. 拖入一个或多个 `.CR3`，也可以点击选择文件；
3. 按需切换 `PNG · 16-bit` / `JPG · 8-bit`，勾选“线性归档”“同名覆盖”；
4. 每张照片下方有进度条（上传 → 解码 → 编码），完成后显示尺寸与体积；
5. 成品保存在 `C:\Users\<用户名>\Downloads\CR3输出\`，点“打开下载文件夹”直达。

可选参数：

```powershell
python cr3_web.py --port 8888                    # 换端口（默认 8765，被占用会自动往后找）
python cr3_web.py --download-dir "D:\CR3输出"     # 换输出基准目录
python cr3_web.py --no-browser                   # 启动时不自动开浏览器
```

### 命令行模式

```powershell
# 整个目录（含子目录），保留相对结构
python cr3_to_png.py "D:\照片" -o "D:\out" --recursive

# 直接出 JPG
python cr3_to_png.py "D:\照片" -o "D:\out" -r --format jpg

# 线性归档（画面偏暗是正常现象）
python cr3_to_png.py "D:\照片" -o "D:\out" -r --linear

# 覆盖已存在文件（默认跳过）
python cr3_to_png.py "D:\照片" -o "D:\out" -r --overwrite

# 想要更小的 PNG（默认不压缩换取速度）
python cr3_to_png.py "D:\照片" -o "D:\out" -r --png-compression 6
```

| 参数 | 说明 |
|---|---|
| `输入 ...` | CR3 文件或目录，可多个（必填） |
| `-o, --output-dir` | 输出目录，保留相对子目录；缺省写到源文件旁边 |
| `-r, --recursive` | 递归扫描输入目录 |
| `--format {png,jpg}` | 输出格式，默认 `png` |
| `--quality 1-100` | JPEG 质量，默认 95 |
| `--png-compression 0-9` | PNG 的 zlib 级别，默认 0（最快）；6 可换约 2.5% 体积 |
| `--linear` | 线性归档模式 |
| `--overwrite` | 覆盖已存在输出（默认跳过） |

退出码：`0` 全部成功 · `1` 有文件失败 · `2` 参数/环境错误。

### 在代码里调用（库用法）

```python
from pathlib import Path
import png, rawpy
from cr3_to_png import render_rgb, write_png16

with rawpy.imread("a.CR3") as raw:
    rgb = render_rgb(raw, linear=False, rawpy_module=rawpy)   # uint16 HxWx3，sRGB
write_png16(Path("a.png"), rgb, png)                          # 真正的 16-bit RGB PNG
```

输出 JPG：

```python
from cr3_to_png import write_jpeg
write_jpeg(Path("a.jpg"), rgb, quality=95)
```

---

## 四、架构与原理

### 目录结构

```text
CR3-PNG/
├─ cr3_to_png.py   # 转换核心 + 命令行入口
├─ cr3_web.py      # 本地拖拽网页（http.server，无第三方 Web 框架）
├─ README.md
├─ LICENSE
└─ .gitignore
```

### 转换流程

1. **读取**：网页端请求体按 1 MB 分块流式写入系统临时文件，rawpy 直接读文件，转完即删；命令行直接读源文件。
2. **解码**（rawpy → LibRaw `postprocess`）：
   - 成品：`output_color=sRGB`、`use_camera_wb=True`、`no_auto_bright=False`、`gamma=(2.222, 4.5)`；
   - 线性：`output_color=RAW`、`gamma=(1.0, 1.0)`、`no_auto_bright=True`，仍然应用相机白平衡。
3. **输出**：
   - PNG：uint16 数组 → `png.Writer(bitdepth=16, greyscale=False, alpha=False)`，色彩类型 2（RGB）；
   - JPG：uint8 数组 → Pillow，质量 95。
4. **落盘**：先写同目录 `.xxx.part`，再 `os.replace` 原子替换；Windows 下若目标被杀软/索引短暂占用（`WinError 5`），自动重试。

### 网页与进度条

- 服务端用 chunked NDJSON 回传事件，例如 `{"pct": 30, "stage": "解码 / 去马赛克…"}`，最终 `{"ok": true, ...}`；
- PNG 编码阶段按扫描行回调进度（66% → 98%）；
- 前端用 XHR 增量读取 `responseText` 并按行解析，上传阶段使用 `upload.onprogress`；拖入多张时队列串行，上一张结束才发下一张；
- 解码阶段 LibRaw 不提供回调，进度条按客户端估算平滑推进，不会卡死。

### 内存与并发

- 24MP 一张的 16-bit RGB 数组约 **145 MB**（4020 × 6024 × 3 × 2）；
- 服务端用锁保证**同一时刻只转换一张**，前端也串行，因此不会出现多张同时占用大内存；
- 上传文件落磁盘临时文件，不整包进内存；单文件上限 512 MB，只约束磁盘占用。

### 性能（本机实测，24MP EOS M50 CR3，6024×4020）

| 输出 | 耗时 | 文件大小 |
|---|---|---|
| PNG 16-bit（默认，zlib 0 存储） | **~1.5–1.7 s** | 138.6 MiB |
| PNG 16-bit（`--png-compression 6`） | ~6.9 s | 135.1 MiB |
| JPG 8-bit（quality 95） | **~1.3–1.5 s** | 3.6 MiB |

16-bit 相机数据几乎压不动：level 6 只省约 2.5% 体积，却要多花 4–5 秒，因此默认选择速度优先的存储模式。需要更小文件时用 `--png-compression 6`。

---

## 五、帮助与支持

### FAQ

**Q：为什么 `--linear` 出来的图很暗？**
A：这是预期行为。线性归档不做 gamma、不做自动提亮，保留的是最大动态范围，供后期调色使用。

**Q：为什么 PNG 文件这么大？**
A：16-bit RGB 的 24MP 图本身就有 138 MiB 像素数据；16-bit 数据压缩收益极低，为了速度默认不压缩。想要小一点用 `--png-compression 6`，或用 JPG。

**Q：为什么不用 Pillow 存 PNG？**
A：`Image.save()` 会把 16-bit RGB 静默转成 8-bit；本项目 PNG 一律走 pypng。

**Q：覆盖时偶尔报 `WinError 5` / 拒绝访问？**
A：通常是杀毒软件或索引服务短暂占用刚写完的文件，程序会自动重试；仍失败时关闭正在查看该图片的程序再试。

**Q：网页打不开 / 端口被占用？**
A：换端口 `python cr3_web.py --port 8888`；程序默认会从 8765 往后找 20 个可用端口。

**Q：网页里的下载路径不对？**
A：默认是系统下载文件夹；可用 `--download-dir` 指定基准目录，程序会在其中创建 `CR3输出`。

**Q：中文、空格路径支持吗？**
A：支持。已用 `D:\照片\样张 一.CR3` 这类路径实测。

**Q：进度条停在“解码 / 去马赛克…”一会儿不动？**
A：LibRaw 解码是单次阻塞调用，没有内部回调；进度条按估算值平滑推进，解码完成后会跳到真实编码进度。

**Q：支持 CR2 / NEF / DNG 吗？**
A：命令行对显式给出的文件会交给 LibRaw 尝试解码；目录扫描默认只认 `.cr3`。格式支持范围取决于安装的 LibRaw 版本。

### 反馈渠道

- Bug / 功能建议：<https://github.com/AYSTBA/CR3-PNG/issues>
- 提交补丁：欢迎 Pull Request。请说明使用场景、系统与 Python 版本，并附最小复现步骤（示例命令或拖入的文件类型）。

---

## 六、版权与许可

- 本项目代码采用 **MIT License**，见 [LICENSE](LICENSE)。
- 依赖许可：`rawpy`（MIT）、`LibRaw`（LGPL-2.1 / CDDL，随 rawpy 提供）、`pypng`（MIT）、`Pillow`（HPND）。
- 说明：RAW 解码结果依赖 LibRaw 对相机型号的支持；转换结果请自行核对。网页默认开启同名覆盖，覆盖的是 `CR3输出` 目录内由本工具生成的文件，原始 CR3 不会被修改。
