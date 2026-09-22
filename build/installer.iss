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
;
;  Output is a setup exe plus numbered .bin slices (see DiskSpanning below).
; ==========================================================================

#define AppName        "Automated Tracker"
#define AppExeName     "Automated_Tracker.exe"
#define AppPublisher   "Automated Tracker"
#define SrcRoot        ".."
#define DistDir        "dist\Automated_Tracker"

; version.txt is written by build_app.py from 05 SCRIPT\core\version.py, the
; single source of the version. Resolve it against this script's own folder,
; not the compiler's working directory, and refuse to build without it rather
; than silently stamping a stale number.
#ifexist SourcePath + "\version.txt"
  #define AppVersion Trim(FileRead(FileOpen(SourcePath + "\version.txt")))
#else
  #error version.txt is missing - run build_app.py (or BUILD.bat) first
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
; The payload is several GB and compresses to well over 2 GB. Inno Setup
; refuses to write a single setup exe above 2,097,152,000 bytes, so the data is
; split into slices: a small AutomatedTracker_Setup_<version>.exe next to
; AutomatedTracker_Setup_<version>-1.bin, -2.bin, ... The .bin files must be
; shipped together with the exe - the exe alone cannot install anything.
; Slices are kept under 2 GB because that is GitHub's limit per release asset.
DiskSpanning=yes
DiskSliceSize=1900000000
SlicesPerDisk=1
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
; ffplay.exe is a 228 MB media player the app never runs
Source: "{#SrcRoot}\03 FFMPEG\*"; DestDir: "{app}\03 FFMPEG"; Excludes: "ffplay.exe"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ---- CoTracker weights (the python package itself is inside the exe) --------
Source: "{#SrcRoot}\06 COTRACKER\checkpoints\*"; DestDir: "{app}\06 COTRACKER\checkpoints"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; ---- optional sample footage ------------------------------------------------
Source: "{#SrcRoot}\02 VIDEOS\*"; DestDir: "{app}\02 VIDEOS"; \
    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist; Tasks: sampleclip

[InstallDelete]
; PyInstaller's _internal folder is replaced wholesale on upgrade. Without this,
; modules dropped between releases would linger and shadow the new ones.
Type: filesandordirs; Name: "{app}\_internal"

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

; No [UninstallDelete] for the per-user cache (%LOCALAPPDATA%\AutomatedTracker).
; The uninstaller runs elevated, so {localappdata} resolves to the *elevating*
; account's profile - which is not necessarily the person who used the app, and
; deleting inside another user's profile is exactly what an uninstaller must
; not do. The cache is small and self-limiting anyway: thumbnails are pruned
; at every start and app.log rotates at 2 MB. Users who want it gone delete
; %LOCALAPPDATA%\AutomatedTracker themselves.

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
