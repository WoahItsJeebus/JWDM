#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "JWDM"
#define MyAppPublisher "WoahItsJeebus"
#define MyAppExeName "JWDM.exe"

[Setup]
AppId={{E9D26B21-7104-45FA-81F4-57E29F6B5FC4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/WoahItsJeebus/JWDM
AppSupportURL=https://github.com/WoahItsJeebus/JWDM/issues
AppUpdatesURL=https://github.com/WoahItsJeebus/JWDM/releases
DefaultDirName={localappdata}\Programs\JWDM
DefaultGroupName=JWDM
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=JWDM-Setup-{#MyAppVersion}-x64
SetupIconFile=..\assets\JWDM.ico
UninstallDisplayIcon={app}\JWDM.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
#ifdef JWDMSigningEnabled
SignTool=jwdm
SignedUninstaller=yes
#else
SignedUninstaller=no
#endif

[Files]
Source: "..\dist\release\JWDM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\JWDM"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\JWDM"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch JWDM"; Flags: nowait postinstall skipifsilent
