# -*- coding: utf-8 -*-
import os
import sys
import shutil
import subprocess
import importlib.util
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from PySide6.QtCore import QObject, Signal, QProcess, QThread


@dataclass
class GpuInfo:
    name: str = ""
    driver_version: str = ""
    cuda_version: str = ""
    vram_mb: int = 0


@dataclass
class DependencyStatus:
    name: str
    package_name: str
    required_version: str
    installed: bool = False
    installed_version: str = ""
    install_command: str = ""
    is_optional: bool = False


MIRROR_PRESETS = {
    "清华大学": {
        "pypi": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "torch_cu128": "https://download.pytorch.org/whl/cu128",
        "torch_cu126": "https://download.pytorch.org/whl/cu126",
        "paddle": "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
    },
    "阿里云": {
        "pypi": "https://mirrors.aliyun.com/pypi/simple",
        "torch_cu128": "https://download.pytorch.org/whl/cu128",
        "torch_cu126": "https://download.pytorch.org/whl/cu126",
        "paddle": "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
    },
    "中科大": {
        "pypi": "https://pypi.mirrors.ustc.edu.cn/simple",
        "torch_cu128": "https://download.pytorch.org/whl/cu128",
        "torch_cu126": "https://download.pytorch.org/whl/cu126",
        "paddle": "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
    },
    "上交大 (PyTorch推荐)": {
        "pypi": "https://pypi.tuna.tsinghua.edu.cn/simple",
        "torch_cu128": "https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/",
        "torch_cu126": "https://mirror.sjtu.edu.cn/pytorch-wheels/cu126/",
        "paddle": "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
    },
    "官方源 (海外)": {
        "pypi": "https://pypi.org/simple",
        "torch_cu128": "https://download.pytorch.org/whl/cu128",
        "torch_cu126": "https://download.pytorch.org/whl/cu126",
        "paddle": "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
    },
}

DEFAULT_MIRROR = "清华大学"

CUDA_VERSION_MAP = {
    "12.8": "cu128",
    "12.6": "cu126",
    "12.4": "cu126",
    "12.2": "cu126",
    "12.1": "cu126",
    "11.8": "cu118",
}


def detect_gpu() -> GpuInfo:
    """通过 nvidia-smi 获取 GPU 信息"""
    info = GpuInfo()
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("\n")[0].split(", ")
            if len(parts) >= 3:
                info.name = parts[0].strip()
                info.driver_version = parts[1].strip()
                info.vram_mb = int(float(parts[2].strip()))
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        if result.returncode == 0:
            smi_output = subprocess.run(
                ["nvidia-smi"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            cuda_match = re.search(r"CUDA Version:\s*([\d.]+)", smi_output.stdout)
            if cuda_match:
                info.cuda_version = cuda_match.group(1)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    return info


def recommend_cuda_tag(gpu_info: GpuInfo) -> str:
    """根据 GPU 信息推荐 CUDA 版本标签"""
    if not gpu_info.cuda_version:
        return "cu128"
    major_minor = ".".join(gpu_info.cuda_version.split(".")[:2])
    for ver, tag in CUDA_VERSION_MAP.items():
        if major_minor >= ver:
            return tag
    return "cu128"


def _get_package_version(package_name: str) -> Optional[str]:
    """获取已安装包的版本"""
    try:
        if importlib.util.find_spec(package_name) is None:
            alt_names = {
                "paddlepaddle": "paddle",
                "paddleocr": "paddleocr",
                "torch": "torch",
                "torchvision": "torchvision",
                "onnxruntime": "onnxruntime",
            }
            alt = alt_names.get(package_name)
            if alt and alt != package_name:
                if importlib.util.find_spec(alt) is None:
                    return None
            else:
                return None

        from importlib.metadata import version, PackageNotFoundError
        try:
            return version(package_name)
        except PackageNotFoundError:
            lookup_map = {
                "paddlepaddle": ["paddlepaddle-gpu", "paddlepaddle"],
                "torch": ["torch"],
                "torchvision": ["torchvision"],
                "paddleocr": ["paddleocr"],
                "onnxruntime": ["onnxruntime-directml", "onnxruntime-gpu", "onnxruntime"],
            }
            for alt_pkg in lookup_map.get(package_name, []):
                try:
                    return version(alt_pkg)
                except PackageNotFoundError:
                    continue
            return None
    except Exception:
        return None


def check_dependencies(gpu_info: GpuInfo, cuda_tag: str = "cu128") -> list:
    """检测所有重型依赖的安装状态"""
    deps = []

    torch_ver = _get_package_version("torch")
    deps.append(DependencyStatus(
        name="PyTorch",
        package_name="torch",
        required_version=f"2.7.0+{cuda_tag}",
        installed=torch_ver is not None,
        installed_version=torch_ver or "",
    ))

    tv_ver = _get_package_version("torchvision")
    deps.append(DependencyStatus(
        name="TorchVision",
        package_name="torchvision",
        required_version=f"0.22.0+{cuda_tag}",
        installed=tv_ver is not None,
        installed_version=tv_ver or "",
    ))

    paddle_ver = _get_package_version("paddlepaddle")
    deps.append(DependencyStatus(
        name="PaddlePaddle",
        package_name="paddlepaddle",
        required_version="3.0.0",
        installed=paddle_ver is not None,
        installed_version=paddle_ver or "",
    ))

    ocr_ver = _get_package_version("paddleocr")
    deps.append(DependencyStatus(
        name="PaddleOCR",
        package_name="paddleocr",
        required_version="3.4.0",
        installed=ocr_ver is not None,
        installed_version=ocr_ver or "",
    ))

    onnx_ver = _get_package_version("onnxruntime")
    deps.append(DependencyStatus(
        name="ONNX Runtime (DirectML)",
        package_name="onnxruntime-directml",
        required_version="1.20.1",
        installed=onnx_ver is not None,
        installed_version=onnx_ver or "",
        is_optional=True,
    ))

    return deps


def build_install_commands(deps: list, mirror_name: str = DEFAULT_MIRROR, cuda_tag: str = "cu128") -> list:
    """为缺失的依赖构建 pip install 命令列表"""
    mirror = MIRROR_PRESETS.get(mirror_name, MIRROR_PRESETS[DEFAULT_MIRROR])
    commands = []

    torch_key = f"torch_{cuda_tag}"
    torch_index = mirror.get(torch_key, mirror.get("torch_cu128"))
    pypi_index = mirror["pypi"]
    paddle_index = mirror["paddle"]

    for dep in deps:
        if dep.installed:
            continue

        if dep.package_name in ("torch", "torchvision"):
            cmd = [
                sys.executable, "-m", "pip", "install",
                f"{dep.package_name}=={dep.required_version.split('+')[0]}",
                "--index-url", torch_index,
                "--trusted-host", _extract_host(torch_index),
            ]
        elif dep.package_name == "paddlepaddle":
            cmd = [
                sys.executable, "-m", "pip", "install",
                "paddlepaddle-gpu==3.0.0",
                "-i", paddle_index,
                "--trusted-host", _extract_host(paddle_index),
            ]
        elif dep.package_name == "paddleocr":
            # paddleocr → paddlex[ocr-core] → opencv-contrib-python
            # opencv-contrib-python tries to overwrite cv2.pyd which is locked
            # by the running application on Windows.
            # Split into: paddleocr --no-deps → paddlex base → remaining extras.
            commands.append((dep, [
                sys.executable, "-m", "pip", "install",
                f"{dep.package_name}=={dep.required_version}",
                "--no-deps",
                "-i", pypi_index,
                "--trusted-host", _extract_host(pypi_index),
            ]))
            commands.append((DependencyStatus(
                name="PaddleX", package_name="paddlex",
                required_version="3.4.0",
            ), [
                sys.executable, "-m", "pip", "install",
                "paddlex>=3.4.0,<3.5.0",
                "-i", pypi_index,
                "--trusted-host", _extract_host(pypi_index),
            ]))
            commands.append((DependencyStatus(
                name="OCR Extras", package_name="imagesize",
                required_version="",
            ), [
                sys.executable, "-m", "pip", "install",
                "imagesize", "pyclipper", "pypdfium2", "python-bidi", "shapely",
                "-i", pypi_index,
                "--trusted-host", _extract_host(pypi_index),
            ]))
            continue
        elif dep.package_name == "onnxruntime-directml":
            cmd = [
                sys.executable, "-m", "pip", "install",
                f"{dep.package_name}=={dep.required_version}",
                "-i", pypi_index,
                "--trusted-host", _extract_host(pypi_index),
            ]
        else:
            cmd = [
                sys.executable, "-m", "pip", "install",
                f"{dep.package_name}=={dep.required_version}",
                "-i", pypi_index,
                "--trusted-host", _extract_host(pypi_index),
            ]
        commands.append((dep, cmd))

    return commands


def _extract_host(url: str) -> str:
    """从 URL 中提取主机名"""
    from urllib.parse import urlparse
    return urlparse(url).hostname or ""


class DependencyInstaller(QObject):
    """异步依赖安装器，通过 QProcess 执行 pip install"""

    output_signal = Signal(str)
    progress_signal = Signal(int)
    finished_signal = Signal(bool, str)
    package_started_signal = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._commands: list = []
        self._current_index: int = 0
        self._total: int = 0
        self._has_error: bool = False

    def install(self, commands: list):
        """开始安装依赖列表"""
        self._commands = commands
        self._current_index = 0
        self._total = len(commands)
        self._has_error = False

        if not commands:
            self.finished_signal.emit(True, "所有依赖已就绪")
            return

        self._run_next()

    def _run_next(self):
        """执行下一个安装命令"""
        if self._current_index >= self._total:
            self.finished_signal.emit(not self._has_error,
                                      "安装完成" if not self._has_error else "部分安装失败")
            return

        dep, cmd = self._commands[self._current_index]
        self.package_started_signal.emit(dep.name)
        self.output_signal.emit(f"\n{'='*50}")
        self.output_signal.emit(f"[{self._current_index + 1}/{self._total}] 正在安装: {dep.name}")
        self.output_signal.emit(f"命令: {' '.join(cmd)}")
        self.output_signal.emit(f"{'='*50}\n")

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_output)
        self._process.finished.connect(self._on_finished)

        self._process.start(cmd[0], cmd[1:])

    def _on_output(self):
        """处理 pip 输出"""
        if self._process is None:
            return
        data = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        for line in data.splitlines():
            self.output_signal.emit(line)
            progress_match = re.search(r"(\d+)%", line)
            if progress_match:
                pkg_progress = int(progress_match.group(1))
                overall = int(
                    (self._current_index / self._total * 100) +
                    (pkg_progress / self._total)
                )
                self.progress_signal.emit(min(overall, 99))

    def _on_finished(self, exit_code, exit_status):
        """单个包安装完成"""
        if exit_code != 0:
            self._has_error = True
            dep, _ = self._commands[self._current_index]
            self.output_signal.emit(f"\n[错误] {dep.name} 安装失败 (退出码: {exit_code})")

        self._current_index += 1
        overall = int(self._current_index / self._total * 100)
        self.progress_signal.emit(overall)
        self._run_next()

    def cancel(self):
        """取消安装"""
        if self._process and self._process.state() != QProcess.NotRunning:
            self._process.kill()
            self._process.waitForFinished(3000)
        self.output_signal.emit("\n[已取消] 安装已被用户中止")
        self.finished_signal.emit(False, "用户取消安装")


# ---------------------------------------------------------------------------
#  资源文件 (模型 / FFmpeg) 在线下载
# ---------------------------------------------------------------------------

@dataclass
class ResourceInfo:
    name: str
    filename: str
    target_dir: str
    size_mb: int
    description: str
    check_paths: list = field(default_factory=list)
    installed: bool = False
    files: list = field(default_factory=list)


_HF_RESOLVE = "https://huggingface.co/{}/resolve/main/{}"
_GH_RELEASE = "https://github.com/{}/releases/download/{}/{}"
_PROPAINTER_TAG = "v0.1.0"

RESOURCE_MIRROR_PRESETS = {
    "hf-mirror 国内镜像 (推荐)": {
        "hf_mirror": "https://hf-mirror.com",
        "gh_proxy": "https://ghfast.top/",
    },
    "gh-proxy.com 国内镜像": {
        "hf_mirror": "https://hf-mirror.com",
        "gh_proxy": "https://gh-proxy.com/",
    },
    "ghproxy.net 国内镜像": {
        "hf_mirror": "https://hf-mirror.com",
        "gh_proxy": "https://ghproxy.net/",
    },
    "直连 (海外用户)": {
        "hf_mirror": "",
        "gh_proxy": "",
    },
}

DEFAULT_RESOURCE_MIRROR = "hf-mirror 国内镜像 (推荐)"

RESOURCE_DOWNLOADS: list[ResourceInfo] = [
    ResourceInfo(
        name="Big-LAMA",
        filename="",
        target_dir="models/big-lama",
        size_mb=206,
        description="图像修复模型 (LAMA 算法)",
        check_paths=["models/big-lama"],
        files=[
            (_HF_RESOLVE.format("spaces/paulpang/video-subtitle-remover",
                "backend/models/big-lama/big-lama_1.pt"), "big-lama_1.pt"),
            (_HF_RESOLVE.format("spaces/paulpang/video-subtitle-remover",
                "backend/models/big-lama/big-lama_2.pt"), "big-lama_2.pt"),
            (_HF_RESOLVE.format("spaces/paulpang/video-subtitle-remover",
                "backend/models/big-lama/big-lama_3.pt"), "big-lama_3.pt"),
            (_HF_RESOLVE.format("spaces/paulpang/video-subtitle-remover",
                "backend/models/big-lama/big-lama_4.pt"), "big-lama_4.pt"),
            (_HF_RESOLVE.format("spaces/paulpang/video-subtitle-remover",
                "backend/models/big-lama/big-lama_5.pt"), "big-lama_5.pt"),
            (_HF_RESOLVE.format("spaces/paulpang/video-subtitle-remover",
                "backend/models/big-lama/fs_manifest.csv"), "fs_manifest.csv"),
        ],
    ),
    ResourceInfo(
        name="ProPainter",
        filename="",
        target_dir="models/propainter",
        size_mb=60,
        description="视频修复模型 (ProPainter 算法)",
        check_paths=["models/propainter"],
        files=[
            (_GH_RELEASE.format("sczhou/ProPainter", _PROPAINTER_TAG, "ProPainter.pth"), "ProPainter.pth"),
            (_GH_RELEASE.format("sczhou/ProPainter", _PROPAINTER_TAG, "raft-things.pth"), "raft-things.pth"),
            (_GH_RELEASE.format("sczhou/ProPainter", _PROPAINTER_TAG, "recurrent_flow_completion.pth"), "recurrent_flow_completion.pth"),
        ],
    ),
    ResourceInfo(
        name="STTN",
        filename="",
        target_dir="models",
        size_mb=126,
        description="视频字幕擦除模型 (STTN 算法)",
        check_paths=["models/sttn-auto", "models/sttn-det"],
        files=[
            (_HF_RESOLVE.format(
                "spaces/paulpang/video-subtitle-remover",
                "backend/models/sttn/infer_model.pth"), "sttn-auto/infer_model.pth"),
            (_HF_RESOLVE.format(
                "spaces/paulpang/video-subtitle-remover",
                "backend/models/sttn/infer_model.pth"), "sttn-det/sttn.pth"),
        ],
    ),
    ResourceInfo(
        name="V5 检测模型",
        filename="",
        target_dir="models/V5",
        size_mb=50,
        description="PP-OCRv5 文字检测模型 (首次使用时自动下载)",
        check_paths=[],
        files=[],
    ),
    ResourceInfo(
        name="FFmpeg",
        filename="ffmpeg-master-latest-win64-gpl.zip",
        target_dir="",
        size_mb=210,
        description="音视频处理工具",
        check_paths=["ffmpeg/win_x64"],
        files=[(_GH_RELEASE.format(
            "BtbN/FFmpeg-Builds", "latest",
            "ffmpeg-master-latest-win64-gpl.zip"), None)],
    ),
]


def check_resources(base_dir: str) -> list[ResourceInfo]:
    """检测所有资源文件的安装状态，返回带 installed 标记的副本列表"""
    results: list[ResourceInfo] = []
    for res in RESOURCE_DOWNLOADS:
        info = ResourceInfo(
            name=res.name,
            filename=res.filename,
            target_dir=res.target_dir,
            size_mb=res.size_mb,
            description=res.description,
            check_paths=list(res.check_paths),
            files=list(res.files),
        )
        if res.check_paths:
            info.installed = all(
                os.path.isdir(os.path.join(base_dir, p)) for p in res.check_paths
            )
        else:
            info.installed = True
        results.append(info)
    return results


class ResourceDownloader(QThread):
    """资源文件下载器 —— 在后台线程中分块下载 zip 并解压"""

    output_signal = Signal(str)
    progress_signal = Signal(int)
    finished_signal = Signal(bool, str)
    resource_started_signal = Signal(str)

    _CHUNK_SIZE = 64 * 1024  # 64 KB

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resources: list[ResourceInfo] = []
        self._base_dir: str = ""
        self._gh_proxy: str = ""
        self._cancelled: bool = False

    def setup(self, resources: list[ResourceInfo], base_dir: str,
              mirror_name: str = DEFAULT_RESOURCE_MIRROR):
        self._resources = resources
        self._base_dir = base_dir
        mirror_cfg = RESOURCE_MIRROR_PRESETS.get(
            mirror_name, list(RESOURCE_MIRROR_PRESETS.values())[0]
        )
        self._hf_mirror: str = mirror_cfg.get("hf_mirror", "")
        self._gh_proxy: str = mirror_cfg.get("gh_proxy", "")
        self._cancelled = False

    def _apply_proxy(self, url: str) -> str:
        if self._hf_mirror and "huggingface.co" in url:
            return url.replace("https://huggingface.co", self._hf_mirror)
        if self._gh_proxy and "github.com" in url:
            return self._gh_proxy + url
        return url

    # -- QThread entry point --------------------------------------------------

    def run(self):
        total = len(self._resources)
        if total == 0:
            self.finished_signal.emit(True, "没有需要下载的资源")
            return

        try:
            self._ensure_writable()
        except PermissionError as e:
            self.finished_signal.emit(False, str(e))
            return

        has_error = False
        for idx, res in enumerate(self._resources):
            if self._cancelled:
                break

            self.resource_started_signal.emit(res.name)
            self.output_signal.emit(f"\n{'=' * 50}")
            self.output_signal.emit(
                f"[{idx + 1}/{total}] 正在下载: {res.name}  (~{res.size_mb} MB)"
            )
            self.output_signal.emit(f"{'=' * 50}\n")

            try:
                if not res.files:
                    self.output_signal.emit(f"  [信息] {res.name} 首次使用时由 PaddleOCR 自动下载，无需手动操作")
                elif any(u.endswith(".zip") for u, _ in res.files if u):
                    self._download_zip_resource(res, idx, total)
                else:
                    self._download_file_resource(res, idx, total)

                if self._cancelled:
                    break
                self.output_signal.emit(f"[完成] {res.name} 下载成功\n")
            except Exception as exc:
                has_error = True
                self.output_signal.emit(f"\n[错误] {res.name} 下载失败: {exc}")

            overall = int((idx + 1) / total * 100)
            self.progress_signal.emit(overall)

        if self._cancelled:
            self.finished_signal.emit(False, "用户取消下载")
        elif has_error:
            self.finished_signal.emit(False, "部分资源下载失败")
        else:
            self.finished_signal.emit(True, "所有资源下载完成")

    def _download_zip_resource(self, res: ResourceInfo, idx: int, total: int):
        url, _ = res.files[0]
        url = self._apply_proxy(url)
        self.output_signal.emit(f"  URL: {url}")
        tmp_path = self._download_file(url, idx, total)
        if self._cancelled:
            self._cleanup(tmp_path)
            return
        self._extract_zip(tmp_path, res)
        self._cleanup(tmp_path)

    def _download_file_resource(self, res: ResourceInfo, idx: int, total: int):
        dest_base = os.path.join(self._base_dir, res.target_dir) if res.target_dir else self._base_dir
        os.makedirs(dest_base, exist_ok=True)
        for fi, (url, rel_path) in enumerate(res.files):
            if self._cancelled:
                return
            url = self._apply_proxy(url)
            fname = rel_path or url.rsplit("/", 1)[-1]
            self.output_signal.emit(f"  [{fi+1}/{len(res.files)}] {fname}")
            dest = os.path.join(dest_base, fname)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp_path = self._download_file(url, idx, total)
            if self._cancelled:
                self._cleanup(tmp_path)
                return
            shutil.move(tmp_path, dest)
            self.output_signal.emit(f"        -> {dest}")

    # -- internal helpers -----------------------------------------------------

    def _download_file(self, url: str, res_idx: int, total: int) -> str:
        """下载单个文件到临时路径，返回临时文件路径"""
        req = Request(url, headers={"User-Agent": "VSR-Downloader/1.0"})
        resp = urlopen(req, timeout=60)
        content_length = int(resp.headers.get("Content-Length", 0))

        suffix = ".zip" if url.endswith(".zip") else ".tmp"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            downloaded = 0
            last_pct = -1
            with os.fdopen(fd, "wb") as f:
                while True:
                    if self._cancelled:
                        return tmp_path
                    chunk = resp.read(self._CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if content_length > 0:
                        pct = int(downloaded / content_length * 100)
                        if pct != last_pct:
                            last_pct = pct
                            speed_info = f"{downloaded / 1048576:.1f}/{content_length / 1048576:.1f} MB"
                            self.output_signal.emit(
                                f"  下载进度: {pct}%  ({speed_info})"
                            )
                            overall = int(
                                (res_idx / total * 100) + (pct / total)
                            )
                            self.progress_signal.emit(min(overall, 99))
        except Exception:
            self._cleanup(tmp_path)
            raise

        return tmp_path

    def _extract_zip(self, zip_path: str, res: ResourceInfo):
        """解压 zip 到目标目录，自动处理嵌套顶层目录"""
        extract_to = os.path.join(self._base_dir, res.target_dir) if res.target_dir else self._base_dir
        os.makedirs(extract_to, exist_ok=True)

        self.output_signal.emit(f"  正在解压到 {extract_to} ...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            top_dirs = {n.split("/")[0] for n in names if "/" in n}

            if res.name == "FFmpeg" and len(top_dirs) == 1:
                staging = tempfile.mkdtemp(prefix="vsr_ffmpeg_")
                try:
                    zf.extractall(staging)
                    nested = os.path.join(staging, top_dirs.pop())
                    bin_dir = os.path.join(nested, "bin")
                    src_exe = os.path.join(bin_dir, "ffmpeg.exe")
                    if os.path.isfile(src_exe):
                        dest_dir = os.path.join(self._base_dir, "ffmpeg", "win_x64")
                        os.makedirs(dest_dir, exist_ok=True)
                        shutil.copy2(src_exe, os.path.join(dest_dir, "ffmpeg.exe"))
                    else:
                        dest = os.path.join(self._base_dir, "ffmpeg")
                        if os.path.exists(dest):
                            shutil.rmtree(dest)
                        shutil.move(nested, dest)
                finally:
                    shutil.rmtree(staging, ignore_errors=True)
            else:
                zf.extractall(extract_to)
        self.output_signal.emit("  解压完成")

    def _ensure_writable(self):
        """在下载前一次性检查并获取目标目录的写入权限"""
        probe = os.path.join(self._base_dir, f".vsr_probe_{os.getpid()}")
        try:
            os.makedirs(probe, exist_ok=True)
            os.rmdir(probe)
            return
        except PermissionError:
            pass

        self.output_signal.emit("目标目录需要管理员权限，正在申请写入权限...\n")

        username = os.environ.get("USERNAME", "")
        if not username:
            raise PermissionError("无法获取当前用户名，请以管理员身份运行程序")

        dirs_to_grant = set()
        for res in self._resources:
            if res.target_dir:
                top = res.target_dir.split("/")[0].split("\\")[0]
                dirs_to_grant.add(os.path.join(self._base_dir, top))
            else:
                dirs_to_grant.add(self._base_dir)

        bat_fd, bat_path = tempfile.mkstemp(suffix=".bat")
        try:
            with os.fdopen(bat_fd, "w", encoding="mbcs") as f:
                f.write("@echo off\n")
                for d in sorted(dirs_to_grant):
                    f.write(f'if not exist "{d}" mkdir "{d}"\n')
                    f.write(f'icacls "{d}" /grant "{username}:(OI)(CI)M" /T /Q\n')
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Start-Process -FilePath '{bat_path}' -Verb RunAs -Wait"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                self.output_signal.emit(f"  权限申请返回码: {result.returncode}")
        except subprocess.TimeoutExpired:
            raise PermissionError("权限申请超时，请以管理员身份运行程序")
        except Exception as e:
            raise PermissionError(f"权限申请失败: {e}")
        finally:
            try:
                os.remove(bat_path)
            except OSError:
                pass

        try:
            os.makedirs(probe, exist_ok=True)
            os.rmdir(probe)
            self.output_signal.emit("写入权限已获取\n")
        except PermissionError:
            raise PermissionError(
                "无法获取目标目录的写入权限，请以管理员身份运行程序"
            )

    @staticmethod
    def _cleanup(path: str):
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    def cancel(self):
        """取消下载"""
        self._cancelled = True
