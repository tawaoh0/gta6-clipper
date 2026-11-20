; Installateur Windows (Inno Setup 6) : iscc packaging\windows.iss
#define AppName "Grand Clip Auto"
#ifndef AppVersion
  #define AppVersion "0.9.2"
#endif

[Setup]
AppId={{6A1E2C34-8F4B-4E7A-9D2B-C1A9F0E5B7D3}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Grand-Clip-Auto-windows-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{group}\Désinstaller {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent
