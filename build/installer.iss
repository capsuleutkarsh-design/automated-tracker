; ==========================================================================
;  Automated Tracker - Inno Setup script
;
;  Packages the frozen application (build\dist\Automated_Tracker) together
;  with the external tools it shells out to: COLMAP, FFmpeg and the CoTracker
;  checkpoints. The exe is installed at the root of the install folder, beside
;  those tool folders, which is exactly the layout core\app_paths.py expects
;  when frozen.
;
;  Compiled by BUILD.bat, or manually:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
; ==========================================================================

#define AppName        "Automated Tracker"
#define AppExeName     "Automated_Tracker.exe"
#define AppPublisher   "Automated Tracker"
#define SrcRoot        ".."
#define DistDir        "dist\Automated_Tracker"

; version.txt is written by build_app.py; fall back if it is not there yet
#ifexist "version.txt"
  #define AppVersion Trim(FileRead(FileOpen("version.txt")))
#else
  #define AppVersion "1.1.0"
#endif

[Setup]
AppId={{8E5A1C34-6B72-4E19-9C2D-7F4A0B3D5E61}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AutomatedTracker_Setup_{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The payload is several GB - warn rather than fail late
DiskSpanning=no
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
; Installing into Program Files needs elevation
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "sampleclip";  Description: "Install the sample clip into 02 VIDEOS"; GroupDescription: "Optional:"; Flags: unchecked

[Files]
; ---- the frozen application -------------------------------------------------
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; ---- external tools the app runs -------------------------------------------
Source: "{#SrcRoot}\01 COLMAP\*"; DestDir: "{app}\01 COLMAP"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SrcRoot}\03 FFMPEG\*"; DestDir: "{app}\03 FFMPEG"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ---- CoTracker weights (the python package itself is inside the exe) --------
Source: "{#SrcRoot}\06 COTRACKER\checkpoints\*"; DestDir: "{app}\06 COTRACKER\checkpoints"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ---- optional sample footage ------------------------------------------------
Source: "{#SrcRoot}\02 VIDEOS\*"; DestDir: "{app}\02 VIDEOS"; \
    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Tasks: sampleclip

[Dirs]
; Working folders the app writes into. Users are not admins at run time, so
; these need to stay writable after an install into Program Files.
Name: "{app}\02 VIDEOS"; Permissions: users-modify
Name: "{app}\04 SCENES"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExeName}"
Name: "{group}\Media folder";      Filename: "{app}\02 VIDEOS"
Name: "{group}\Output folder";     Filename: "{app}\04 SCENES"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Generated caches - leave the user's own scenes and media alone
Type: filesandordirs; Name: "{localappdata}\AutomatedTracker"

[Code]
function InitializeSetup(): Boolean;
var
  FreeMB, TotalMB: Cardinal;
begin
  Result := True;
  { The payload is large; check before unpacking gigabytes and failing. }
  if GetSpaceOnDisk(ExpandConstant('{autopf}'), True, FreeMB, TotalMB) then
  begin
    if FreeMB < 9000 then
    begin
      if MsgBox('This install needs roughly 9 GB free, but only ' +
                IntToStr(FreeMB) + ' MB is available on the target drive.' + #13#10 +
                'Continue anyway?', mbConfirmation, MB_YESNO) = IDNO then
        Result := False;
    end;
  end;
end;
