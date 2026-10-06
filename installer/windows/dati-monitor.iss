; Instalador Windows do coletor (PROMPT Fase 8 / seção 4.1).
;
; Compile com scripts\build-installer.ps1 (passa a versão, o servidor padrão, os nomes do product.json e
; a pasta dos binários). Uso:
;   setup.exe                                         assistente: chave de cadastro (servidor gravado no
;                                                     build) ou servidor + chave (build sem servidor)
;   setup.exe /VERYSILENT /CODE=ABCD1234 [/SERVER=https://monitor.exemplo.com.br]
;   /INSECUREDEV=1 aceita servidor http:// fora do localhost (somente desenvolvimento)
;   Build com InsecureLan=1 (servidor http:// de rede local): o coletor aceita http:// só para IP de rede
;   privada (--insecure-lan); endereço público continua exigindo HTTPS.
;
; O que faz: recusa Windows 7/8/8.1/Server 2008/2012 com a mensagem do PROMPT; confere o código no
; servidor SEM gastá-lo (dm-agent enroll --check-only) antes de copiar arquivos; instala dm-agent,
; dm-watchdog e dm-tool da arquitetura do PC; cadastra; instala os 2 serviços (início automático com
; atraso e recuperação do SCM 5 s / 5 s / 30 s, zerada em 1 dia — feita pelo próprio "install"); inicia.
; Reinstalar/atualizar mantém o cadastro. O desinstalador remove serviços, programa e a pasta de dados.
;
; Códigos de saída (modo silencioso): 0 ok; 1 recusado/cancelado antes de instalar; 3 arquivos copiados,
; mas cadastro ou serviços falharam (a mensagem fica no log /LOG=...).

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.0.0.0"
#endif
#ifndef AppName
  #define AppName "Dati Monitor"
#endif
#ifndef Slug
  #define Slug "dati-monitor"
#endif
#ifndef ServicePrefix
  #define ServicePrefix "DatiMonitor"
#endif
#ifndef DefaultServer
  #define DefaultServer ""
#endif
#ifndef InsecureLan
  #define InsecureLan "0"
#endif
#ifndef BinDir
  #define BinDir "..\..\dist"
#endif
#ifndef OutDir
  #define OutDir "..\..\dist\installers"
#endif

#define AgentService ServicePrefix + "Agent"
#define WatchdogService ServicePrefix + "Watchdog"

[Setup]
AppId={{7B6E2C1A-4D2F-4E8B-9C35-6A1D2E0F5B71}
AppName={#AppName} — Coletor
AppVersion={#AppVersion}
AppVerName={#AppName} — Coletor {#AppVersion}
AppPublisher=Daticopy
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductName={#AppName}
VersionInfoDescription=Instalador do coletor {#AppName}
DefaultDirName={autopf}\{#ServicePrefix}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=no
UsePreviousAppDir=yes
; A recusa de Windows antigo é nossa (mensagem em português do PROMPT); o mínimo do Inno fica no 7.
MinVersion=6.1
ArchitecturesAllowed=x86compatible x64compatible arm64
ArchitecturesInstallIn64BitMode=x64os arm64
#ifdef TestMode
PrivilegesRequired=lowest
OutputBaseFilename={#Slug}-setup-{#AppVersion}-teste
#else
PrivilegesRequired=admin
OutputBaseFilename={#Slug}-setup-{#AppVersion}
#endif
OutputDir={#OutDir}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=no
RestartIfNeededByRun=no
UninstallDisplayName={#AppName} — Coletor
UninstallDisplayIcon={app}\dm-agent.exe

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
; Binários instalados: só os da arquitetura do PC.
Source: "{#BinDir}\windows-amd64\dm-agent.exe"; DestDir: "{app}"; Check: IsArch('amd64'); Flags: ignoreversion
Source: "{#BinDir}\windows-amd64\dm-watchdog.exe"; DestDir: "{app}"; Check: IsArch('amd64'); Flags: ignoreversion
Source: "{#BinDir}\windows-amd64\dm-tool.exe"; DestDir: "{app}"; Check: IsArch('amd64'); Flags: ignoreversion
Source: "{#BinDir}\windows-arm64\dm-agent.exe"; DestDir: "{app}"; Check: IsArch('arm64'); Flags: ignoreversion
Source: "{#BinDir}\windows-arm64\dm-watchdog.exe"; DestDir: "{app}"; Check: IsArch('arm64'); Flags: ignoreversion
Source: "{#BinDir}\windows-arm64\dm-tool.exe"; DestDir: "{app}"; Check: IsArch('arm64'); Flags: ignoreversion
Source: "{#BinDir}\windows-386\dm-agent.exe"; DestDir: "{app}"; Check: IsArch('386'); Flags: ignoreversion
Source: "{#BinDir}\windows-386\dm-watchdog.exe"; DestDir: "{app}"; Check: IsArch('386'); Flags: ignoreversion
Source: "{#BinDir}\windows-386\dm-tool.exe"; DestDir: "{app}"; Check: IsArch('386'); Flags: ignoreversion
; Cópias para conferir o código antes de instalar (extraídas para a pasta temporária).
Source: "{#BinDir}\windows-amd64\dm-agent.exe"; DestName: "dm-agent-amd64.exe"; Flags: dontcopy
Source: "{#BinDir}\windows-arm64\dm-agent.exe"; DestName: "dm-agent-arm64.exe"; Flags: dontcopy
Source: "{#BinDir}\windows-386\dm-agent.exe"; DestName: "dm-agent-386.exe"; Flags: dontcopy

[UninstallRun]
Filename: "{app}\dm-watchdog.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveWatchdogService"
Filename: "{app}\dm-agent.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveAgentService"

[UninstallDelete]
; Desinstalador limpo: credencial, fila, logs e o que o watchdog guardou para rollback.
Type: filesandordirs; Name: "{commonappdata}\{#ServicePrefix}"
Type: filesandordirs; Name: "{app}"

[Code]
var
  CodePage: TInputQueryWizardPage;
  CheckedServer, CheckedCode: String;
  StepFailed: Boolean;

function BuildArch: String;
begin
  if IsArm64 then
    Result := 'arm64'
  else if IsX64OS then
    Result := 'amd64'
  else
    Result := '386';
end;

function IsArch(Arch: String): Boolean;
begin
  Result := BuildArch = Arch;
end;

function DataDir: String;
begin
  Result := ExpandConstant('{commonappdata}\{#ServicePrefix}');
end;

function AlreadyEnrolled: Boolean;
begin
  Result := FileExists(DataDir + '\config.json');
end;

{ ---------------------------------------------------------------- versão do Windows }

function WindowsName(Major, Minor: Cardinal; Server: Boolean): String;
begin
  Result := 'Windows ' + IntToStr(Major) + '.' + IntToStr(Minor);
  if Server then
  begin
    if (Major = 6) and (Minor = 0) then Result := 'Windows Server 2008';
    if (Major = 6) and (Minor = 1) then Result := 'Windows Server 2008 R2';
    if (Major = 6) and (Minor = 2) then Result := 'Windows Server 2012';
    if (Major = 6) and (Minor = 3) then Result := 'Windows Server 2012 R2';
  end
  else
  begin
    if (Major = 6) and (Minor = 0) then Result := 'Windows Vista';
    if (Major = 6) and (Minor = 1) then Result := 'Windows 7';
    if (Major = 6) and (Minor = 2) then Result := 'Windows 8';
    if (Major = 6) and (Minor = 3) then Result := 'Windows 8.1';
  end;
end;

{ Mesma regra e mesmo texto do agente (osinfo.CheckSupported): Windows 10/11 e Server 2016+ (NT 10.0). }
function UnsupportedMessage(Major, Minor: Cardinal; Server: Boolean): String;
begin
  Result := '';
  if Major < 10 then
    Result := 'Este computador usa ' + WindowsName(Major, Minor, Server) +
      '. Instale o coletor em um PC com Windows 10 ou mais novo na mesma rede.';
end;

{ ---------------------------------------------------------------- execução dos binários }

{ A saída dos dm-* é UTF-8; o Inno lê o arquivo como bytes. }
function Utf8ToString(const S: AnsiString): String;
var
  I, B, C: Integer;
begin
  Result := '';
  I := 1;
  while I <= Length(S) do
  begin
    B := Ord(S[I]);
    if (B >= $C0) and (B < $E0) and (I + 1 <= Length(S)) then
    begin
      C := ((B and $1F) shl 6) or (Ord(S[I + 1]) and $3F);
      I := I + 2;
    end
    else if (B >= $E0) and (B < $F0) and (I + 2 <= Length(S)) then
    begin
      C := ((B and $0F) shl 12) or ((Ord(S[I + 1]) and $3F) shl 6) or (Ord(S[I + 2]) and $3F);
      I := I + 3;
    end
    else
    begin
      C := B;
      I := I + 1;
    end;
    Result := Result + Chr(C);
  end;
end;

{ Executa um binário escondido, guarda a saída (stdout+stderr) e devolve o código de saída. }
function RunTool(const Exe, Params: String; var Output: String): Integer;
var
  LogFile: String;
  Raw: AnsiString;
  Rc: Integer;
begin
  LogFile := ExpandConstant('{tmp}\dm-saida.txt');
  DeleteFile(LogFile);
  Output := '';
  if not Exec(ExpandConstant('{cmd}'), '/C ""' + Exe + '" ' + Params + ' > "' + LogFile + '" 2>&1"', '',
    SW_HIDE, ewWaitUntilTerminated, Rc) then
  begin
    Output := 'não foi possível executar ' + Exe + ': ' + SysErrorMessage(Rc);
    Result := -1;
    Exit;
  end;
  if LoadStringFromFile(LogFile, Raw) then
    Output := Trim(Utf8ToString(Raw));
  Result := Rc;
  Log(ExtractFileName(Exe) + ' ' + Params + ' -> ' + IntToStr(Rc) + ': ' + Output);
end;

function NormalizeCode(S: String): String;
begin
  Result := Uppercase(Trim(S));
  StringChangeEx(Result, ' ', '', True);
  StringChangeEx(Result, '-', '', True);
end;

function DevFlag: String;
begin
  Result := '';
  if ExpandConstant('{param:INSECUREDEV|0}') = '1' then
    Result := ' --insecure-dev'
  else if '{#InsecureLan}' = '1' then
    Result := ' --insecure-lan';
end;

{ Servidor gravado no build (/SERVER= na linha de comando tem prioridade): a tela pede só a chave. }
function ServerFixed: Boolean;
begin
  Result := ExpandConstant('{param:SERVER|{#DefaultServer}}') <> '';
end;

function ServerValue: String;
begin
  if ServerFixed then
    Result := Trim(ExpandConstant('{param:SERVER|{#DefaultServer}}'))
  else
    Result := Trim(CodePage.Values[0]);
end;

function CodeValue: String;
begin
  if ServerFixed then
    Result := NormalizeCode(CodePage.Values[0])
  else
    Result := NormalizeCode(CodePage.Values[1]);
end;

{ Confere o código no servidor sem gastá-lo. }
function CheckCode(const Server, Code: String; var Message: String): Boolean;
var
  Exe: String;
begin
  ExtractTemporaryFile('dm-agent-' + BuildArch + '.exe');
  Exe := ExpandConstant('{tmp}\dm-agent-') + BuildArch + '.exe';
  Result := RunTool(Exe, 'enroll --check-only --server "' + Server + '" --code "' + Code + '"' + DevFlag, Message) = 0;
end;

{ ---------------------------------------------------------------- etapas do assistente }

function InitializeSetup: Boolean;
var
  V: TWindowsVersion;
  Major, Minor: Cardinal;
  Server: Boolean;
  Msg: String;
#ifdef TestMode
  Sim, CheckMsg: String;
  P: Integer;
#endif
begin
  GetWindowsVersionEx(V);
  Major := V.Major;
  Minor := V.Minor;
  Server := V.ProductType <> VER_NT_WORKSTATION;
#ifdef TestMode
  { Só no instalador de teste: simula outra versão (ex.: /SIMULATEOS=6.1 ou /SIMULATEOS=6.3-server). }
  Sim := ExpandConstant('{param:SIMULATEOS|}');
  if Sim <> '' then
  begin
    P := Pos('.', Sim);
    Major := StrToIntDef(Copy(Sim, 1, P - 1), 0);
    Minor := StrToIntDef(Copy(Sim, P + 1, 1), 0);
    Server := Pos('server', Lowercase(Sim)) > 0;
  end;
#endif
  Msg := UnsupportedMessage(Major, Minor, Server);
  if Msg <> '' then
  begin
    Log('RECUSADO: ' + Msg);
    SuppressibleMsgBox(Msg, mbCriticalError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  Log('Sistema suportado: ' + IntToStr(Major) + '.' + IntToStr(Minor) + '; arquitetura ' + BuildArch);
#ifdef TestMode
  { Também confere um código, para o teste automatizado da comunicação instalador -> servidor. }
  if ExpandConstant('{param:CODE|}') <> '' then
  begin
    if CheckCode(ExpandConstant('{param:SERVER|}'), NormalizeCode(ExpandConstant('{param:CODE|}')), CheckMsg) then
      Log('CODIGO OK: ' + CheckMsg)
    else
      Log('CODIGO RECUSADO: ' + CheckMsg);
  end;
  Log('MODO DE TESTE: nada é instalado.');
  Result := False;
  Exit;
#endif
  Result := True;
end;

procedure InitializeWizard;
begin
  if ServerFixed then
  begin
    CodePage := CreateInputQueryPage(wpWelcome, 'Cadastro do coletor',
      'Chave de cadastro',
      'Servidor: ' + ExpandConstant('{param:SERVER|{#DefaultServer}}') + #13#10#13#10 +
      'Informe a chave de cadastro (8 caracteres) gerada no portal em Coletores → Novo coletor. ' +
      'Ela vale por 7 dias e será conferida agora, sem ser usada: o cadastro só acontece no fim da instalação.');
    CodePage.Add('Chave de cadastro:', False);
    CodePage.Values[0] := ExpandConstant('{param:CODE|}');
  end
  else
  begin
    CodePage := CreateInputQueryPage(wpWelcome, 'Cadastro do coletor',
      'Endereço do servidor e chave de cadastro',
      'No portal: Coletores → Novo coletor. A chave tem 8 caracteres e vale por 7 dias. ' +
      'Ela será conferida agora, sem ser usada: o cadastro só acontece no fim da instalação.');
    CodePage.Add('Endereço do servidor:', False);
    CodePage.Add('Chave de cadastro:', False);
    CodePage.Values[0] := ExpandConstant('{param:SERVER|}');
    CodePage.Values[1] := ExpandConstant('{param:CODE|}');
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Reinstalação/atualização: o PC já está cadastrado e nenhum código novo foi passado. }
  Result := (PageID = CodePage.ID) and AlreadyEnrolled and (ExpandConstant('{param:CODE|}') = '');
end;

function ValidateCodePage: Boolean;
var
  Server, Code, Msg: String;
begin
  Server := ServerValue;
  Code := CodeValue;
  Result := False;
  if (Pos('https://', Lowercase(Server)) <> 1) and (Pos('http://', Lowercase(Server)) <> 1) then
  begin
    SuppressibleMsgBox('Informe o endereço do servidor começando com https://', mbError, MB_OK, IDOK);
    Exit;
  end;
  if Length(Code) <> 8 then
  begin
    SuppressibleMsgBox('A chave de cadastro tem 8 caracteres (letras e números).', mbError, MB_OK, IDOK);
    Exit;
  end;
  if not CheckCode(Server, Code, Msg) then
  begin
    SuppressibleMsgBox('O servidor recusou a chave:' + #13#10#13#10 + Msg, mbError, MB_OK, IDOK);
    Exit;
  end;
  CheckedServer := Server;
  CheckedCode := Code;
  if not WizardSilent then
    MsgBox(Msg, mbInformation, MB_OK);
  Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = CodePage.ID then
    Result := ValidateCodePage;
end;

{ Modo silencioso: as páginas não aparecem, então a conferência acontece aqui. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Saida: String;
begin
  Result := '';
  if (CheckedCode = '') and not ShouldSkipPage(CodePage.ID) then
  begin
    if not ValidateCodePage then
    begin
      Result := 'Cadastro do coletor não confirmado: confira /SERVER e /CODE.';
      Exit;
    end;
  end;
  { Atualização: os serviços em execução seguram os arquivos. }
  if FileExists(ExpandConstant('{app}\dm-watchdog.exe')) then
    RunTool(ExpandConstant('{app}\dm-watchdog.exe'), 'stop', Saida);
  if FileExists(ExpandConstant('{app}\dm-agent.exe')) then
    RunTool(ExpandConstant('{app}\dm-agent.exe'), 'stop', Saida);
end;

function ServiceExists(const Name: String): Boolean;
var
  Rc: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\sc.exe'), 'query ' + Name, '', SW_HIDE, ewWaitUntilTerminated, Rc) and (Rc = 0);
end;

procedure Step(const Title, Exe, Params: String);
var
  Saida: String;
begin
  if StepFailed then
    Exit;
  if RunTool(ExpandConstant('{app}\') + Exe, Params, Saida) <> 0 then
  begin
    StepFailed := True;
    Log('FALHOU: ' + Title + ': ' + Saida);
    SuppressibleMsgBox('Falha ao ' + Title + ':' + #13#10#13#10 + Saida + #13#10#13#10 +
      'Os arquivos foram instalados em ' + ExpandConstant('{app}') + '. Corrija o problema e rode o instalador de novo.',
      mbCriticalError, MB_OK, IDOK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Force: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
  StepFailed := False;
  if CheckedCode <> '' then
  begin
    Force := '';
    if AlreadyEnrolled then
      Force := ' --force';
    Step('cadastrar o coletor', 'dm-agent.exe',
      'enroll --server "' + CheckedServer + '" --code "' + CheckedCode + '"' + Force + DevFlag);
  end;
  if not ServiceExists('{#AgentService}') then
    Step('instalar o serviço do coletor', 'dm-agent.exe', 'install');
  if not ServiceExists('{#WatchdogService}') then
    Step('instalar o serviço do watchdog', 'dm-watchdog.exe', 'install');
  Step('iniciar o coletor', 'dm-agent.exe', 'start');
  Step('iniciar o watchdog', 'dm-watchdog.exe', 'start');
  if not StepFailed then
    Log('Instalação concluída: serviços {#AgentService} e {#WatchdogService} em execução.');
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if StepFailed then
    Result := 3;
end;
