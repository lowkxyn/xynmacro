; XynMacro installer theme, built to match installer-mockup: dark window, logo + step list
; sidebar on every page, one Options page (folder + shortcuts), dark buttons.
; Included by installer.nsi before its pages. Install/upgrade logic is untouched, and silent
; or passive updater installs skip every page here.

!define XYN_BG 0D0D10
!define XYN_PANEL 15151A
!define XYN_TEXT E2E3E7
!define XYN_MUTED 9A9DA4
!define XYN_FIELD 191920
!define XYN_ACCENT 7EB39A
!define XYN_DIR "${__FILEDIR__}"

!define MUI_BGCOLOR ${XYN_BG}
!define MUI_TEXTCOLOR ${XYN_TEXT}
!define MUI_INSTFILESPAGE_COLORS "6F727C 0A0A0D"
!define MUI_INSTFILESPAGE_PROGRESSBAR smooth
!define MUI_LICENSEPAGE_BGCOLOR ${XYN_BG}
!define MUI_HEADER_TRANSPARENT_TEXT
!define MUI_CUSTOMFUNCTION_GUIINIT XynThemeGuiInit

Var XynOverlayNext
Var XynOverlayBack
Var XynOverlayCancel
Var XynOptionsSeen
Var XynDesktop        ; 1 = make a desktop shortcut (GUI installs; passive/silent keep Tauri's rule)
Var XynNoStartMenu    ; 1 = user unticked the Start menu shortcut
Var XynNoDesktop      ; 1 = /XYNNODESKTOP from the branded setup app (silent installs)
Var XynDirField
Var XynDesktopBox
Var XynStartMenuBox
Var XynSidebarImage
Var XynTitleFont

; Convert dialog units to pixels for DLG and move/size HWND there.
!macro XYN_PLACE HWND DLG X Y W H
  System::Call '*(i ${X}, i ${Y}, i ${W}, i ${H}) p .r9'
  System::Call 'user32::MapDialogRect(p ${DLG}, p r9)'
  System::Call '*$9(i .r5, i .r6, i .r7, i .r8)'
  System::Free $9
  System::Call 'user32::SetWindowPos(p ${HWND}, p 0, i r5, i r6, i r7, i r8, i 0x14)'
!macroend

; Rect of HWND in its parent's client coordinates: $1 parent, $4 left, $5 top, $6 width, $7 height.
!macro XYN_CHILD_RECT HWND
  System::Call '*(i,i,i,i) p .r3'
  System::Call 'user32::GetWindowRect(p ${HWND}, p r3)'
  System::Call 'user32::GetParent(p ${HWND}) p .r1'
  System::Call 'user32::MapWindowPoints(p 0, p r1, p r3, i 2)'
  System::Call '*$3(i .r4, i .r5, i .r6, i .r7)'
  System::Free $3
  IntOp $6 $6 - $4
  IntOp $7 $7 - $5
!macroend

; Windows draws buttons light grey whatever colours we set, so a bitmap of the mockup's
; button sits on top of each one. The bitmap static has no SS_NOTIFY, so clicks fall
; through to the real button underneath; WS_CLIPSIBLINGS stops that button painting over it.
!macro XYN_OVERLAY BTN OUT
  !insertmacro XYN_CHILD_RECT ${BTN}
  System::Call 'user32::GetWindowLongW(p ${BTN}, i -16) i .r8'
  IntOp $8 $8 | 0x04000000
  System::Call 'user32::SetWindowLongW(p ${BTN}, i -16, i r8)'
  System::Call 'user32::CreateWindowExW(i 0, w "STATIC", w "", i 0x5000004E, i r4, i r5, i r6, i r7, p r1, p 0, p 0, p 0) p .r8'
  StrCpy ${OUT} $8
  System::Call 'user32::SetWindowPos(p r8, p 0, i 0, i 0, i 0, i 0, i 0x13)'
!macroend

; Load $PLUGINSDIR\<name>.bmp at the size of a bitmap static. Stack: name, hwnd.
Function XynLoadBitmap
  Exch $0   ; hwnd
  Exch
  Exch $1   ; name
  Push $2
  Push $3
  Push $4
  System::Call '*(i,i,i,i) p .r4'
  System::Call 'user32::GetClientRect(p r0, p r4)'
  System::Call '*$4(i, i, i .r2, i .r3)'
  System::Free $4
  System::Call 'user32::LoadImageW(p 0, w "$PLUGINSDIR\$1.bmp", i 0, i r2, i r3, i 0x10) p .r2'
  SendMessage $0 0x172 0 $2   ; STM_SETIMAGE
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

!macro XYN_BUTTON ID OVERLAY IMAGE
  GetDlgItem $1 $HWNDPARENT ${ID}
  !if "${IMAGE}" == ""
    ShowWindow $1 ${SW_HIDE}
    ShowWindow ${OVERLAY} ${SW_HIDE}
  !else
    ShowWindow $1 ${SW_SHOW}
    Push "${IMAGE}"
    Push ${OVERLAY}
    Call XynLoadBitmap
    ShowWindow ${OVERLAY} ${SW_SHOW}
  !endif
!macroend

; Which dark buttons a page shows ("" hides that button).
!macro XYN_BUTTONS NEXT BACK CANCEL
  !insertmacro XYN_BUTTON 1 $XynOverlayNext "${NEXT}"
  !insertmacro XYN_BUTTON 3 $XynOverlayBack "${BACK}"
  !insertmacro XYN_BUTTON 2 $XynOverlayCancel "${CANCEL}"
!macroend

; Themed checkboxes/radios draw their own text dark whatever colour we set. Keep the box,
; move the caption into a clickable label beside it that clicks the box.
!macro XYN_BUDDY_LABEL HWND
  System::Call 'user32::GetWindowTextW(p ${HWND}, w .r2, i ${NSIS_MAX_STRLEN})'
  !insertmacro XYN_CHILD_RECT ${HWND}
  SendMessage ${HWND} ${WM_SETTEXT} 0 "STR:"
  System::Call 'user32::GetSystemMetrics(i 71) i .r8'   ; SM_CXMENUCHECK
  IntOp $8 $8 + 8
  System::Call 'user32::SetWindowPos(p ${HWND}, p 0, i 0, i 0, i r8, i r7, i 0x16)'
  IntOp $4 $4 + $8
  IntOp $6 $6 - $8
  nsDialogs::CreateControl STATIC 0x50000300 0 $4 $5 $6 $7 $2   ; SS_NOTIFY|SS_CENTERIMAGE
  Pop $0
  SendMessage ${HWND} ${WM_GETFONT} 0 0 $8
  SendMessage $0 ${WM_SETFONT} $8 1
  SetCtlColors $0 ${XYN_TEXT} ${XYN_BG}
  System::Call 'user32::SetPropW(p r0, w "xynbox", p ${HWND})'
  ${NSD_OnClick} $0 XynBuddyClick
!macroend

Function XynBuddyClick
  Pop $0
  System::Call 'user32::GetPropW(p r0, w "xynbox") p .r0'
  SendMessage $0 ${BM_CLICK} 0 0
FunctionEnd

Function XynThemeGuiInit
  SetCtlColors $HWNDPARENT ${XYN_TEXT} ${XYN_BG}
  GetDlgItem $0 $HWNDPARENT 1034   ; header background
  SetCtlColors $0 ${XYN_TEXT} ${XYN_PANEL}
  GetDlgItem $0 $HWNDPARENT 1037   ; header title
  SetCtlColors $0 ${XYN_TEXT} transparent
  GetDlgItem $0 $HWNDPARENT 1038   ; header subtitle
  SetCtlColors $0 ${XYN_MUTED} transparent
  GetDlgItem $0 $HWNDPARENT 1028   ; branding text ("Nullsoft Install System")
  SetCtlColors $0 ${XYN_BG} ${XYN_BG}
  GetDlgItem $0 $HWNDPARENT 1256   ; branding text shadow
  SetCtlColors $0 ${XYN_BG} ${XYN_BG}
  GetDlgItem $0 $HWNDPARENT 1035   ; header separator
  SetCtlColors $0 ${XYN_PANEL} ${XYN_PANEL}
  GetDlgItem $0 $HWNDPARENT 1045   ; footer separator
  SetCtlColors $0 ${XYN_PANEL} ${XYN_PANEL}

  InitPluginsDir
  File "/oname=$PLUGINSDIR\step1.bmp" "${XYN_DIR}\step1.bmp"
  File "/oname=$PLUGINSDIR\step2.bmp" "${XYN_DIR}\step2.bmp"
  File "/oname=$PLUGINSDIR\step3.bmp" "${XYN_DIR}\step3.bmp"
  File "/oname=$PLUGINSDIR\step4.bmp" "${XYN_DIR}\step4.bmp"
  File "/oname=$PLUGINSDIR\btn_next.bmp" "${XYN_DIR}\btn_next.bmp"
  File "/oname=$PLUGINSDIR\btn_install.bmp" "${XYN_DIR}\btn_install.bmp"
  File "/oname=$PLUGINSDIR\btn_finish.bmp" "${XYN_DIR}\btn_finish.bmp"
  File "/oname=$PLUGINSDIR\btn_back.bmp" "${XYN_DIR}\btn_back.bmp"
  File "/oname=$PLUGINSDIR\btn_cancel.bmp" "${XYN_DIR}\btn_cancel.bmp"
  File "/oname=$PLUGINSDIR\btn_cancel_off.bmp" "${XYN_DIR}\btn_cancel_off.bmp"
  File "/oname=$PLUGINSDIR\btn_browse.bmp" "${XYN_DIR}\btn_browse.bmp"
  GetDlgItem $0 $HWNDPARENT 1
  !insertmacro XYN_OVERLAY $0 $XynOverlayNext
  GetDlgItem $0 $HWNDPARENT 3
  !insertmacro XYN_OVERLAY $0 $XynOverlayBack
  GetDlgItem $0 $HWNDPARENT 2
  !insertmacro XYN_OVERLAY $0 $XynOverlayCancel
  CreateFont $XynTitleFont "$(^Font)" "12" "700"
FunctionEnd

; MUI's full-window helpers hide the header text but leave its background strip and line,
; which then sat over our full-window pages. SW_HIDE (0) or SW_SHOW (5).
!macro XYN_HEADER_STRIP SHOW
  GetDlgItem $1 $HWNDPARENT 1034
  ShowWindow $1 ${SHOW}
  GetDlgItem $1 $HWNDPARENT 1035
  ShowWindow $1 ${SHOW}
  GetDlgItem $1 $HWNDPARENT 1036
  ShowWindow $1 ${SHOW}
!macroend

; Generic dark page: paint the page dialog and every child control dark.
Function XynDarkPage
  FindWindow $1 "#32770" "" $HWNDPARENT
  SetCtlColors $1 ${XYN_TEXT} ${XYN_BG}
  StrCpy $2 0
  xyn_next_control:
    FindWindow $2 "" "" $1 $2
    StrCmp $2 0 xyn_done
    SetCtlColors $2 ${XYN_TEXT} ${XYN_BG}
    Goto xyn_next_control
  xyn_done:
FunctionEnd

; 1. Welcome: version tag under the title. Expanded after MUI_PAGE_WELCOME, which declares
; $mui.WelcomePage.*: !insertmacro XYN_WELCOME_PAGE_FUNCTION.
!macro XYN_WELCOME_PAGE_FUNCTION
  Function XynWelcomePage
    !insertmacro XYN_PLACE $mui.WelcomePage.Title $mui.WelcomePage 120 10 195 16
    ${NSD_CreateLabel} 120u 28u 195u 10u "v${VERSION}"
    Pop $0
    SetCtlColors $0 ${XYN_ACCENT} ${XYN_BG}
    SetCtlColors $mui.WelcomePage.Text ${XYN_MUTED} ${XYN_BG}
    !insertmacro XYN_BUTTONS btn_next "" btn_cancel
  FunctionEnd
!macroend

; Upgrade page (only when XynMacro is already installed). installer.nsi creates it as a
; full-window (1044) page; this lays it out like the others: sidebar, title, text, choices.
!macro XYN_REINSTALL_PAGE PAGE TEXT RADIO1 RADIO2
  Call XynDarkPage
  ${NSD_CreateBitmap} 0u 0u 109u 193u ""
  Pop $0
  Push step1
  Push $0
  Call XynLoadBitmap
  ${NSD_CreateLabel} 120u 10u 195u 16u "Already installed"
  Pop $0
  SendMessage $0 ${WM_SETFONT} $XynTitleFont 0
  SetCtlColors $0 ${XYN_TEXT} ${XYN_BG}
  !insertmacro XYN_PLACE ${TEXT} ${PAGE} 120 34 195 40
  SetCtlColors ${TEXT} ${XYN_MUTED} ${XYN_BG}
  !insertmacro XYN_PLACE ${RADIO1} ${PAGE} 120 80 195 12
  !insertmacro XYN_PLACE ${RADIO2} ${PAGE} 120 96 195 12
  !insertmacro XYN_BUDDY_LABEL ${RADIO1}
  !insertmacro XYN_BUDDY_LABEL ${RADIO2}
  Call muiPageLoadFullWindow
  !insertmacro XYN_HEADER_STRIP ${SW_HIDE}
  !insertmacro XYN_BUTTONS btn_next btn_back btn_cancel
!macroend

!macro XYN_REINSTALL_PAGE_END
  !insertmacro XYN_HEADER_STRIP ${SW_SHOW}
  Call muiPageUnloadFullWindow
!macroend

; 2. Options: install folder + shortcuts on one full-window page (replaces MUI's directory page
; and the finish page's desktop-shortcut box).
Function XynOptionsPage
  Call SkipIfPassive
  ${If} $XynOptionsSeen != 1
    StrCpy $XynDesktop 1
    StrCpy $XynNoStartMenu 0
  ${EndIf}

  nsDialogs::Create 1044
  Pop $0
  SetCtlColors $0 "" ${XYN_BG}

  ${NSD_CreateBitmap} 0u 0u 109u 193u ""
  Pop $1
  ${NSD_SetStretchedImage} $1 "$PLUGINSDIR\step2.bmp" $XynSidebarImage

  ${NSD_CreateLabel} 120u 10u 195u 16u "Options"
  Pop $1
  SendMessage $1 ${WM_SETFONT} $XynTitleFont 0
  SetCtlColors $1 ${XYN_TEXT} ${XYN_BG}

  ${NSD_CreateLabel} 120u 34u 195u 10u "Install folder"
  Pop $1
  SetCtlColors $1 ${XYN_MUTED} ${XYN_BG}

  ${NSD_CreateText} 120u 46u 146u 13u "$INSTDIR"
  Pop $XynDirField
  SetCtlColors $XynDirField ${XYN_TEXT} ${XYN_FIELD}
  System::Call 'user32::GetWindowLongW(p $XynDirField, i -20) i .r2'
  IntOp $2 $2 & 0xFFFDFDFF   ; no WS_EX_CLIENTEDGE / WS_EX_STATICEDGE: the theme drew it light
  System::Call 'user32::SetWindowLongW(p $XynDirField, i -20, i r2)'
  System::Call 'user32::SetWindowPos(p $XynDirField, p 0, i 0, i 0, i 0, i 0, i 0x37)'   ; FRAMECHANGED

  ${NSD_CreateButton} 270u 45u 45u 15u "Browse"
  Pop $1
  ${NSD_OnClick} $1 XynBrowse
  !insertmacro XYN_OVERLAY $1 $2
  Push btn_browse
  Push $2
  Call XynLoadBitmap

  ${NSD_CreateCheckbox} 120u 70u 195u 12u "Desktop shortcut"
  Pop $XynDesktopBox
  SetCtlColors $XynDesktopBox ${XYN_TEXT} ${XYN_BG}
  ${IfThen} $XynDesktop = 1 ${|} ${NSD_Check} $XynDesktopBox ${|}
  !insertmacro XYN_BUDDY_LABEL $XynDesktopBox

  ${NSD_CreateCheckbox} 120u 84u 195u 12u "Start menu shortcut"
  Pop $XynStartMenuBox
  SetCtlColors $XynStartMenuBox ${XYN_TEXT} ${XYN_BG}
  ${IfThen} $XynNoStartMenu != 1 ${|} ${NSD_Check} $XynStartMenuBox ${|}
  !insertmacro XYN_BUDDY_LABEL $XynStartMenuBox

  Call muiPageLoadFullWindow
  !insertmacro XYN_HEADER_STRIP ${SW_HIDE}
  !insertmacro XYN_BUTTONS btn_install btn_back btn_cancel
  nsDialogs::Show
  !insertmacro XYN_HEADER_STRIP ${SW_SHOW}
  Call muiPageUnloadFullWindow
  ${NSD_FreeImage} $XynSidebarImage
FunctionEnd

Function XynBrowse
  Pop $0
  ${NSD_GetText} $XynDirField $1
  nsDialogs::SelectFolderDialog "Choose where to install XynMacro" "$1"
  Pop $1
  ${If} $1 != error
    ${GetFileName} "$1" $2
    ${If} $2 != "XynMacro"
      StrCpy $1 "$1\XynMacro"
    ${EndIf}
    ${NSD_SetText} $XynDirField "$1"
  ${EndIf}
FunctionEnd

Function XynOptionsLeave
  ${NSD_GetText} $XynDirField $0
  StrCpy $1 $0 1 1
  ${If} $1 != ":"
    StrCpy $1 $0 2
    ${If} $1 != "\\"
      MessageBox MB_ICONEXCLAMATION "Pick a full folder path, like C:\Users\you\AppData\Local\XynMacro."
      Abort
    ${EndIf}
  ${EndIf}
  StrCpy $INSTDIR $0
  ${NSD_GetState} $XynDesktopBox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $XynDesktop 1
  ${Else}
    StrCpy $XynDesktop 0
  ${EndIf}
  ${NSD_GetState} $XynStartMenuBox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $XynNoStartMenu 0
  ${Else}
    StrCpy $XynNoStartMenu 1
  ${EndIf}
  StrCpy $XynOptionsSeen 1
FunctionEnd

; 3. Installing: stretch NSIS's progress page to full window with the sidebar, like the mockup.
Function XynInstFilesPage
  Call muiPageLoadFullWindow
  !insertmacro XYN_HEADER_STRIP ${SW_HIDE}
  FindWindow $0 "#32770" "" $HWNDPARENT
  GetDlgItem $1 $HWNDPARENT 1044
  !insertmacro XYN_CHILD_RECT $1
  System::Call 'user32::SetWindowPos(p r0, p 0, i r4, i r5, i r6, i r7, i 0x14)'
  SetCtlColors $0 ${XYN_TEXT} ${XYN_BG}

  System::Call 'user32::CreateWindowExW(i 0, w "STATIC", w "", i 0x5000004E, i 0, i 0, i 1, i 1, p r0, p 0, p 0, p 0) p .r1'
  !insertmacro XYN_PLACE $1 $0 0 0 109 193
  Push step3
  Push $1
  Call XynLoadBitmap

  System::Call 'user32::CreateWindowExW(i 0, w "STATIC", w "Installing...", i 0x50000000, i 0, i 0, i 1, i 1, p r0, p 0, p 0, p 0) p .r1'
  !insertmacro XYN_PLACE $1 $0 120 10 195 16
  SendMessage $1 ${WM_SETFONT} $XynTitleFont 0
  SetCtlColors $1 ${XYN_TEXT} ${XYN_BG}

  GetDlgItem $1 $0 1006   ; status line
  !insertmacro XYN_PLACE $1 $0 120 32 195 10
  SetCtlColors $1 ${XYN_MUTED} ${XYN_BG}

  GetDlgItem $1 $0 1004   ; progress bar: drop the Windows theme so the colours apply, flat
  System::Call 'uxtheme::SetWindowTheme(p r1, w "", w "")'
  System::Call 'user32::GetWindowLongW(p r1, i -20) i .r2'
  IntOp $2 $2 & 0xFFFDFDFF   ; no WS_EX_STATICEDGE / WS_EX_CLIENTEDGE
  System::Call 'user32::SetWindowLongW(p r1, i -20, i r2)'
  SendMessage $1 0x0409 0 0xC8BBB4   ; PBM_SETBARCOLOR (BGR of #B4BBC8)
  SendMessage $1 0x2001 0 0x201919   ; PBM_SETBKCOLOR (#191920)
  !insertmacro XYN_PLACE $1 $0 120 46 195 5

  GetDlgItem $1 $0 1016   ; details log: flat, dark scrollbars, one column as wide as the box
  System::Call 'uxtheme::SetWindowTheme(p r1, w "DarkMode_Explorer", p 0)'
  System::Call 'user32::GetWindowLongW(p r1, i -20) i .r2'
  IntOp $2 $2 & 0xFFFDFDFF
  System::Call 'user32::SetWindowLongW(p r1, i -20, i r2)'
  !insertmacro XYN_PLACE $1 $0 120 58 195 120
  SendMessage $1 0x101E 0 -2   ; LVM_SETCOLUMNWIDTH, LVSCW_AUTOSIZE_USEHEADER
  GetDlgItem $1 $0 1027   ; "Show details" button
  ShowWindow $1 ${SW_HIDE}

  !insertmacro XYN_BUTTONS "" "" btn_cancel_off
FunctionEnd

; If the install fails the page stays up; give back the real buttons so Close/Cancel work.
Function .onInstFailed
  ShowWindow $XynOverlayNext ${SW_HIDE}
  ShowWindow $XynOverlayBack ${SW_HIDE}
  ShowWindow $XynOverlayCancel ${SW_HIDE}
  GetDlgItem $0 $HWNDPARENT 1
  ShowWindow $0 ${SW_SHOW}
  GetDlgItem $0 $HWNDPARENT 2
  ShowWindow $0 ${SW_SHOW}
FunctionEnd

; 4. Finish. MUI declares $mui.FinishPage.* in MUI_PAGE_FINISH, after this file is included,
; so this function is expanded there: !insertmacro XYN_FINISH_PAGE_FUNCTION.
!macro XYN_FINISH_PAGE_FUNCTION
  Function XynFinishPage
    Push step4
    Push $mui.FinishPage.Image
    Call XynLoadBitmap
    !insertmacro XYN_BUDDY_LABEL $mui.FinishPage.Run
    !insertmacro XYN_BUTTONS btn_finish "" ""
    GetDlgItem $0 $HWNDPARENT 1
    System::Call 'user32::SetFocus(p r0)'   ; keep the focus box off the checkbox
  FunctionEnd
!macroend
