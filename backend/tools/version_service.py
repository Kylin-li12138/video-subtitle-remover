# coding: utf-8
"""
版本检查与自动更新服务

支持两种更新源:
    1. Electron Release Server (Squirrel 协议) - 优先
       URL: https://cmupdate.mengjun.icu/update/remover/win64
    2. GitHub Releases API - 备用

更新流程:
    检查新版本 → 下载补丁 .exe → 静默运行 → 提示重启
"""

import json
import os
import re
import subprocess
import sys
import tempfile

import requests
from PySide6.QtCore import QVersionNumber

from backend.config import VERSION, PROJECT_UPDATE_URLS, tr

# Electron Release Server 地址
ERS_UPDATE_URL = "https://cmupdate.mengjun.icu/update/remover/win64"


class VersionService:

    def __init__(self):
        self.current_version = VERSION
        self.lastest_version = VERSION
        self.version_pattern = re.compile(r'v*((\d+)\.(\d+)\.(\d+))')
        self.github_api_endpoints = PROJECT_UPDATE_URLS
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
        """依次查询 Electron Release Server 和 GitHub，返回最新版本号"""
        ver = self._check_ers()
        if ver:
            return ver
        return self._check_github()

    def has_new_version(self) -> bool:
        latest = QVersionNumber.fromString(self.get_latest_version())
        current = QVersionNumber.fromString(self.current_version)
        return latest > current

    # --- Electron Release Server (Squirrel 协议) ---

    def _check_ers(self) -> str | None:
        """
        Squirrel.Windows 协议:
            GET /update/remover/win64/RELEASES?id=remover&localVersion={ver}&arch=x64
        如果有新版本, 返回 RELEASES 内容 (包含 nupkg/exe 下载信息)
        如果返回 "Version not found" 或 204, 说明无更新
        """
        try:
            url = f"{ERS_UPDATE_URL}/RELEASES"
            params = {
                "id": "remover",
                "localVersion": self.current_version,
                "arch": "x64",
            }
            resp = requests.get(url, params=params, headers=self._headers(),
                                proxies=self._proxies(), timeout=10)

            if resp.status_code == 204:
                return None

            body = resp.text.strip()
            if not body or "not found" in body.lower():
                return None

            # RELEASES 文件格式: SHA1 filename size
            # 例: ABC123 remover-1.5.0-full.exe 12345
            for line in body.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    filename = parts[1]
                    match = self.version_pattern.search(filename)
                    if match:
                        self.lastest_version = match.group(1)
                        base = ERS_UPDATE_URL.rstrip("/")
                        self._download_url = f"{base}/{filename}"
                        self._download_name = filename
                        self._log_version()
                        return self.lastest_version

            return None
        except Exception as e:
            print(f"[Update] Electron Release Server 检查失败: {e}")
            return None

    # --- GitHub Releases ---

    def _check_github(self) -> str:
        for url in self.github_api_endpoints:
            try:
                resp = requests.get(url, headers=self._headers(),
                                    proxies=self._proxies(), timeout=10,
                                    allow_redirects=True)
                resp.raise_for_status()
                data = resp.json()

                tag = data.get("tag_name", "")
                match = self.version_pattern.search(tag)
                if not match:
                    continue

                self.lastest_version = match.group(1)

                # 在 assets 中找补丁 .exe
                for asset in data.get("assets", []):
                    name = asset.get("name", "")
                    if name.startswith("patch-") and name.endswith(".exe"):
                        self._download_url = asset.get("browser_download_url")
                        self._download_name = name
                        break

                self._log_version()
                return self.lastest_version
            except Exception as e:
                print(tr['VersionService']['RequestError'].format(url, str(e)))
                continue

        return VERSION

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

        cmd = [exe_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        if install_dir:
            cmd.append(f"/DIR={install_dir}")

        try:
            print(f"[Update] 执行: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, timeout=120)
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
