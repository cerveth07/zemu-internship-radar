Unicode true
RequestExecutionLevel user
ManifestDPIAware true
Name "择木 · 实习工作台（轻量版）"
OutFile "Zemu-v1.0-Windows-x64-Lite-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\ZemuLite"
Icon "radar.ico"
UninstallIcon "radar.ico"
SetCompressor /SOLID lzma
SetCompressorDictSize 16

!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ZemuInternshipRadarLite"
!define MUI_ICON "radar.ico"
!define MUI_UNICON "radar.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\Zemu.exe"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

VIProductVersion "1.0.0.0"
VIAddVersionKey /LANG=2052 "ProductName" "择木 · 实习工作台（轻量版）"
VIAddVersionKey /LANG=2052 "ProductVersion" "1.0"
VIAddVersionKey /LANG=2052 "FileVersion" "1.0"
VIAddVersionKey /LANG=2052 "FileDescription" "择木轻量版 Windows x64 安装程序"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright (c) 2026 cerveth07"

!macro CheckClosed
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\InternshipRadarPrivateRCDesktop") p.r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    MessageBox MB_OK|MB_ICONINFORMATION "请先关闭正在运行的择木窗口，然后重新打开安装或卸载程序。" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
!macroend

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此版本需要 64 位 Windows。" /SD IDOK
    SetErrorLevel 3
    Abort
  ${EndIf}
  SetRegView 64
  !insertmacro CheckClosed
FunctionEnd

Function un.onInit
  SetRegView 64
  !insertmacro CheckClosed
FunctionEnd

Section "择木"
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "appdist/Zemu/*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\择木"
  CreateShortcut "$SMPROGRAMS\择木\择木轻量版.lnk" "$INSTDIR\Zemu.exe"
  WriteRegStr HKCU "${REGKEY}" "DisplayName" "择木 · 实习工作台（轻量版）"
  WriteRegStr HKCU "${REGKEY}" "DisplayVersion" "1.0"
  WriteRegStr HKCU "${REGKEY}" "Publisher" "cerveth07"
  WriteRegStr HKCU "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon" "$INSTDIR\Zemu.exe"
  WriteRegStr HKCU "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${REGKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  !include "remove-installed-files.nsh"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\择木\择木轻量版.lnk"
  RMDir "$SMPROGRAMS\择木"
  DeleteRegKey HKCU "${REGKEY}"
SectionEnd
