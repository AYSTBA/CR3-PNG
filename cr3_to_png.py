#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch-convert Canon CR3 raw files into 16-bit RGB PNG (or JPEG).

Two rendering modes are provided:

* default  - "finished" image: sRGB primaries, camera white balance and
             libraw's automatic brightening, so the result looks right when
             opened in any viewer or editor.
* --linear - "linear archive": camera (RAW) colour space, gamma 1.0 and
             ``no_auto_bright``, i.e. the untouched tonal range for later
             grading. The picture is dark on purpose.

PNG output is written by pypng as genuine 16-bit RGB (bit depth 16, colour
type 2). Pillow is deliberately not used for PNG, because ``Image.save()``
silently turns 16-bit RGB into 8-bit. JPEG (--format jpg) is inherently
8-bit and is encoded with Pillow.

PNG is stored (zlib level 0) by default: 16-bit camera data barely
compresses (level 6 saves ~2.5%% but costs ~5 s per 24 MP frame). Use
``--png-compression 6`` when a smaller file matters more than speed.

Camera white balance is applied in both modes; that keeps the channels
balanced. If you need the sensor's untouched per-channel data, use rawpy
directly with ``use_camera_wb=False``.

Usage
-----
    python cr3_to_png.py "D:\\照片" -o "D:\\out" --recursive
    python cr3_to_png.py "D:\\照片" -o "D:\\out" -r --format jpg
    python cr3_to_png.py a.CR3 b.CR3 -o out --linear --overwrite
    python cr3_to_png.py "D:\\照片\\2024" -o "D:\\out\\2024" -r

Exit codes: 0 = all files converted (skips are fine), 1 = at least one
file failed, 2 = usage or environment error.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
import time
from pathlib import Path
from typing import Callable, Iterator, List, NamedTuple, Optional, Sequence, Tuple

CR3_SUFFIX = ".cr3"


class Job(NamedTuple):
    """One source file and the PNG path it should produce."""

    src: Path
    dst: Path


# ---------------------------------------------------------------------------
# environment / dependencies
# ---------------------------------------------------------------------------


def _setup_console() -> None:
    """Keep legacy Windows code pages from turning a path into a crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _load_deps():
    """Import the third-party stack and return (numpy, png, rawpy)."""
    try:
        import numpy as np
        import png
        import rawpy
    except Exception as exc:  # ImportError, or a missing libraw DLL (OSError)
        print(
            "错误: 无法导入依赖 "
            f"({exc.__class__.__name__}: {exc})\n"
            "      请先安装: python -m pip install rawpy pypng numpy",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return np, png, rawpy


# ---------------------------------------------------------------------------
# collecting source files
# ---------------------------------------------------------------------------


def _iter_cr3_files(
    root: Path, recursive: bool, warn: Callable[[str], None]
) -> Iterator[Path]:
    """Yield the .cr3 files of ``root`` (optionally walking sub-folders)."""
    if not recursive:
        try:
            with os.scandir(root) as entries:
                listing = sorted(entries, key=lambda entry: entry.name.lower())
        except OSError as exc:
            warn(f"无法读取目录 {root}: {exc}")
            return
        for entry in listing:
            if not entry.name.lower().endswith(CR3_SUFFIX):
                continue
            try:
                if entry.is_file():
                    yield Path(entry.path)
            except OSError as exc:
                warn(f"无法访问 {entry.path}: {exc}")
        return

    def on_error(exc: OSError) -> None:
        warn(f"无法读取目录 {exc.filename}: {exc}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        dirnames.sort(key=str.lower)
        for name in sorted(filenames, key=str.lower):
            if name.lower().endswith(CR3_SUFFIX):
                yield Path(dirpath) / name


def plan_jobs(
    inputs: Sequence[str],
    output_dir: Optional[Path],
    recursive: bool,
    warn: Callable[[str], None],
    suffix: str = ".png",
) -> Tuple[List[Job], List[Tuple[str, str]]]:
    """Map every input file to its output path.

    Relative sub-folders are kept, based on the input directory that was
    given on the command line (explicit files simply keep their file name).
    Returns (jobs, problems) where problems are inputs that cannot be
    converted at all, e.g. a path that does not exist.
    """
    jobs: List[Job] = []
    problems: List[Tuple[str, str]] = []
    seen_src: set[str] = set()
    seen_dst: set[str] = set()

    for raw_input in inputs:
        src = Path(raw_input).expanduser()
        try:
            exists = src.exists()
        except OSError:
            exists = False
        if not exists:
            problems.append((str(raw_input), "输入不存在"))
            continue

        if src.is_dir():
            base = src
            candidates: Iterator[Path] = _iter_cr3_files(src, recursive, warn)
        elif src.is_file():
            base = src.parent
            if src.suffix.lower() != CR3_SUFFIX:
                warn(f"{src} 不是 .cr3 文件，仍会尝试转换")
            candidates = iter([src])
        else:
            problems.append((str(raw_input), "不是普通文件或目录"))
            continue

        base_abs = os.path.abspath(base)
        found_any = False
        for path in candidates:
            found_any = True
            src_key = os.path.normcase(os.path.abspath(path))
            if src_key in seen_src:
                continue
            seen_src.add(src_key)

            rel = Path(os.path.relpath(os.path.abspath(path), base_abs))
            if output_dir is not None:
                dst = output_dir / rel.with_suffix(suffix)
            else:
                dst = path.with_suffix(suffix)

            dst_key = os.path.normcase(os.path.abspath(dst))
            if dst_key == src_key:
                problems.append((str(path), "输出路径与输入相同，已忽略"))
                continue
            if dst_key in seen_dst:
                problems.append((str(path), f"输出路径冲突，已忽略: {dst}"))
                continue
            seen_dst.add(dst_key)
            jobs.append(Job(path, dst))

        if not found_any and src.is_dir():
            warn(f"目录中没有 .cr3 文件: {src}")

    return jobs, problems


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------


def render_rgb(raw, linear: bool, rawpy_module, output_bps: int = 16):
    """Run libraw's post-processing and return an HxWx3 array.

    ``output_bps`` is 16 for PNG and 8 for JPEG, which is both faster and
    avoids a needless 16-bit buffer.
    """
    if linear:
        # Archival: linear light in the camera's own RGB space. Values are
        # still black-level shifted and white-balanced, but nothing is tone
        # mapped, so the preview is dark by design.
        return raw.postprocess(
            output_bps=output_bps,
            output_color=rawpy_module.ColorSpace.raw,
            use_camera_wb=True,
            no_auto_bright=True,
            gamma=(1.0, 1.0),
        )
    # Finished: sRGB, camera white balance, libraw auto brightness.
    return raw.postprocess(
        output_bps=output_bps,
        output_color=rawpy_module.ColorSpace.sRGB,
        use_camera_wb=True,
        no_auto_bright=False,
        gamma=(2.222, 4.5),
    )


def _png_rows(rgb, packed: bool, progress: Optional[Callable[[float], None]]):
    """Yield every scanline, optionally reporting 0..1 progress per row."""
    height = rgb.shape[0]
    for y in range(height):
        row = rgb[y]
        if packed:
            yield row.astype(">u2", copy=False).tobytes()
        else:
            yield row.reshape(-1)
        if progress is not None:
            progress((y + 1) / height)


def _write_scanlines(writer, fileobj, rgb, progress=None) -> None:
    """Feed 16-bit big-endian scanlines to pypng.

    pypng's generic ``Writer.write`` re-packs every value through
    ``struct.pack``; handing already packed big-endian bytes to
    ``write_packed`` avoids that per-pixel Python loop and produces the same
    scanlines (filter type 0). The fallback keeps older pypng releases
    working.
    """
    packed = getattr(writer, "write_packed", None)
    if callable(packed):
        packed(fileobj, _png_rows(rgb, True, progress))
    else:
        writer.write(fileobj, _png_rows(rgb, False, progress))


def _replace_with_retry(tmp_path: Path, path: Path, attempts: int = 6) -> None:
    """os.replace, retrying while Windows holds a lock on the target.

    Antivirus/indexers sometimes keep a freshly written file open for a
    moment, which turns the rename into WinError 5 (PermissionError).
    """
    for attempt in range(1, attempts + 1):
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(0.15 * attempt)


def write_png16(path: Path, rgb, png_module, progress=None, compression: int = 0) -> None:
    """Write ``rgb`` (HxWx3, uint16) as a 16-bit RGB PNG, atomically.

    ``compression`` is the zlib level.  0 (store, the default) is roughly 10x
    faster than 6 on 16-bit camera data, which barely compresses anyway
    (measured: 138 MB of IDAT, 135 MB at level 6, +2.5% size, +5 s).
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"期望 HxWx3 的 RGB 数组，实际形状为 {rgb.shape}")
    height, width = rgb.shape[:2]

    writer = png_module.Writer(
        width=width,
        height=height,
        greyscale=False,
        alpha=False,
        bitdepth=16,
        compression=compression,
    )

    # Write to a temporary file first: an interrupted run must not leave a
    # truncated PNG behind that the next run would happily skip.
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        with open(tmp_path, "wb") as fileobj:
            _write_scanlines(writer, fileobj, rgb, progress)
        _replace_with_retry(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def write_jpeg(path: Path, rgb, quality: int = 95) -> None:
    """Write ``rgb`` (HxWx3, uint8 or uint16) as an 8-bit JPEG, atomically."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "输出 JPEG 需要 Pillow，请先安装: python -m pip install pillow"
        ) from exc

    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"期望 HxWx3 的 RGB 数组，实际形状为 {rgb.shape}")
    if rgb.dtype.itemsize > 1:
        rgb = (rgb >> 8).astype("uint8")
    image = Image.fromarray(rgb, "RGB")

    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.part")
    try:
        image.save(tmp_path, format="JPEG", quality=quality)
        _replace_with_retry(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# reporting helpers
# ---------------------------------------------------------------------------


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}m{rest:04.1f}s"


def _reason(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    # rawpy surfaces libraw messages as a bytes repr ("b'...'").
    if len(text) >= 3 and text[0] == "b" and text[1] in "'\"" and text[-1] == text[1]:
        try:
            text = ast.literal_eval(text).decode("utf-8", "replace")
        except (SyntaxError, ValueError):
            text = text[2:-1]
    if len(text) > 300:
        text = text[:297] + "..."
    return f"{exc.__class__.__name__}: {text}"


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cr3_to_png.py",
        description="批量把 Canon CR3 转成 16-bit PNG 或 8-bit JPG（成品模式或线性归档模式）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            '  python cr3_to_png.py "D:\\照片" -o "D:\\out" --recursive\n'
            '  python cr3_to_png.py "D:\\照片" -o "D:\\out" -r --format jpg\n'
            "  python cr3_to_png.py a.CR3 b.CR3 -o out --linear --overwrite\n"
            '  python cr3_to_png.py "D:\\照片" -o "D:\\out" -r --overwrite\n'
            "\n"
            "退出码: 0 全部成功; 1 有文件失败; 2 参数或环境错误。"
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        metavar="输入",
        help="CR3 文件或目录，可一次给多个",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        metavar="输出目录",
        help="输出目录，保留相对子目录结构；缺省写到每个源文件旁边",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="递归扫描输入目录",
    )
    parser.add_argument(
        "--linear",
        action="store_true",
        help="线性归档模式: RAW 色彩空间、gamma 1.0、关闭自动亮度（画面偏暗是预期）",
    )
    parser.add_argument(
        "--format",
        choices=("png", "jpg"),
        default="png",
        help="输出格式: png = 16-bit RGB PNG（默认）, jpg = 8-bit JPEG",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=95,
        metavar="1-100",
        help="JPEG 质量，默认 95（仅 --format jpg 时有效）",
    )
    parser.add_argument(
        "--png-compression",
        type=int,
        default=0,
        metavar="0-9",
        help="PNG 的 zlib 级别，默认 0（最快，文件约大 3%%）；想要更小文件可用 6",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的输出文件（默认跳过）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)

    np, png, rawpy = _load_deps()

    if not 1 <= args.quality <= 100:
        print("错误: --quality 必须在 1-100 之间", file=sys.stderr)
        return 2
    if not 0 <= args.png_compression <= 9:
        print("错误: --png-compression 必须在 0-9 之间", file=sys.stderr)
        return 2

    output_dir: Optional[Path] = None
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
        if output_dir.exists() and not output_dir.is_dir():
            print(f"错误: 输出路径不是目录: {output_dir}", file=sys.stderr)
            return 2

    def warn(message: str) -> None:
        print(f"警告: {message}", file=sys.stderr)

    suffix = ".png" if args.format == "png" else ".jpg"
    jobs, problems = plan_jobs(args.inputs, output_dir, args.recursive, warn, suffix)
    if not jobs and not problems:
        print("错误: 没有找到 .cr3 文件", file=sys.stderr)
        return 2

    mode = (
        "线性归档 (RAW 色彩空间, gamma 1.0, 无自动亮度; 画面偏暗是预期)"
        if args.linear
        else "成品 (sRGB, 相机白平衡, 自动亮度)"
    )
    print(f"模式: {mode} | 格式: {args.format.upper()}")
    print(f"待处理: {len(jobs)} 张 | 输出: {output_dir if output_dir else '源文件旁'}")
    print()

    failures: List[Tuple[str, str]] = list(problems)
    converted = 0
    skipped = 0
    started = time.perf_counter()
    interrupted = False

    try:
        for index, job in enumerate(jobs, 1):
            label = f"[{index}/{len(jobs)}]"
            if job.dst.is_dir():
                reason = "输出路径是一个已存在的目录"
                failures.append((str(job.src), reason))
                print(f"{label} 失败  {job.src}: {reason}")
                continue
            if job.dst.exists() and not args.overwrite:
                skipped += 1
                print(f"{label} 跳过  {job.src}  (已存在, 用 --overwrite 覆盖)")
                continue

            file_started = time.perf_counter()
            try:
                output_bps = 16 if args.format == "png" else 8
                with rawpy.imread(str(job.src)) as raw:
                    rgb = np.asarray(
                        render_rgb(raw, args.linear, rawpy, output_bps=output_bps)
                    )
                job.dst.parent.mkdir(parents=True, exist_ok=True)
                if args.format == "png":
                    write_png16(job.dst, rgb, png, compression=args.png_compression)
                else:
                    write_jpeg(job.dst, rgb, args.quality)
                converted += 1
                height, width = rgb.shape[:2]
                size_mb = job.dst.stat().st_size / (1024 * 1024)
                del rgb
                print(
                    f"{label} 完成  {job.src} -> {job.dst}  "
                    f"{width}x{height}  {size_mb:.1f}MB  "
                    f"{_format_duration(time.perf_counter() - file_started)}"
                )
            except Exception as exc:
                reason = _reason(exc)
                failures.append((str(job.src), reason))
                print(f"{label} 失败  {job.src}: {reason}")
    except KeyboardInterrupt:
        interrupted = True
        print("\n已中断", file=sys.stderr)

    elapsed = _format_duration(time.perf_counter() - started)
    print()
    print("-" * 64)
    print(f"完成 {converted} 张, 跳过 {skipped} 张, 失败 {len(failures)} 张, 用时 {elapsed}")
    if failures:
        print("失败清单:")
        for src, reason in failures:
            print(f"  - {src}: {reason}")

    if interrupted:
        return 130
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
