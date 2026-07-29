; Инсталлятор Video-Dub для Windows (Inno Setup 6).
;
; Внутрь кладём только исходники и лаунчер — Python, ffmpeg и torch приезжают
; при первом запуске через bootstrap.ps1. Собирать всё в один .exe нельзя:
; приложение на ходу создаёт изолированные окружения через `sys.executable -m
; venv`, а внутри замороженной сборки sys.executable указывает на сам .exe.
;
; Сборка:  iscc /DMyAppVersion=0.1.0 packaging\windows\video-dub.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName    "Video-Dub"
#define MyAppExeName "Video-Dub.bat"
#define MyAppPublisher "OneEvil"

[Setup]
AppId={{8E1C6A42-9C0D-4B7E-A3F1-7D2B5E0A9C34}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\..\dist
OutputBaseFilename=Video-Dub-{#MyAppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
; Ставим в Program Files → нужны права администратора
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\packaging\windows\icon.ico

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
; payload\ формирует build_installer.ps1 — там уже отфильтрованы .git, venv'ы и модели
Source: "..\..\dist\payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\packaging\windows\icon.ico"
Name: "{group}\Удалить {#MyAppName}";      Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\packaging\windows\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; Всё, что докачалось после установки — иначе в Program Files остаётся мусор на гигабайты
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.venv-faster-whisper"
Type: filesandordirs; Name: "{app}\.venv-whisperx"
Type: filesandordirs; Name: "{app}\.venv-omnivoice"
Type: filesandordirs; Name: "{app}\.venv-demucs"
Type: filesandordirs; Name: "{app}\.venv-latentsync"
Type: filesandordirs; Name: "{app}\.latentsync"
Type: filesandordirs; Name: "{app}\models"
Type: files;          Name: "{app}\.env"

[Messages]
russian.WelcomeLabel2=Установка [name/ver].%n%nПри первом запуске приложение само скачает Python, ffmpeg и ML-библиотеки — это займёт несколько минут и потребует интернет.
