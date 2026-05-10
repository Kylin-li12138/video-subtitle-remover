from PySide6 import QtWidgets
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QInputDialog
from PySide6.QtCore import Signal
from qfluentwidgets import (PushButton, FluentIcon, CardWidget,
                          ComboBoxSettingCard, SwitchSettingCard,
                          ComboBox, BodyLabel, InfoBar, MessageBox)
from backend.config import config, tr, HARDWARD_ACCELERATION_OPTION
from backend.tools.constant import InpaintMode, SubtitleDetectMode
from backend.tools.template_manager import (
    load_all_templates, save_template, delete_template,
    get_template, get_template_names
)


def _tr_template(key: str) -> str:
    """获取模板相关翻译"""
    try:
        return tr['Template'][key]
    except (KeyError, TypeError):
        return _TEMPLATE_FALLBACK.get(key, key)


_TEMPLATE_FALLBACK = {
    "Title": "去水印模板",
    "SelectTemplate": "选择模板",
    "NoTemplate": "无 (手动选区)",
    "Save": "保存",
    "Delete": "删除",
    "SaveDialogTitle": "保存模板",
    "SaveDialogLabel": "请输入模板名称:",
    "SaveSuccess": "模板已保存",
    "SaveFailed": "保存失败",
    "DeleteConfirmTitle": "删除模板",
    "DeleteConfirmContent": "确定要删除模板「{}」吗？",
    "DeleteSuccess": "模板已删除",
    "NoSelectionToSave": "当前没有选区，请先在视频预览中框选水印区域",
    "TemplateApplied": "已应用模板: {}",
}


class SettingInterface(QtWidgets.QVBoxLayout):

    template_applied = Signal(list)

    def __init__(self, parent):
        super().__init__()
        self._parent_widget = parent
        self.setContentsMargins(16, 16, 16, 16)
        
        # 界面语言设置
        self.interface_combo = ComboBoxSettingCard(
            configItem=config.interface,
            icon=FluentIcon.LANGUAGE,
            title=tr["SubtitleExtractorGUI"]["InterfaceLanguage"],
            content="",
            parent=parent,
            texts=config.intefaceTexts.keys(),
        )
        self.addWidget(self.interface_combo)
        
        # 处理模式设置
        self.inpaint_mode_combo = ComboBoxSettingCard(
            configItem=config.inpaintMode,
            icon=FluentIcon.GLOBE,
            title=tr["SubtitleExtractorGUI"]["InpaintMode"],
            content="",
            parent=parent,
            texts=[list(tr['InpaintMode'].values())[i] for i,_ in enumerate(config.inpaintMode.validator.options)],
        )
        self.inpaint_mode_combo.setToolTip(tr["SubtitleExtractorGUI"]["InpaintModeDesc"])
        self.addWidget(self.inpaint_mode_combo)

        self.subtitle_detect_model_combo = ComboBoxSettingCard(
            configItem=config.subtitleDetectMode,
            icon=FluentIcon.SEARCH,
            title=tr["SubtitleExtractorGUI"]["SubtitleDetectMode"],
            content="",
            parent=parent,
            texts=[list(tr['SubtitleDetectMode'].values())[i] for i,_ in enumerate(config.subtitleDetectMode.validator.options)],
        )
        self.addWidget(self.subtitle_detect_model_combo)

        # 是否启用硬件加速
        self.hardware_acceleration = SwitchSettingCard(
            configItem=config.hardwareAcceleration,
            icon=FluentIcon.SPEED_HIGH, 
            title=tr["Setting"]["HardwareAcceleration"],
            content=tr["Setting"]["HardwareAccelerationDesc"],
            parent=parent
        )
        self.addWidget(self.hardware_acceleration)
        if not HARDWARD_ACCELERATION_OPTION:
            self.hardware_acceleration.switchButton.setChecked(False)
            self.hardware_acceleration.switchButton.setEnabled(False)
            self.hardware_acceleration.setContent(tr["Setting"]["HardwareAccelerationNO"])
            config.set(config.hardwareAcceleration, False)

        # 去水印模板区域
        self._create_template_section(parent)

        self.addStretch(1)
    
    def _create_template_section(self, parent):
        """创建去水印模板选择区域"""
        template_card = CardWidget(parent)
        template_layout = QVBoxLayout(template_card)
        template_layout.setContentsMargins(16, 12, 16, 12)
        template_layout.setSpacing(8)

        title_label = BodyLabel(_tr_template("Title"), template_card)
        template_layout.addWidget(title_label)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(8)

        self.template_combo = ComboBox(template_card)
        self.template_combo.setMinimumWidth(140)
        self._refresh_template_list()
        self.template_combo.currentTextChanged.connect(self._on_template_selected)
        combo_row.addWidget(self.template_combo, 1)

        self.template_save_btn = PushButton(FluentIcon.SAVE, _tr_template("Save"), template_card)
        self.template_save_btn.setFixedWidth(70)
        self.template_save_btn.clicked.connect(self._save_template)
        combo_row.addWidget(self.template_save_btn)

        self.template_delete_btn = PushButton(FluentIcon.DELETE, _tr_template("Delete"), template_card)
        self.template_delete_btn.setFixedWidth(70)
        self.template_delete_btn.clicked.connect(self._delete_template)
        combo_row.addWidget(self.template_delete_btn)

        template_layout.addLayout(combo_row)
        self.addWidget(template_card)

    def _refresh_template_list(self):
        """刷新模板下拉列表"""
        current = self.template_combo.currentText()
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem(_tr_template("NoTemplate"))
        names = get_template_names()
        for name in names:
            self.template_combo.addItem(name)
        if current and current in names:
            self.template_combo.setCurrentText(current)
        else:
            self.template_combo.setCurrentIndex(0)
        self.template_combo.blockSignals(False)

    def _on_template_selected(self, text: str):
        """模板选择变更"""
        no_template_text = _tr_template("NoTemplate")
        if text == no_template_text or not text:
            return
        areas = get_template(text)
        if areas:
            self.template_applied.emit(areas)

    def _save_template(self):
        """保存当前选区为模板"""
        home = self._find_home_interface()
        if home is None:
            return

        selections = home.video_display_component.selection_rects
        if not selections:
            InfoBar.warning(
                _tr_template("SaveFailed"),
                _tr_template("NoSelectionToSave"),
                duration=3000,
                parent=self._parent_widget
            )
            return

        name, ok = QInputDialog.getText(
            self._parent_widget,
            _tr_template("SaveDialogTitle"),
            _tr_template("SaveDialogLabel")
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if save_template(name, selections):
            self._refresh_template_list()
            self.template_combo.setCurrentText(name)
            InfoBar.success(
                _tr_template("SaveSuccess"),
                name,
                duration=3000,
                parent=self._parent_widget
            )

    def _delete_template(self):
        """删除当前选中的模板"""
        no_template_text = _tr_template("NoTemplate")
        current = self.template_combo.currentText()
        if current == no_template_text or not current:
            return

        w = MessageBox(
            _tr_template("DeleteConfirmTitle"),
            _tr_template("DeleteConfirmContent").format(current),
            self._parent_widget
        )
        if w.exec():
            if delete_template(current):
                self._refresh_template_list()
                InfoBar.success(
                    _tr_template("DeleteSuccess"),
                    current,
                    duration=3000,
                    parent=self._parent_widget
                )

    def _find_home_interface(self):
        """查找 HomeInterface 实例"""
        widget = self._parent_widget
        while widget is not None:
            if hasattr(widget, 'video_display_component'):
                return widget
            widget = widget.parent() if hasattr(widget, 'parent') else None
        return None

    def set_inpaint_mode_enabled(self, enabled):
        """启用或禁用 inpaint 模式下拉框"""
        self.inpaint_mode_combo.comboBox.setEnabled(enabled)

    def reset_setting(self):
        """重置所有设置为默认值"""
        pass