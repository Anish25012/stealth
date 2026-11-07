; Windows installer. Per-user, so no administrator rights are needed.
; Built by packaging/build.py, which passes AppVersion and SourceDir.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Stealth"
#endif

[Setup]
AppId={{6F2B8D44-1C7E-4E0B-9A53-5D2E0C7B9A11}
AppName=Stealth
AppVersion={#AppVersion}
AppVerName=Stealth {#AppVersion}
AppPublisher=Stealth contributors
AppPublisherURL=https://github.com/Anish25012/stealth
AppSupportURL=https://github.com/Anish25012/stealth/issues
DefaultDirName={localappdata}\Programs\Stealth
DefaultGroupName=Stealth
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Stealth-{#AppVersion}-windows-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Stealth.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Lets the installer close a running Stealth before replacing its files.
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Stealth"; Filename: "{app}\Stealth.exe"
Name: "{userdesktop}\Stealth"; Filename: "{app}\Stealth.exe"; Tasks: desktopicon

[Registry]
; "Start with Windows" is switched on from inside the app (it writes this value). Writing nothing
; here, but marking the value, makes uninstalling remove it.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Stealth"; Flags: uninsdeletevalue

[UninstallRun]
; A running copy holds its files open, so ask it to quit before they're removed.
Filename: "{app}\Stealth.exe"; Parameters: "--quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitStealth"

[Run]
Filename: "{app}\Stealth.exe"; Description: "Open Stealth now"; Flags: nowait postinstall skipifsilent

; Your profiles and evidence live in %LOCALAPPDATA%\Stealth and are never removed by uninstalling.
; Deleting them is a deliberate step inside the app.

[Code]
// Stealth lives in the tray with no window, which Windows' own "close applications" step can't
// close. An upgrade over a running copy would then fail, so ask it to quit first. Older copies
// without --quit reject the argument and exit straight away, which is harmless.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\Stealth.exe')) then
    Exec(ExpandConstant('{app}\Stealth.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

