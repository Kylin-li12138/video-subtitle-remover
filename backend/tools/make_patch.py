# coding: utf-8
"""
补丁包生成工具 - 用于自动更新机制

使用方法:
    python make_patch.py --from v1.3.0                        # 从 v1.3.0 到当前 HEAD
    python make_patch.py --from v1.3.0 --to v1.4.0            # 指定版本区间
    python make_patch.py --from v1.3.0 --output ./dist        # 指定输出目录
    python make_patch.py --from v1.3.0 --no-exe               # 只生成 ZIP，不编译 .exe

原理:
    1. 通过 git diff 找出两个版本之间变更的源文件
    2. 只打包项目源代码 (gui.py, backend/, ui/, config/, design/)
    3. 生成 Inno Setup 脚本并编译成自解压 .exe (支持 /VERYSILENT 静默安装)
    4. 同时输出 ZIP 包 + patch_manifest.json 供参考

输出:
    dist/patches/patch-v{version}.zip       补丁 ZIP
    dist/patches/patch-v{version}.exe       静默安装的补丁 .exe
    dist/patches/patch-v{version}.iss       Inno Setup 脚本 (中间产物)
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parent.parent.parent

SOURCE_DIRS = {"backend", "ui", "config", "design"}
SOURCE_FILES = {"gui.py"}

EXCLUDE_PATTERNS = {
    "__pycache__", ".pyc", ".pyo", ".git", ".venv",
    "node_modules", ".egg-info", "vsr_gui.log", "vsr_gui.err.log",
}

ISCC_PATHS = [
    Path(os.environ.get("ISCC_PATH", "")) if os.environ.get("ISCC_PATH") else None,
    Path(r"C:\Users\kylin_li\AppData\Local\Programs\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def _find_iscc() -> Path | None:
    for p in ISCC_PATHS:
        if p and p.exists():
            return p
    result = shutil.which("iscc") or shutil.which("ISCC")
    return Path(result) if result else None


def _is_source_file(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if not parts:
        return False
    if any(exc in rel_path for exc in EXCLUDE_PATTERNS):
        return False
    if parts[0] in SOURCE_DIRS:
        return True
    if len(parts) == 1 and parts[0] in SOURCE_FILES:
        return True
    return False


def _git(*args, cwd=None) -> str:
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True,
        cwd=cwd or str(PROJ_ROOT),
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败:\n{result.stderr.strip()}")
    return result.stdout.strip()


def _sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_version() -> str:
    config_py = PROJ_ROOT / "backend" / "config.py"
    for line in config_py.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("VERSION"):
            return line.split("=")[1].strip().strip('"').strip("'")
    raise RuntimeError("无法从 backend/config.py 读取 VERSION")


def get_changed_files(from_ref: str, to_ref: str = "HEAD") -> list[str]:
    diff_output = _git("diff", "--name-only", "--diff-filter=ACMR", from_ref, to_ref)
    if not diff_output:
        return []
    return [f for f in diff_output.splitlines() if _is_source_file(f)]


# ------------------------------------------------------------------
#  Inno Setup 脚本生成
# ------------------------------------------------------------------

def _generate_iss(version: str, patch_files_dir: Path, file_list: list[str],
                  output_dir: str) -> Path:
    """根据模板生成补丁的 Inno Setup 脚本"""
    # 生成 [Files] 段
    file_entries = []
    for rel in sorted(file_list):
        src = str(patch_files_dir / rel).replace("/", "\\")
        dest_subdir = str(Path(rel).parent).replace("/", "\\")
        if dest_subdir == ".":
            dest_subdir = ""
        # 安装到 {app}\resources\{subdir}
        if dest_subdir:
            entry = f'Source: "{src}"; DestDir: "{{app}}\\resources\\{dest_subdir}"; Flags: ignoreversion'
        else:
            entry = f'Source: "{src}"; DestDir: "{{app}}\\resources"; Flags: ignoreversion'
        file_entries.append(entry)

    template = PROJ_ROOT / "installer" / "patch_template.iss"
    content = template.read_text(encoding="utf-8")

    content = content.replace("{{PATCH_VERSION}}", version)
    content = content.replace("{{PATCH_FILES_DIR}}", str(patch_files_dir))
    content = content.replace("{{OUTPUT_DIR}}", output_dir)
    content = content.replace("{{FILE_ENTRIES}}", "\n".join(file_entries))

    iss_path = Path(output_dir) / f"patch-v{version}.iss"
    iss_path.write_text(content, encoding="utf-8")
    return iss_path


# ------------------------------------------------------------------
#  主构建流程
# ------------------------------------------------------------------

def build_patch(from_ref: str, to_ref: str = "HEAD",
                output_dir: str = None, build_exe: bool = True):
    to_version = _read_version()
    from_version = from_ref.lstrip("v")

    changed = get_changed_files(from_ref, to_ref)
    if not changed:
        print("没有需要更新的源文件。")
        return None

    print(f"[Patch] {from_ref} → {to_ref} (v{to_version})")
    print(f"[Patch] 变更文件: {len(changed)} 个")

    if output_dir is None:
        output_dir = str(PROJ_ROOT / "dist" / "patches")
    os.makedirs(output_dir, exist_ok=True)

    manifest = {
        "version": to_version,
        "from_version": from_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }

    # --- 1. 生成 ZIP ---
    zip_name = f"patch-v{to_version}.zip"
    zip_path = os.path.join(output_dir, zip_name)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in sorted(changed):
            abs_path = PROJ_ROOT / rel
            if not abs_path.exists():
                print(f"  [跳过] {rel} (已删除)")
                continue
            zf.write(abs_path, rel)
            manifest["files"][rel] = {
                "sha256": _sha256(abs_path),
                "size": abs_path.stat().st_size,
            }
            print(f"  [添加] {rel}")
        zf.writestr("patch_manifest.json",
                     json.dumps(manifest, indent=2, ensure_ascii=False))

    print(f"\n[ZIP] {zip_path} ({os.path.getsize(zip_path) / 1024:.1f} KB)")

    # --- 2. 准备临时文件目录供 Inno Setup 使用 ---
    staging_dir = Path(tempfile.mkdtemp(prefix="vsr_patch_staging_"))
    existing_files = []
    for rel in sorted(changed):
        src = PROJ_ROOT / rel
        if not src.exists():
            continue
        dst = staging_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        existing_files.append(rel)

    # --- 3. 生成并编译 Inno Setup 脚本 ---
    if build_exe:
        iscc = _find_iscc()
        if not iscc:
            print("\n[警告] 未找到 Inno Setup (ISCC.exe)，跳过 .exe 生成")
            print("       请安装 Inno Setup 6 或设置环境变量 ISCC_PATH")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return zip_path

        iss_path = _generate_iss(to_version, staging_dir, existing_files, output_dir)
        print(f"\n[ISS] {iss_path}")
        print("[编译] 正在用 Inno Setup 生成 .exe ...")

        result = subprocess.run(
            [str(iscc), str(iss_path)],
            capture_output=True,
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.returncode != 0:
            print(f"[错误] ISCC 编译失败:\n{stdout}\n{stderr}")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return zip_path

        exe_path = os.path.join(output_dir, f"patch-v{to_version}.exe")
        print(f"[EXE] {exe_path} ({os.path.getsize(exe_path) / 1024:.1f} KB)")

    shutil.rmtree(staging_dir, ignore_errors=True)

    # --- 4. 输出 manifest ---
    manifest_path = os.path.join(output_dir, f"patch-v{to_version}-manifest.json")
    Path(manifest_path).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[完成] 补丁生成成功!")
    print(f"  ZIP:      {zip_path}")
    if build_exe and os.path.exists(os.path.join(output_dir, f"patch-v{to_version}.exe")):
        print(f"  EXE:      {os.path.join(output_dir, f'patch-v{to_version}.exe')}")
    print(f"  Manifest: {manifest_path}")
    print(f"  文件数:   {len(manifest['files'])}")

    return zip_path


def main():
    parser = argparse.ArgumentParser(description="生成补丁更新包 (ZIP + EXE)")
    parser.add_argument("--from", dest="from_ref", required=True,
                        help="起始版本 tag 或 commit，如 v1.3.0")
    parser.add_argument("--to", dest="to_ref", default="HEAD",
                        help="目标版本 tag 或 commit，默认 HEAD")
    parser.add_argument("--output", dest="output_dir", default=None,
                        help="输出目录，默认 dist/patches/")
    parser.add_argument("--no-exe", dest="no_exe", action="store_true",
                        help="只生成 ZIP，不编译 .exe")
    args = parser.parse_args()

    build_patch(args.from_ref, args.to_ref, args.output_dir,
                build_exe=not args.no_exe)


if __name__ == "__main__":
    main()
