; ============================================================
; Video Subtitle Remover - 完整安装程序 (Inno Setup)
;
; 使用方法:
;   1. 先用 QPT 打包生成 Release 目录
;   2. 修改下方 #define 指向实际输出路径
;   3. 用 ISCC.exe 编译本脚本
;
; 命令行编译:
;   "C:\Users\kylin_li\AppData\Local\Programs\Inno Setup 6\ISCC.exe" setup.iss
; ============================================================

#define MyAppName "Video Subtitle Remover"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "VSR Team"
#define MyAppURL "https://github.com/Kylin-li12138/video-subtitle-remover"
#define MyAppExeName "启动程序.exe"

; QPT 打包输出的 Release 目录 (编译前请确认路径)
#define QPTReleaseDir "E:\Documents\Document\New project 4\vsr_out_lite\Release"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist\installer
OutputBaseFilename=vsr-setup-v{#MyAppVersion}
SetupIconFile=..\design\vsr.ico
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; 静默安装支持: /VERYSILENT /SUPPRESSMSGBOXES
DisableDirPage=no
DisableProgramGroupPage=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 复制 QPT Release 目录，排除不需要的文件以减小体积和加速编译
Source: "{#QPTReleaseDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__,*.pyc,\Python\Lib\idlelib\*,\Python\Lib\test\*,\Python\Lib\unittest\*,\Python\DLLs\tcl*.dll,\Python\DLLs\tk*.dll,\Python\DLLs\_tkinter.pyd"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Registry]
; 写入安装路径供补丁更新定位
Root: HKCU; Subkey: "Software\{#MyAppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
