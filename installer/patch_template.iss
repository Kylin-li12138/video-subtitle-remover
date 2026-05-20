; ============================================================
; Video Subtitle Remover - 补丁更新包 (Inno Setup)
;
; 此文件为模板，由 make_patch.py 自动生成最终 .iss
; 占位符 {{PLACEHOLDER}} 会被替换为实际值
;
; 特性:
;   - 静默安装: /VERYSILENT 无任何弹窗
;   - 自动检测安装目录 (从注册表或命令行 /DIR=)
;   - 仅覆盖源文件，不碰 Python 运行时和依赖
; ============================================================

#define MyAppName "Video Subtitle Remover"
#define MyAppVersion "{{PATCH_VERSION}}"
#define PatchFilesDir "{{PATCH_FILES_DIR}}"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName} Patch
AppVersion={#MyAppVersion}
DefaultDirName={reg:HKCU\Software\Video Subtitle Remover,InstallPath|{autopf}\{#MyAppName}}
; 补丁不需要开始菜单、卸载项
CreateUninstallRegKey=no
UpdateUninstallLogAppName=no
Uninstallable=no
OutputDir={{OUTPUT_DIR}}
OutputBaseFilename=patch-v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 安装前自动关闭正在运行的程序
CloseApplications=force
CloseApplicationsFilter=*.exe

; 默认静默模式
DisableDirPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
; 补丁文件 - 只覆盖到 resources 子目录
; make_patch.py 会在此处插入具体文件列表
{{FILE_ENTRIES}}

[Code]
function GetResourcesDir(Value: String): String;
begin
  Result := ExpandConstant('{app}') + '\resources';
  if not DirExists(Result) then
    Result := ExpandConstant('{app}');
end;

procedure DeletePycacheRecursive(Dir: String);
var
  FindRec: TFindRec;
begin
  if FindFirst(Dir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name = '.') or (FindRec.Name = '..') then
          Continue;
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          if FindRec.Name = '__pycache__' then
            DelTree(Dir + '\' + FindRec.Name, True, True, True)
          else
            DeletePycacheRecursive(Dir + '\' + FindRec.Name);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RegWriteStringValue(HKEY_CURRENT_USER, 'Software\Video Subtitle Remover',
      'AppVersion', '{#MyAppVersion}');
    DeletePycacheRecursive(ExpandConstant('{app}') + '\resources');
  end;
end;
