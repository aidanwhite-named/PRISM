#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef AppGuid
  #define AppGuid "B458E9C0-49CC-482B-92EE-D36B09EA68AF"
#endif

[Setup]
AppId={{{#AppGuid}}
AppName=PRISM
AppVersion={#AppVersion}
AppPublisher=PRISM
AppPublisherURL=https://github.com/aidanwhite-named/PRISM
DefaultDirName={localappdata}\Programs\PRISM
DefaultGroupName=PRISM
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
OutputDir={#SourceDir}\..\..
OutputBaseFilename=PRISM-{#AppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\PRISM-Running
SetupMutex=Local\PRISM-Installer
UninstallDisplayName=PRISM
CloseApplications=no
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "{#SourceDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\실행.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\설치.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\제거.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PRISM"; Filename: "{app}\실행.cmd"; WorkingDir: "{app}"
Name: "{autodesktop}\PRISM"; Filename: "{app}\실행.cmd"; WorkingDir: "{app}"
Name: "{group}\PRISM 제거"; Filename: "{uninstallexe}"

[Code]
var
  SetupFailed: Boolean;
  ExistingInstall: Boolean;

function InitializeSetup(): Boolean;
var Installed: String; InstalledVersion, NewVersion: Int64;
begin
  Result := True;
  ExistingInstall := RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#AppGuid}}_is1', 'DisplayVersion', Installed);
  if ExistingInstall and StrToVersion(Installed, InstalledVersion) and StrToVersion('{#AppVersion}', NewVersion) then
    if ComparePackedVersion(InstalledVersion, NewVersion) > 0 then begin
      SuppressibleMsgBox('더 최신 버전이 설치되어 있습니다. 최신 설치 파일을 사용하세요.', mbError, MB_OK, IDOK);
      Result := False;
    end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer; Args: String;
begin
  if CurStep = ssPostInstall then begin
    Args := '-NoProfile -STA -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\app\scripts\install-window.ps1') + '" -Managed';
    if WizardSilent then begin
      ForceDirectories(ExpandConstant('{app}\app\.setup'));
      Args := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\app\setup.ps1') + '" -OwnershipFile "' + ExpandConstant('{app}\app\.setup\dependencies.json') + '"';
      if ExpandConstant('{param:CLI|all}') = 'skip' then Args := Args + ' -Cli skip';
    end;
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Args,
      '', SW_HIDE, ewWaitUntilTerminated, Code) then Code := 1;
    SetupFailed := Code <> 0;
    if SetupFailed then begin
      WizardForm.FinishedLabel.Caption := '프로그램 파일은 설치했지만 실행 환경 준비에 실패했습니다. 설치.cmd를 실행하여 다시 시도하세요.';
      SuppressibleMsgBox('실행 환경 준비에 실패했습니다. 설치 폴더의 설치.cmd로 다시 시도할 수 있습니다.', mbError, MB_OK, IDOK);
    end;
  end;
end;

function GetCustomSetupExitCode(): Integer;
begin
  Result := 0;
  if SetupFailed then Result := 1;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and SetupFailed then
    WizardForm.FinishedLabel.Caption := '실행 환경 준비에 실패했습니다. 설치 폴더의 설치.cmd로 다시 시도하세요. 기존 데이터는 유지됩니다.';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Code: Integer; Args: String;
begin
  if CurUninstallStep = usUninstall then begin
    Args := '-NoProfile -STA -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\app\scripts\uninstall-cleanup.ps1') + '"';
    if UninstallSilent then Args := Args + ' -NonInteractive';
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Args,
      '', SW_HIDE, ewWaitUntilTerminated, Code) then Code := 1;
    if Code <> 0 then Abort;
  end;
end;
