# -*- coding: utf-8 -*-
import sys
import subprocess
import importlib.util
import re
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Signal, QProcess


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
