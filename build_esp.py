#!/usr/bin/env python3
"""
build_esp.py — 构建压缩版 esp 目录到 esp-x

处理规则：
    - boot.py / main.py     原样复制（MicroPython 启动入口，保持 .py 便于调试）
    - 其他 .py              mpy-cross 编译为同名 .mpy
    - 其他文件（.mpy/.bin/.txt/.idx/.map 等）  原样复制
    - __pycache__           跳过

用法：
    python build_esp.py                 # 默认 esp/ -> esp-x/
    python build_esp.py --clean         # 先清空 esp-x/ 再构建
    python build_esp.py --no-mpy        # 不编译 .py，只复制
    python build_esp.py -s esp -d esp-x --clean

依赖：
    pip install mpy-cross==1.28.0.post2

    mpy-cross 版本必须与设备固件一致（设备是 v1.28.0）

生成后上传：
    mpremote cp -r esp-x/* :

注意：
    设备同时存在 .py 和 .mpy 时优先加载 .mpy。改 .py 后如果行为没变，
    说明设备上加载的是旧的 .mpy，要么删掉 .mpy，要么重新跑这个脚本。
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 保持为 .py 的启动入口
KEEP_AS_PY = ('boot.py', 'main.py')


def find_mpy_cross():
    """返回 (mode, exe)。mode='cli' 用系统命令，'module' 用 python -m，None 未安装。"""
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


def process_file(src, dst, mpy_mode, mpy_exe):
    src = Path(src)
    dst = Path(dst)
    suffix = src.suffix.lower()
    name = src.name

    # boot.py / main.py 保持原样
    if name in KEEP_AS_PY:
        shutil.copy2(src, dst)
        return 'copy (%s)' % name

    if suffix == '.py':
        if mpy_mode is None:
            shutil.copy2(src, dst)
            return 'copy (.py no-mpy)'
        dst_mpy = dst.with_suffix('.mpy')
        try:
            compile_mpy(src, dst_mpy, mpy_mode, mpy_exe)
            orig = src.stat().st_size
            new = dst_mpy.stat().st_size
            return 'py->mpy %d->%d B' % (orig, new)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode('utf-8', 'replace') if e.stderr else str(e)
            print('  [WARN] mpy-cross 失败 %s: %s' % (src, err.strip()))
            shutil.copy2(src, dst)
            return 'copy (.py mpy-failed)'
        except Exception as e:
            print('  [WARN] mpy-cross 失败 %s: %s' % (src, e))
            shutil.copy2(src, dst)
            return 'copy (.py mpy-failed)'

    # 其他原样复制（.mpy .bin .txt .idx .map .json ...）
    shutil.copy2(src, dst)
    return 'copy'


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
        description="构建压缩版 esp-x（py→mpy）",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    ap.add_argument('-s', '--src', default='esp', help='源目录（默认 esp）')
    ap.add_argument('-d', '--dst', default='esp-x', help='输出目录（默认 esp-x）')
    ap.add_argument('--clean', action='store_true', help='先清空输出目录')
    ap.add_argument('--no-mpy', action='store_true', help='不编译 .py，只复制')
    args = ap.parse_args()

    src_dir = Path(args.src).resolve()
    dst_dir = Path(args.dst).resolve()

    if not src_dir.is_dir():
        print('[ERROR] 源目录不存在: %s' % src_dir)
        sys.exit(1)

    if args.clean and dst_dir.exists():
        print('[INFO] 清空输出目录: %s' % dst_dir)
        shutil.rmtree(dst_dir)

    dst_dir.mkdir(parents=True, exist_ok=True)

    mpy_mode = None
    mpy_exe = None
    if not args.no_mpy:
        mpy_mode, mpy_exe = find_mpy_cross()
        if mpy_mode is None:
            print('[ERROR] mpy-cross 未安装')
            print('        pip install mpy-cross==1.28.0.post2')
            print('        或用 --no-mpy 只复制')
            sys.exit(1)
        print('[INFO] mpy-cross 可用（%s）' % mpy_mode)
    else:
        print('[INFO] --no-mpy 已指定，.py 文件原样复制')

    print('[INFO] 保持 .py: %s' % ', '.join(KEEP_AS_PY))
    print('[INFO] 构建: %s' % src_dir)
    print('[INFO]     -> %s' % dst_dir)
    print()

    total = 0
    for root, dirs, files in os.walk(src_dir):
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
                print('  %s  ->  %s' % (rel_name, info))
            except Exception as e:
                rel_name = str(rel / name) if str(rel) != '.' else name
                print('  [ERROR] %s: %s' % (rel_name, e))

    print()
    print('[INFO] 完成，处理 %d 个文件' % total)

    src_size = count_size(src_dir)
    dst_size = count_size(dst_dir)
    if src_size > 0:
        print('[INFO] 源大小: %.1f KB' % (src_size / 1024))
        print('[INFO] 压缩后: %.1f KB（%.1f%%）'
              % (dst_size / 1024, 100.0 * dst_size / src_size))
    print('[INFO] 输出目录: %s' % dst_dir)


if __name__ == '__main__':
    main()