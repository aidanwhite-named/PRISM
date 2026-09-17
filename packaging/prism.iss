#ifndef AppVersion
  #define AppVersion "2.0.0"
#endif
[Setup]
AppId={{DB0B3E7C-7345-4EBB-90CF-15B981B104FD}
AppName=PRISM
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\PRISM
DefaultGroupName=PRISM
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release
OutputBaseFilename=PRISM-Setup-{#AppVersion}-windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\PRISM.exe
CloseApplications=yes
RestartApplications=no

[Files]
Source: "..\dist\PRISM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PRISM"; Filename: "{app}\PRISM.exe"; WorkingDir: "{app}"
Name: "{group}\Stop PRISM"; Filename: "{app}\PRISM.exe"; Parameters: "--stop"; WorkingDir: "{app}"
Name: "{group}\Uninstall PRISM"; Filename: "{uninstallexe}"

[UninstallRun]
Filename: "{app}\PRISM.exe"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopPRISM"

; User data under LocalAppData\PRISM is deliberately retained on uninstall.
