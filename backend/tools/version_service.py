# coding: utf-8
"""
版本检查与自动更新服务

更新源: Electron Release Server (electron-updater 协议)
    GET /update/flavor/remover/windows_64/latest.yml

更新流程:
    检查新版本 → 下载补丁 .exe → 静默运行 → 提示重启
"""

import os
import re
import subprocess
import sys
import tempfile

import requests
from PySide6.QtCore import QVersionNumber

from backend.config import VERSION, tr

ERS_BASE_URL = "https://cmupdate.mengjun.icu"
ERS_FLAVOR = "remover"
ERS_PLATFORM = "windows_64"


class VersionService:

    def __init__(self):
        self.current_version = VERSION
        self.lastest_version = VERSION
        self.version_pattern = re.compile(r'v*((\d+)\.(\d+)\.(\d+))')
        self._download_url: str | None = None
        self._download_name: str | None = None

    # ------------------------------------------------------------------
    #  网络辅助
    # ------------------------------------------------------------------

    def _headers(self):
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"
        }

    def _proxies(self):
        proxy = self.get_system_proxy()
        return {"http": proxy, "https": proxy}

    # ------------------------------------------------------------------
    #  版本检查
    # ------------------------------------------------------------------

    def get_latest_version(self) -> str:
        """查询 Electron Release Server REST API 获取最新版本号"""
        ver = self._check_ers()
        if ver:
            return ver
        return VERSION

    def has_new_version(self) -> bool:
        latest = QVersionNumber.fromString(self.get_latest_version())
        current = QVersionNumber.fromString(self.current_version)
        return latest > current

    # --- Electron Release Server (electron-updater 协议) ---

    def _check_ers(self) -> str | None:
        """
        electron-updater 协议:
            GET /update/flavor/remover/windows_64/latest.yml
        返回 YAML 格式:
            version: x.y.z
            files:
              - url: /download/flavor/remover/x.y.z/windows_64/filename.exe
                sha512: ...
                size: ...
            path: /download/flavor/remover/x.y.z/windows_64/filename.exe
        """
        try:
            url = (f"{ERS_BASE_URL}/update/flavor/{ERS_FLAVOR}"
                   f"/{ERS_PLATFORM}/latest.yml")
            resp = requests.get(url, headers=self._headers(),
                                proxies=self._proxies(), timeout=15)

            if resp.status_code != 200:
                return None

            body = resp.text.strip()
            if not body:
                return None

            ver_match = re.search(r'version:\s*(\S+)', body)
            if not ver_match:
                return None

            latest_ver = ver_match.group(1)
            qver_latest = QVersionNumber.fromString(latest_ver)
            qver_current = QVersionNumber.fromString(self.current_version)
            if qver_latest <= qver_current:
                return None

            path_match = re.search(r'(?:path|url):\s*(/download/\S+\.exe)', body)
            if path_match:
                rel_path = path_match.group(1)
                self._download_url = f"{ERS_BASE_URL}{rel_path}"
                self._download_name = rel_path.rsplit("/", 1)[-1]
            else:
                self._download_url = (
                    f"{ERS_BASE_URL}/download/flavor/{ERS_FLAVOR}"
                    f"/{latest_ver}/{ERS_PLATFORM}")
                self._download_name = f"patch-v{latest_ver}.exe"

            self.lastest_version = latest_ver
            self._log_version()
            return latest_ver

        except Exception as e:
            print(f"[Update] Electron Release Server 检查失败: {e}")
            return None

    def _log_version(self):
        try:
            print(tr['VersionService']['VersionInfo'].format(
                VERSION, self.lastest_version))
        except Exception:
            print(f"[Update] 当前: v{VERSION}, 最新: v{self.lastest_version}")

    # ------------------------------------------------------------------
    #  下载更新
    # ------------------------------------------------------------------

    def get_download_url(self) -> str | None:
        return self._download_url

    def download_update(self, progress_callback=None) -> str | None:
        """
        下载更新 .exe 到临时目录。

        Parameters
        ----------
        progress_callback : callable(downloaded_bytes, total_bytes) or None

        Returns
        -------
        下载后的 .exe 文件路径, 失败返回 None
        """
        url = self._download_url
        if not url:
            return None

        try:
            resp = requests.get(url, headers=self._headers(),
                                proxies=self._proxies(),
                                stream=True, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            print(f"[Update] 下载失败: {e}")
            return None

        total = int(resp.headers.get("content-length", 0))
        filename = self._download_name or f"patch-v{self.lastest_version}.exe"
        tmp_dir = tempfile.mkdtemp(prefix="vsr_update_")
        exe_path = os.path.join(tmp_dir, filename)

        downloaded = 0
        with open(exe_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total > 0:
                    progress_callback(downloaded, total)

        print(f"[Update] 下载完成: {exe_path} ({downloaded / 1024:.0f} KB)")
        return exe_path

    # ------------------------------------------------------------------
    #  应用更新 (运行 .exe)
    # ------------------------------------------------------------------

    @staticmethod
    def apply_update(exe_path: str) -> tuple[bool, str]:
        """
        静默运行补丁 .exe。

        Inno Setup 参数:
            /VERYSILENT     无任何弹窗
            /SUPPRESSMSGBOXES  抑制消息框
            /NORESTART      不自动重启
            /DIR=           指定安装目录

        Returns
        -------
        (success, message)
        """
        if not exe_path or not os.path.isfile(exe_path):
            return False, "更新文件不存在"

        install_dir = VersionService._detect_install_dir()

        dir_arg = f' /DIR="{install_dir}"' if install_dir else ""
        cmd_str = f'"{exe_path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART{dir_arg}'

        try:
            print(f"[Update] 执行: {cmd_str}")
            result = subprocess.run(cmd_str, capture_output=True, timeout=120)
            if result.returncode == 0:
                return True, f"更新成功 (v{VERSION} → 新版本)，请重启应用"
            else:
                return False, f"安装程序退出码: {result.returncode}"
        except subprocess.TimeoutExpired:
            return False, "安装超时 (120秒)"
        except Exception as e:
            return False, f"运行安装程序失败: {e}"

    @staticmethod
    def _detect_install_dir() -> str | None:
        """
        检测应用安装目录:
        1. 从注册表读取
        2. 从当前运行路径推断
        """
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Video Subtitle Remover"
                ) as key:
                    path, _ = winreg.QueryValueEx(key, "InstallPath")
                    if os.path.isdir(path):
                        return path
            except Exception:
                pass

        # 从当前文件路径推断: backend/tools/version_service.py → 上三级
        current = os.path.abspath(__file__)
        candidate = os.path.dirname(os.path.dirname(os.path.dirname(current)))
        resources_dir = os.path.join(candidate, "resources")
        if os.path.isdir(resources_dir):
            return candidate
        if os.path.isfile(os.path.join(candidate, "gui.py")):
            return candidate

        return None

    # ------------------------------------------------------------------
    #  系统代理
    # ------------------------------------------------------------------

    @staticmethod
    def get_system_proxy():
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'
                ) as key:
                    enabled, _ = winreg.QueryValueEx(key, 'ProxyEnable')
                    if enabled:
                        return "http://" + winreg.QueryValueEx(key, 'ProxyServer')[0]
            except Exception:
                pass
        elif sys.platform == "darwin":
            s = os.popen('scutil --proxy').read()
            info = dict(re.findall(r'(?m)^\s+([A-Z]\w+)\s+:\s+(\S+)', s))
            if info.get('HTTPEnable') == '1':
                return f"http://{info['HTTPProxy']}:{info['HTTPPort']}"
            elif info.get('ProxyAutoConfigEnable') == '1':
                return info['ProxyAutoConfigURLString']
        return os.environ.get("http_proxy")
