# coding: utf-8
"""
一键打包脚本 - QPT 打包 + Inno Setup 安装包生成

用法:
    python build.py                     # 默认 lite 模式
    python build.py --lite              # 轻量模式 (排除 torch/paddle)
    python build.py --cuda 11.8         # CUDA 11.8 完整版
    python build.py --cuda 12.6         # CUDA 12.6 完整版
    python build.py --directml          # DirectML 版
    python build.py --skip-qpt          # 跳过 QPT 打包，只编译安装包
    python build.py --skip-installer    # 跳过安装包，只做 QPT 打包
"""

import argparse
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "backend", "config.py")
SETUP_ISS_PATH = os.path.join(SCRIPT_DIR, "installer", "setup.iss")
MAKEDIST_PATH = os.path.join(SCRIPT_DIR, "backend", "tools", "makedist.py")
ISCC_PATHS = [
    os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    os.path.join(os.environ.get("ProgramFiles", ""), "Inno Setup 6", "ISCC.exe"),
]


def read_version() -> str:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        m = re.search(r'VERSION\s*=\s*"([^"]+)"', f.read())
    if not m:
        sys.exit("[ERROR] 无法从 backend/config.py 读取 VERSION")
    return m.group(1)


def sync_iss_version(version: str, release_dir: str):
    with open(SETUP_ISS_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    content = re.sub(
        r'(#define MyAppVersion )"[^"]+"',
        lambda m: f'{m.group(1)}"{version}"',
        content,
    )
    content = re.sub(
        r'(#define QPTReleaseDir )"[^"]+"',
        lambda m: f'{m.group(1)}"{release_dir}"',
        content,
    )

    with open(SETUP_ISS_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def find_iscc() -> str:
    for p in ISCC_PATHS:
        if os.path.isfile(p):
            return p
    sys.exit(
        "[ERROR] 找不到 ISCC.exe，请安装 Inno Setup 6:\n"
        "  https://jrsoftware.org/isdl.php"
    )


def run(cmd: list[str], label: str):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}\n")
    t0 = time.time()
    ret = subprocess.run(cmd)
    elapsed = time.time() - t0
    if ret.returncode != 0:
        sys.exit(f"[ERROR] {label} 失败 (exit code {ret.returncode})")
    m, s = divmod(int(elapsed), 60)
    print(f"\n[OK] {label} 完成 ({m}分{s}秒)")


def main():
    parser = argparse.ArgumentParser(description="一键打包: QPT + Inno Setup")
    parser.add_argument("--cuda", nargs="?", const="11.8", default=None)
    parser.add_argument("--directml", action="store_true")
    parser.add_argument("--lite", action="store_true", default=True)
    parser.add_argument("--no-lite", action="store_true", help="完整打包 (需配合 --cuda 或 --directml)")
    parser.add_argument("--skip-qpt", action="store_true", help="跳过 QPT 打包")
    parser.add_argument("--skip-installer", action="store_true", help="跳过安装包编译")
    args = parser.parse_args()

    if args.no_lite or args.cuda or args.directml:
        args.lite = False

    version = read_version()
    is_lite = args.lite
    suffix = "lite" if is_lite else "full"
    out_parent = os.path.join(os.path.dirname(SCRIPT_DIR), f"vsr_out_{suffix}")
    release_dir = os.path.join(out_parent, "Release")

    print(f"[INFO] 版本: {version}")
    print(f"[INFO] 模式: {'Lite (轻量)' if is_lite else 'Full (完整)'}")
    print(f"[INFO] 输出: {release_dir}")

    # ── 步骤 1: QPT 打包 ──
    if not args.skip_qpt:
        qpt_cmd = [sys.executable, MAKEDIST_PATH]
        if is_lite:
            qpt_cmd.append("--lite")
        if args.cuda:
            qpt_cmd.extend(["--cuda", args.cuda])
        if args.directml:
            qpt_cmd.append("--directml")
        run(qpt_cmd, "QPT 打包")
    else:
        print("\n[SKIP] QPT 打包已跳过")
        if not os.path.isdir(release_dir):
            sys.exit(f"[ERROR] Release 目录不存在: {release_dir}")

    # ── 步骤 2: 编译 Inno Setup 安装包 ──
    if not args.skip_installer:
        iscc = find_iscc()

        sync_iss_version(version, release_dir)
        print(f"[INFO] 已同步 setup.iss: version={version}, release_dir={release_dir}")

        run([iscc, SETUP_ISS_PATH], "Inno Setup 编译安装包")

        installer_name = f"vsr-setup-v{version}.exe"
        installer_path = os.path.join(SCRIPT_DIR, "dist", "installer", installer_name)
        if os.path.isfile(installer_path):
            size_mb = os.path.getsize(installer_path) / (1024 * 1024)
            print(f"\n{'='*60}")
            print(f"  打包完成!")
            print(f"  安装包: {installer_path}")
            print(f"  大小:   {size_mb:.1f} MB")
            print(f"{'='*60}")
    else:
        print("\n[SKIP] 安装包编译已跳过")


if __name__ == "__main__":
    main()
