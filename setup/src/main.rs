// XynMacro Setup: shows the branded install window and runs the real NSIS installer silently
// underneath, so installs, upgrades, the uninstaller and the app's auto-updater all stay on
// the one installer Tauri builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::State;
use windows_sys::Win32::System::Threading::{GetProcessIoCounters, IO_COUNTERS};
use winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};
use winreg::RegKey;

const PAYLOAD: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/payload.exe"));
const INSTALL_FILES: &str = env!("XYN_INSTALL_FILES");
const VERSION: &str = env!("CARGO_PKG_VERSION");
const UNINSTALL_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\XynMacro";
const APP_EXE: &str = "XynMacroApp.exe";
const SIDECAR_EXE: &str = "XynMacro-core.exe";
const WEBVIEW2_KEY: &str = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Serialize)]
struct Installed {
    version: Option<String>,
    dir: String,
}

#[derive(Serialize)]
struct SetupInfo {
    version: &'static str,
    default_dir: String,
    installed: Option<Installed>,
    files: Vec<(String, u64)>,
}

#[derive(Clone, Serialize)]
struct Progress {
    phase: &'static str, // idle, prepare, uninstall, copy, done, failed
    written: u64,
    error: Option<String>,
}

type Shared = Arc<Mutex<Progress>>;

fn default_dir() -> PathBuf {
    let local = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_default();
    local.join("XynMacro")
}

fn installed() -> Option<Installed> {
    let key = RegKey::predef(HKEY_CURRENT_USER).open_subkey(UNINSTALL_KEY).ok()?;
    let location: String = key.get_value("InstallLocation").ok()?;
    let dir = location.trim_matches('"').to_string();
    if !Path::new(&dir).is_dir() {
        return None;
    }
    Some(Installed { version: key.get_value("DisplayVersion").ok(), dir })
}

fn install_files() -> Vec<(String, u64)> {
    INSTALL_FILES
        .split(';')
        .filter_map(|entry| {
            let (name, bytes) = entry.split_once('|')?;
            Some((name.to_string(), bytes.parse().ok()?))
        })
        .collect()
}

fn webview2_present() -> bool {
    let version = |root, path: &str| -> Option<String> {
        RegKey::predef(root).open_subkey(path).ok()?.get_value("pv").ok()
    };
    [
        version(HKEY_LOCAL_MACHINE, &format!(r"SOFTWARE\WOW6432Node\{WEBVIEW2_KEY}")),
        version(HKEY_LOCAL_MACHINE, &format!(r"SOFTWARE\{WEBVIEW2_KEY}")),
        version(HKEY_CURRENT_USER, &format!(r"Software\{WEBVIEW2_KEY}")),
    ]
    .into_iter()
    .flatten()
    .any(|v| !v.is_empty() && v != "0.0.0.0")
}

fn write_payload() -> Result<PathBuf, String> {
    if PAYLOAD.is_empty() {
        return Err("This setup was built without the installer inside.".into());
    }
    let path = std::env::temp_dir().join(format!("XynMacro_{VERSION}_x64-setup-{}.exe", std::process::id()));
    std::fs::write(&path, PAYLOAD).map_err(|e| format!("Couldn't unpack the installer ({e})."))?;
    Ok(path)
}

fn stop_running_app() {
    for exe in [APP_EXE, SIDECAR_EXE] {
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/IM", exe])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }
}

fn set(progress: &Shared, phase: &'static str, written: u64) {
    let mut p = progress.lock().unwrap();
    p.phase = phase;
    p.written = written;
}

fn fail(progress: &Shared, message: String) {
    let mut p = progress.lock().unwrap();
    p.phase = "failed";
    p.error = Some(message);
}

fn run_install(progress: Shared, dir: String, desktop: bool, start_menu: bool, uninstall_first: bool) {
    set(&progress, "prepare", 0);
    stop_running_app();
    let payload = match write_payload() {
        Ok(path) => path,
        Err(e) => return fail(&progress, e),
    };

    if uninstall_first {
        if let Some(old) = installed() {
            set(&progress, "uninstall", 0);
            // _?= keeps the uninstaller in place so we can wait for it; /S keeps app data.
            let uninstaller = Path::new(&old.dir).join("uninstall.exe");
            let status = Command::new(&uninstaller)
                .arg("/S")
                .raw_arg(format!("_?={}", old.dir))
                .status();
            if !matches!(status, Ok(s) if s.success()) {
                let _ = std::fs::remove_file(&payload);
                return fail(&progress, "The old version's uninstaller didn't finish.".into());
            }
            let _ = std::fs::remove_file(&uninstaller);
        }
    }

    set(&progress, "copy", 0);
    let mut command = Command::new(&payload);
    command.arg("/S");
    if !desktop {
        command.arg("/XYNNODESKTOP");
    }
    if !start_menu {
        command.arg("/XYNNOSTARTMENU");
    }
    // NSIS wants /D= last and unquoted, even with spaces in the path.
    command.raw_arg(format!("/D={dir}"));
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(e) => {
            let _ = std::fs::remove_file(&payload);
            return fail(&progress, format!("Couldn't start the installer ({e})."));
        }
    };

    // NSIS gives no progress when silent, so count the bytes it has written to disk.
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) => {}
            Err(e) => break Err(e),
        }
        let mut io: IO_COUNTERS = unsafe { std::mem::zeroed() };
        if unsafe { GetProcessIoCounters(child.as_raw_handle() as _, &mut io) } != 0 {
            set(&progress, "copy", io.WriteTransferCount);
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    let _ = std::fs::remove_file(&payload);

    match status {
        Ok(s) if s.success() => set(&progress, "done", 0),
        Ok(s) => fail(&progress, format!("The installer stopped with code {}.", s.code().unwrap_or(-1))),
        Err(e) => fail(&progress, format!("Lost track of the installer ({e}).")),
    }
}

#[tauri::command]
fn setup_info() -> SetupInfo {
    SetupInfo {
        version: VERSION,
        default_dir: default_dir().to_string_lossy().into_owned(),
        installed: installed(),
        files: install_files(),
    }
}

#[tauri::command]
fn start_install(
    progress: State<'_, Shared>,
    dir: String,
    desktop: bool,
    start_menu: bool,
    uninstall_first: bool,
) -> Result<(), String> {
    let dir = dir.trim().trim_matches('"').to_string();
    if dir.is_empty() || !Path::new(&dir).is_absolute() {
        return Err("Pick a full install folder path.".into());
    }
    {
        let mut p = progress.lock().unwrap();
        if matches!(p.phase, "prepare" | "uninstall" | "copy") {
            return Err("Setup is already running.".into());
        }
        *p = Progress { phase: "prepare", written: 0, error: None };
    }
    let shared = progress.inner().clone();
    std::thread::spawn(move || run_install(shared, dir, desktop, start_menu, uninstall_first));
    Ok(())
}

#[tauri::command]
fn install_progress(progress: State<'_, Shared>) -> Progress {
    progress.lock().unwrap().clone()
}

#[tauri::command]
fn launch_app(dir: String) -> Result<(), String> {
    let exe = Path::new(&dir).join(APP_EXE);
    Command::new(&exe).current_dir(&dir).spawn().map(|_| ()).map_err(|e| e.to_string())
}

/// Only for PCs without WebView2: the NSIS installer's own pages install WebView2 too.
fn run_classic_installer() -> Result<(), String> {
    let payload = write_payload()?;
    Command::new(payload).spawn().map(|_| ()).map_err(|e| e.to_string())
}

fn main() {
    // The setup window needs WebView2; without it, hand over to the classic installer,
    // which installs WebView2 itself.
    if !webview2_present() {
        let _ = run_classic_installer();
        return;
    }
    let progress: Shared = Arc::new(Mutex::new(Progress { phase: "idle", written: 0, error: None }));
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(progress)
        .invoke_handler(tauri::generate_handler![
            setup_info,
            start_install,
            install_progress,
            launch_app
        ])
        .run(tauri::generate_context!())
        .expect("error while running XynMacro Setup");
}
