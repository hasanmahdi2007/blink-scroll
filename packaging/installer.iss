; Inno Setup script for the Blink Scroll installer.
; Build: first PyInstaller (packaging\blinkscroll.spec -> dist\BlinkScroll), then
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\installer.iss
; Output: dist\BlinkScroll-Setup-<version>.exe

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif

[Setup]
AppId={{6B0E7C1A-3F5D-4C2B-9A8E-2D7F4B1C9E35}
AppName=Blink Scroll
AppVersion={#AppVersion}
AppVerName=Blink Scroll {#AppVersion}
AppPublisher=hasanmahdi2007
AppPublisherURL=https://github.com/hasanmahdi2007/blink-scroll
AppSupportURL=https://github.com/hasanmahdi2007/blink-scroll/issues
; Per-user install: no administrator prompt, installs to %LOCALAPPDATA%\Programs\Blink Scroll.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\Blink Scroll
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=BlinkScroll-Setup-{#AppVersion}
SetupIconFile=blinkscroll.ico
UninstallDisplayIcon={app}\BlinkScroll.exe
UninstallDisplayName=Blink Scroll
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The app's single-instance mutex: setup asks to close a running copy before upgrading.
AppMutex=BlinkScroll.SingleInstance
CloseApplications=force
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"
Name: "autostart"; Description: "&Start Blink Scroll automatically when Windows starts"

[Files]
Source: "..\dist\BlinkScroll\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Blink Scroll"; Filename: "{app}\BlinkScroll.exe"; Comment: "Hands-free scrolling with your eyes"
Name: "{autoprograms}\Blink Scroll (tuning session)"; Filename: "{app}\BlinkScroll.exe"; Parameters: "--tune"; Comment: "Guided 3-minute recording to tune Blink Scroll to your eyes"
Name: "{autodesktop}\Blink Scroll"; Filename: "{app}\BlinkScroll.exe"; Tasks: desktopicon
; Same name/folder the app's own "Start with Windows" switch uses, so the two stay in sync.
Name: "{userstartup}\Blink Scroll"; Filename: "{app}\BlinkScroll.exe"; WorkingDir: "{app}"; Tasks: autostart

[Run]
Filename: "{app}\BlinkScroll.exe"; Description: "Start Blink Scroll now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im BlinkScroll.exe"; Flags: runhidden; RunOnceId: "StopBlinkScroll"

[UninstallDelete]
; The app may have created this itself via its "Start with Windows" switch.
Type: files; Name: "{userstartup}\Blink Scroll.lnk"
