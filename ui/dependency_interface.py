# -*- coding: utf-8 -*-
"""
依赖安装界面 - 可视化检测和安装重型 AI 依赖
"""
import sys
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import (ScrollArea, CardWidget, SubtitleLabel,
                           FluentIcon, PushButton, PrimaryPushButton, ProgressBar,
                           ComboBox, BodyLabel, CaptionLabel, StrongBodyLabel,
                           InfoBar, InfoBarPosition, PlainTextEdit)

from backend.config import tr
from backend.tools.dependency_manager import (
    detect_gpu, recommend_cuda_tag, check_dependencies,
    build_install_commands, DependencyInstaller, GpuInfo,
    MIRROR_PRESETS, DEFAULT_MIRROR
)


class GpuDetectThread(QThread):
    """后台线程检测 GPU 信息"""
    finished = Signal(object)

    def run(self):
        info = detect_gpu()
        self.finished.emit(info)


class DepCheckThread(QThread):
    """后台线程检测依赖状态"""
    finished = Signal(list)

    def __init__(self, gpu_info, cuda_tag, parent=None):
        super().__init__(parent)
        self.gpu_info = gpu_info
        self.cuda_tag = cuda_tag

    def run(self):
        deps = check_dependencies(self.gpu_info, self.cuda_tag)
        self.finished.emit(deps)


class DependencyItemCard(CardWidget):
    """单个依赖项的卡片组件"""

    install_clicked = Signal(object)

    def __init__(self, dep_status, parent=None):
        super().__init__(parent)
        self.dep = dep_status
        self.setFixedHeight(64)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(12)

        name_layout = QVBoxLayout()
        name_layout.setSpacing(2)
        self.name_label = StrongBodyLabel(self.dep.name, self)
        self.version_label = CaptionLabel(f"需要: {self.dep.required_version}", self)
        name_layout.addWidget(self.name_label)
        name_layout.addWidget(self.version_label)
        layout.addLayout(name_layout, 1)

        self.status_label = BodyLabel("", self)
        self.status_label.setFixedWidth(180)
        layout.addWidget(self.status_label)

        self.action_btn = PushButton("安装", self)
        self.action_btn.setFixedWidth(80)
        self.action_btn.clicked.connect(lambda: self.install_clicked.emit(self.dep))
        layout.addWidget(self.action_btn)

        self.update_status(self.dep)

    def update_status(self, dep):
        """更新显示状态"""
        self.dep = dep
        if dep.installed:
            self.status_label.setText(f"✓ 已安装 ({dep.installed_version})")
            self.status_label.setStyleSheet("color: #2ecc71;")
            self.action_btn.setText("重新安装")
            self.action_btn.setEnabled(True)
        else:
            if dep.is_optional:
                self.status_label.setText("○ 未安装 (可选)")
                self.status_label.setStyleSheet("color: #f39c12;")
            else:
                self.status_label.setText("✗ 未安装")
                self.status_label.setStyleSheet("color: #e74c3c;")
            self.action_btn.setText("安装")
            self.action_btn.setEnabled(True)


class DependencyInterface(ScrollArea):
    """依赖安装界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = parent
        self._gpu_info = GpuInfo()
        self._cuda_tag = "cu128"
        self._deps = []
        self._installer: DependencyInstaller = None
        self._dep_cards: list = []
        self._is_installing = False

        self.__init_widgets()
        self._start_detection()

    def __init_widgets(self):
        self.scrollWidget = QWidget(self)
        self.expandLayout = QVBoxLayout(self.scrollWidget)
        self.expandLayout.setContentsMargins(36, 20, 36, 20)
        self.expandLayout.setSpacing(16)

        self.setWidget(self.scrollWidget)
        self.enableTransparentBackground()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setAttribute(Qt.WA_StyledBackground)

        self._create_env_section()
        self._create_mirror_section()
        self._create_dep_section()
        self._create_action_section()
        self._create_log_section()
        self.expandLayout.addStretch(1)

    def _create_env_section(self):
        """环境信息区域"""
        title = SubtitleLabel(self._tr("EnvInfo"), self.scrollWidget)
        self.expandLayout.addWidget(title)

        self.env_card = CardWidget(self.scrollWidget)
        env_layout = QVBoxLayout(self.env_card)
        env_layout.setContentsMargins(16, 12, 16, 12)
        env_layout.setSpacing(8)

        self.gpu_label = BodyLabel(self._tr("Detecting"), self.env_card)
        self.driver_label = BodyLabel("", self.env_card)
        self.cuda_label = BodyLabel("", self.env_card)
        self.python_label = BodyLabel(
            f"Python: {sys.version.split()[0]} ({sys.executable})", self.env_card
        )

        env_layout.addWidget(self.gpu_label)
        env_layout.addWidget(self.driver_label)
        env_layout.addWidget(self.cuda_label)
        env_layout.addWidget(self.python_label)

        self.expandLayout.addWidget(self.env_card)

    def _create_mirror_section(self):
        """镜像源选择区域"""
        mirror_card = CardWidget(self.scrollWidget)
        mirror_layout = QHBoxLayout(mirror_card)
        mirror_layout.setContentsMargins(16, 12, 16, 12)
        mirror_layout.setSpacing(12)

        mirror_label = StrongBodyLabel(self._tr("DownloadSource"), self.scrollWidget)
        mirror_layout.addWidget(mirror_label)

        self.mirror_combo = ComboBox(self.scrollWidget)
        self.mirror_combo.addItems(list(MIRROR_PRESETS.keys()))
        self.mirror_combo.setCurrentText(DEFAULT_MIRROR)
        self.mirror_combo.setMinimumWidth(200)
        mirror_layout.addWidget(self.mirror_combo)

        mirror_tip = CaptionLabel(self._tr("MirrorTip"), self.scrollWidget)
        mirror_layout.addWidget(mirror_tip, 1)

        self.expandLayout.addWidget(mirror_card)

    def _create_dep_section(self):
        """依赖列表区域"""
        title = SubtitleLabel(self._tr("DepList"), self.scrollWidget)
        self.expandLayout.addWidget(title)

        self.dep_list_layout = QVBoxLayout()
        self.dep_list_layout.setSpacing(8)

        placeholder = BodyLabel(self._tr("CheckingDeps"), self.scrollWidget)
        self.dep_list_layout.addWidget(placeholder)

        self.expandLayout.addLayout(self.dep_list_layout)

    def _create_action_section(self):
        """操作按钮区域"""
        action_layout = QHBoxLayout()
        action_layout.setSpacing(12)

        self.refresh_btn = PushButton(FluentIcon.SYNC, self._tr("Refresh"), self.scrollWidget)
        self.refresh_btn.clicked.connect(self._start_detection)
        action_layout.addWidget(self.refresh_btn)

        action_layout.addStretch(1)

        self.cancel_btn = PushButton(self._tr("Cancel"), self.scrollWidget)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_install)
        action_layout.addWidget(self.cancel_btn)

        self.install_all_btn = PrimaryPushButton(
            FluentIcon.DOWNLOAD, self._tr("InstallAll"), self.scrollWidget
        )
        self.install_all_btn.clicked.connect(self._install_all_missing)
        action_layout.addWidget(self.install_all_btn)

        self.expandLayout.addLayout(action_layout)

        self.progress_bar = ProgressBar(self.scrollWidget)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.expandLayout.addWidget(self.progress_bar)

    def _create_log_section(self):
        """安装日志区域"""
        title = SubtitleLabel(self._tr("InstallLog"), self.scrollWidget)
        self.expandLayout.addWidget(title)

        self.log_text = PlainTextEdit(self.scrollWidget)
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(200)
        self.log_text.setPlaceholderText(self._tr("LogPlaceholder"))
        self.expandLayout.addWidget(self.log_text)

    def _start_detection(self):
        """开始检测 GPU 和依赖"""
        self.gpu_label.setText(self._tr("Detecting"))
        self.driver_label.setText("")
        self.cuda_label.setText("")
        self.install_all_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)

        self._gpu_thread = GpuDetectThread(self)
        self._gpu_thread.finished.connect(self._on_gpu_detected)
        self._gpu_thread.start()

    def _on_gpu_detected(self, gpu_info: GpuInfo):
        """GPU 检测完成"""
        self._gpu_info = gpu_info

        if gpu_info.name:
            self.gpu_label.setText(f"GPU: {gpu_info.name} ({gpu_info.vram_mb} MB)")
            self.driver_label.setText(f"{self._tr('Driver')}: {gpu_info.driver_version}")
            self._cuda_tag = recommend_cuda_tag(gpu_info)
            cuda_display = gpu_info.cuda_version if gpu_info.cuda_version else self._tr("Unknown")
            self.cuda_label.setText(
                f"CUDA: {cuda_display} → {self._tr('Recommend')}: {self._cuda_tag}"
            )
        else:
            self.gpu_label.setText(self._tr("NoGpu"))
            self.driver_label.setText(self._tr("NoGpuHint"))
            self.cuda_label.setText("")
            self._cuda_tag = "cu128"

        self._check_deps()

    def _check_deps(self):
        """检测依赖状态"""
        self._dep_thread = DepCheckThread(self._gpu_info, self._cuda_tag, self)
        self._dep_thread.finished.connect(self._on_deps_checked)
        self._dep_thread.start()

    def _on_deps_checked(self, deps: list):
        """依赖检测完成，刷新列表"""
        self._deps = deps

        while self.dep_list_layout.count():
            item = self.dep_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._dep_cards = []
        for dep in deps:
            card = DependencyItemCard(dep, self.scrollWidget)
            card.install_clicked.connect(self._install_single)
            self._dep_cards.append(card)
            self.dep_list_layout.addWidget(card)

        missing = [d for d in deps if not d.installed and not d.is_optional]
        self.install_all_btn.setEnabled(len(missing) > 0)
        self.refresh_btn.setEnabled(True)

        if not missing:
            self.install_all_btn.setText(self._tr("AllInstalled"))
        else:
            self.install_all_btn.setText(
                f"{self._tr('InstallAll')} ({len(missing)} {self._tr('Items')})"
            )

    def _install_single(self, dep):
        """安装单个依赖"""
        dep.installed = False
        commands = build_install_commands(
            [dep], self.mirror_combo.currentText(), self._cuda_tag
        )
        self._run_install(commands)

    def _install_all_missing(self):
        """安装所有缺失依赖"""
        missing = [d for d in self._deps if not d.installed]
        if not missing:
            InfoBar.success(
                self._tr("AllInstalled"),
                self._tr("NothingToInstall"),
                duration=3000,
                parent=self
            )
            return

        commands = build_install_commands(
            missing, self.mirror_combo.currentText(), self._cuda_tag
        )
        self._run_install(commands)

    def _run_install(self, commands):
        """执行安装命令"""
        if not commands:
            return

        self._is_installing = True
        self._set_ui_installing(True)
        self.log_text.clear()

        self._installer = DependencyInstaller(self)
        self._installer.output_signal.connect(self._append_log)
        self._installer.progress_signal.connect(self._update_progress)
        self._installer.finished_signal.connect(self._on_install_finished)
        self._installer.package_started_signal.connect(self._on_package_started)
        self._installer.install(commands)

    def _cancel_install(self):
        """取消安装"""
        if self._installer:
            self._installer.cancel()

    def _append_log(self, text: str):
        """追加日志"""
        self.log_text.appendPlainText(text)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _update_progress(self, value: int):
        """更新进度条"""
        self.progress_bar.setValue(value)

    def _on_package_started(self, name: str):
        """包开始安装"""
        for card in self._dep_cards:
            if card.dep.name == name:
                card.action_btn.setEnabled(False)
                card.action_btn.setText("安装中...")
                break

    def _on_install_finished(self, success: bool, message: str):
        """安装完成"""
        self._is_installing = False
        self._set_ui_installing(False)

        if success:
            InfoBar.success(
                self._tr("InstallSuccess"),
                message,
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self
            )
        else:
            InfoBar.warning(
                self._tr("InstallFailed"),
                message,
                duration=5000,
                position=InfoBarPosition.TOP,
                parent=self
            )

        self._start_detection()

    def _set_ui_installing(self, installing: bool):
        """切换安装/非安装状态 UI"""
        self.install_all_btn.setEnabled(not installing)
        self.refresh_btn.setEnabled(not installing)
        self.mirror_combo.setEnabled(not installing)
        self.cancel_btn.setVisible(installing)
        self.progress_bar.setVisible(installing)
        if installing:
            self.progress_bar.setValue(0)

        for card in self._dep_cards:
            card.action_btn.setEnabled(not installing)

    def _tr(self, key: str) -> str:
        """获取翻译文本，若不存在则返回默认值"""
        try:
            return tr['DependencyInterface'][key]
        except (KeyError, TypeError):
            return _FALLBACK_TR.get(key, key)

    def has_missing_deps(self) -> bool:
        """是否有缺失的必要依赖"""
        return any(not d.installed and not d.is_optional for d in self._deps)


_FALLBACK_TR = {
    "EnvInfo": "环境信息",
    "Detecting": "正在检测 GPU...",
    "DownloadSource": "下载源",
    "MirrorTip": "国内用户建议使用镜像源加速下载",
    "DepList": "依赖组件",
    "CheckingDeps": "正在检测依赖...",
    "Refresh": "刷新检测",
    "Cancel": "取消",
    "InstallAll": "一键安装全部",
    "InstallLog": "安装日志",
    "LogPlaceholder": "安装过程中的日志将在此显示...",
    "AllInstalled": "所有依赖已就绪",
    "NothingToInstall": "无需安装任何依赖",
    "InstallSuccess": "安装成功",
    "InstallFailed": "安装异常",
    "Items": "项",
    "Driver": "驱动版本",
    "Recommend": "推荐",
    "Unknown": "未检测到",
    "NoGpu": "未检测到 NVIDIA GPU",
    "NoGpuHint": "请确认已安装 NVIDIA 驱动和 nvidia-smi",
    "MissingDepTitle": "缺少依赖",
    "MissingDepHint": "部分 AI 依赖未安装，请前往「依赖安装」页面安装",
}
