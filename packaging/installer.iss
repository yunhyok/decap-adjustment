#ifndef AppVersion
  #error AppVersion must be supplied by build.ps1
#endif
#ifndef SourceRoot
  #define SourceRoot ".."
#endif

[Setup]
AppId={{EA39DB72-3DEB-4D60-813E-BDE33AE92D66}
AppName=Decap Adjustment
AppVersion={#AppVersion}
AppPublisher=Decap Adjustment contributors
DefaultDirName={localappdata}\Programs\Decap Adjustment
DefaultGroupName=Decap Adjustment
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#SourceRoot}\outputs
OutputBaseFilename=DecapAdjustment-{#AppVersion}-Setup-x64
SetupIconFile={#SourceRoot}\assets\app.ico
UninstallDisplayIcon={app}\DecapAdjustment.exe
LicenseFile={#SourceRoot}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
Uninstallable=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceRoot}\work\dist\DecapAdjustment\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Decap Adjustment"; Filename: "{app}\DecapAdjustment.exe"; WorkingDir: "{app}"; IconFilename: "{app}\DecapAdjustment.exe"; AppUserModelID: "DecapAdjustment.Desktop.1"
Name: "{autodesktop}\Decap Adjustment"; Filename: "{app}\DecapAdjustment.exe"; WorkingDir: "{app}"; IconFilename: "{app}\DecapAdjustment.exe"; Tasks: desktopicon; AppUserModelID: "DecapAdjustment.Desktop.1"

[Run]
Filename: "{app}\DecapAdjustment.exe"; Description: "{cm:LaunchProgram,Decap Adjustment}"; Flags: nowait postinstall skipifsilent
