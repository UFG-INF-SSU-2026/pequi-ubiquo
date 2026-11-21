#define AppName "TowerAI Servidor"
#define AppVersion "4.1.3"

[Setup]
AppId={{C016F406-11CC-43FA-8866-07EF2E1467E0}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=TowerAI
DefaultDirName={autopf}\TowerAI Servidor
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\TowerAI-Servidor.exe
OutputDir=saida
OutputBaseFilename=TowerAI-Servidor-Setup-{#AppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
AllowNoIcons=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "downloadmodels"; Description: "Baixar os pesos de transcrição e ata ao abrir o TowerAI (até 2,8 GB)"; GroupDescription: "Modelos locais"

[Files]
Source: "build\dist\TowerAI-Servidor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "build\llama\*"; DestDir: "{app}\llama"; Flags: ignoreversion
Source: "..\servidor\modelos\object_detection_yolox_2022nov.onnx"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\docs\servidor-windows.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\licencas\*"; DestDir: "{app}\licencas"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\TowerAI-Servidor.exe"

[Run]
Filename: "{app}\TowerAI-Servidor.exe"; Parameters: "{code:StartupOptions}"; Description: "Abrir configuração do TowerAI Servidor"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""TowerAI Servidor (TCP 8443)"" program=""{app}\TowerAI-Servidor.exe"""; Flags: runhidden; Check: IsAdminInstallMode; RunOnceId: "TowerAI8443"

[Code]
function StartupOptions(Param: String): String;
begin
  if WizardIsTaskSelected('downloadmodels') then
    Result := '--preparar-modelos'
  else
    Result := '';
end;
