#!/usr/bin/env python3
"""
build_esp.py — 构建压缩版 esp 目录到 esp-x

用法：
    python build_esp.py                 # 默认 esp/ -> esp-x/
    python build_esp.py --clean         # 先清空 esp-x/ 再构建
    python build_esp.py -s esp -d esp-x --clean
    python build_esp.py --no-mpy        # 不编译 .py，只压缩 html/js

处理规则：
    - boot.py / main.py     原样复制（MicroPython 启动入口，保持 .py 便于调试）
    - 其他 .py              mpy-cross 编译为同名 .mpy（体积↓、启动快、RAM 少）
    - .html / .htm          htmlmin 压缩（去注释、多余空白）
    - .js                   rjsmin 压缩（去注释、多余空白）
    - 其他文件（.bin .map .idx 等）  原样复制
    - __pycache__           跳过

依赖（建议都装）：
    pip install mpy-cross           # 必需（要 mpy 编译时）
    pip install rjsmin htmlmin      # 可选，未装则用简单 fallback

注意：
    - .mpy 字节码与 MicroPython 版本相关，需用与设备匹配的 mpy-cross
      （设备是 v1.28.0，用 pip install mpy-cross==1.28.0 最稳）
    - boot.py / main.py 保留为 .py，上传后可 REPL 现场查看/修改
    - 生成后，上传 esp-x/ 下所有文件到设备即可
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------- 保持为 .py 的启动入口 ----------
# MicroPython 启动链：boot.py → main.py
# 保留 .py 便于 REPL 现场调试（print(open('main.py').read()) 等）
KEEP_AS_PY = ('boot.py', 'main.py')

# ---------- 可选库 ----------
try:
    import rjsmin
    HAS_RJSMIN = True
except ImportError:
    HAS_RJSMIN = False

try:
    import htmlmin
    HAS_HTMLMIN = True
except ImportError:
    HAS_HTMLMIN = False


# ---------- mpy-cross 探测 ----------
def find_mpy_cross():
    """返回 (mode, exe)。mode='cli' 系统命令，'module' 用 python -m，None 未安装。"""
    if shutil.which('mpy-cross'):
        return ('cli', 'mpy-cross')
    try:
        import mpy_cross  # noqa: F401
        return ('module', sys.executable)
    except ImportError:
        return (None, None)


def compile_mpy(src_path, dst_path, mode, exe):
    if mode == 'cli':
        subprocess.run([exe, '-o', str(dst_path), str(src_path)],
                       check=True, capture_output=True)
    elif mode == 'module':
        subprocess.run([exe, '-m', 'mpy_cross', '-o', str(dst_path), str(src_path)],
                       check=True, capture_output=True)
    else:
        raise RuntimeError("mpy-cross 未安装")


# ---------- minify fallback（简单保守版） ----------
def fallback_js_minify(src):
    # 只合并多余空白，不动注释/字符串，避免误删
    text = re.sub(r'[ \t]+', ' ', src)
    text = re.sub(r'\s*\n\s*', '\n', text)
    return text


def fallback_html_minify(src):
    text = re.sub(r'<!--(?!\[if).*?-->', '', src, flags=re.DOTALL)
    text = re.sub(r'>\s+<', '><', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\s*\n\s*', '\n', text)
    return text


def minify_html(src):
    if HAS_HTMLMIN:
        try:
            return htmlmin.minify(
                src,
                remove_comments=True,
                remove_empty_space=True,
                remove_all_empty_space=False,
                reduce_boolean_attributes=True,
            )
        except Exception as e:
            print(f"  [WARN] htmlmin 失败，用 fallback: {e}")
    return fallback_html_minify(src)


def minify_js(src):
    if HAS_RJSMIN:
        try:
            return rjsmin.jsmin(src)
        except Exception as e:
            print(f"  [WARN] rjsmin 失败，用 fallback: {e}")
    return fallback_js_minify(src)


# ---------- 单文件处理 ----------
def process_file(src, dst, mpy_mode, mpy_exe):
    src = Path(src)
    dst = Path(dst)
    suffix = src.suffix.lower()
    name = src.name

    # boot.py / main.py 保持原样
    if name in KEEP_AS_PY:
        shutil.copy2(src, dst)
        return f'copy ({name})'

    if suffix == '.py':
        if mpy_mode is None:
            # --no-mpy，原样复制
            shutil.copy2(src, dst)
            return 'copy (.py no-mpy)'
        dst_mpy = dst.with_suffix('.mpy')
        try:
            compile_mpy(src, dst_mpy, mpy_mode, mpy_exe)
            orig = src.stat().st_size
            new = dst_mpy.stat().st_size
            return f'py->mpy {orig}->{new} B'
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode('utf-8', 'replace') if e.stderr else str(e)
            print(f"  [WARN] mpy-cross 失败 {src}: {err.strip()}")
            shutil.copy2(src, dst)
            return 'copy (.py mpy-failed)'
        except Exception as e:
            print(f"  [WARN] mpy-cross 失败 {src}: {e}")
            shutil.copy2(src, dst)
            return 'copy (.py mpy-failed)'

    if suffix in ('.html', '.htm'):
        src_text = src.read_text(encoding='utf-8')
        out = minify_html(src_text)
        dst.write_text(out, encoding='utf-8')
        return f'html minify {len(src_text)}->{len(out)} B'

    if suffix == '.js':
        src_text = src.read_text(encoding='utf-8')
        out = minify_js(src_text)
        dst.write_text(out, encoding='utf-8')
        return f'js minify {len(src_text)}->{len(out)} B'

    # 其他原样
    shutil.copy2(src, dst)
    return 'copy'


# ---------- 主流程 ----------
def count_size(path, skip_pycache=True):
    total = 0
    for f in Path(path).rglob('*'):
        if not f.is_file():
            continue
        if skip_pycache and '__pycache__' in f.parts:
            continue
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def main():
    ap = argparse.ArgumentParser(
        description="构建压缩版 esp-x（py→mpy，html/js minify）",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument('-s', '--src', default='esp', help='源目录（默认 esp）')
    ap.add_argument('-d', '--dst', default='esp-x', help='输出目录（默认 esp-x）')
    ap.add_argument('--clean', action='store_true', help='先清空输出目录')
    ap.add_argument('--no-mpy', action='store_true', help='不编译 .py，只 minify html/js')
    args = ap.parse_args()

    src_dir = Path(args.src).resolve()
    dst_dir = Path(args.dst).resolve()

    if not src_dir.is_dir():
        print(f"[ERROR] 源目录不存在: {src_dir}")
        sys.exit(1)

    if args.clean and dst_dir.exists():
        print(f"[INFO] 清空输出目录: {dst_dir}")
        shutil.rmtree(dst_dir)

    dst_dir.mkdir(parents=True, exist_ok=True)

    # 检测 mpy-cross
    mpy_mode = None
    mpy_exe = None
    if not args.no_mpy:
        mpy_mode, mpy_exe = find_mpy_cross()
        if mpy_mode is None:
            print("[ERROR] mpy-cross 未安装")
            print("        pip install mpy-cross")
            print("        或者使用 --no-mpy 只压缩 html/js")
            sys.exit(1)
        print(f"[INFO] mpy-cross 可用（{mpy_mode}）")
    else:
        print("[INFO] --no-mpy 已指定，.py 文件原样复制")

    print(f"[INFO] htmlmin: {'可用' if HAS_HTMLMIN else '不可用（fallback）'}")
    print(f"[INFO] rjsmin:  {'可用' if HAS_RJSMIN else '不可用（fallback）'}")
    print(f"[INFO] 保持 .py: {', '.join(KEEP_AS_PY)}")
    print(f"[INFO] 构建: {src_dir}")
    print(f"[INFO]     -> {dst_dir}")
    print()

    total = 0
    for root, dirs, files in os.walk(src_dir):
        # 跳过 __pycache__
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')

        rel = Path(root).relative_to(src_dir)
        out_dir = dst_dir / rel
        out_dir.mkdir(parents=True, exist_ok=True)

        for name in sorted(files):
            src_file = Path(root) / name
            dst_file = out_dir / name
            try:
                info = process_file(src_file, dst_file, mpy_mode, mpy_exe)
                total += 1
                rel_name = str(rel / name) if str(rel) != '.' else name
                print(f"  {rel_name}  ->  {info}")
            except Exception as e:
                rel_name = str(rel / name) if str(rel) != '.' else name
                print(f"  [ERROR] {rel_name}: {e}")

    print()
    print(f"[INFO] 完成，处理 {total} 个文件")

    src_size = count_size(src_dir)
    dst_size = count_size(dst_dir)
    if src_size > 0:
        print(f"[INFO] 源大小: {src_size / 1024:.1f} KB")
        print(f"[INFO] 压缩后: {dst_size / 1024:.1f} KB "
              f"（{100.0 * dst_size / src_size:.1f}%）")
    print(f"[INFO] 输出目录: {dst_dir}")


if __name__ == '__main__':
    main()