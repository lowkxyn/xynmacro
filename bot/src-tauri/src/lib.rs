use std::fmt::Write as _;
use std::fs;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{
    AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, PhysicalSize, Position, Size,
};
use tauri_plugin_updater::UpdaterExt;

/// The bundled Python sidecar's executable name. Used to spawn the owned child
/// and verify its installed file is unlocked before an updater replaces it.
const SIDECAR_EXE: &str = "XynMacro-core.exe";
const BACKEND_AUTH_HEADER: &str = "X-XynMacro-Token";

struct PythonProcess(Mutex<Option<Child>>);
struct BackendPort(Mutex<u16>);
struct BackendAuthToken(String);
/// A downloaded-but-not-installed update: kept until the user restarts (or the
/// window closes), so updating never interrupts a running session.
struct PendingUpdate(Mutex<Option<(tauri_plugin_updater::Update, Vec<u8>)>>);
struct ShutdownState {
    started: AtomicBool,
    launcher_pid: u32,
}
/// Size of the expanded window the last time we entered compact (restored on uncompact).
struct PreCompactSize(Mutex<Option<(f64, f64)>>);
/// Size of the pill the last time we left compact (restored on next compact).
struct LastCompactSize(Mutex<Option<(f64, f64)>>);
/// Resize-animation bookkeeping. `gen` cancels superseded animation threads
/// (rapid compact↔expand toggling used to leave two threads fighting over
/// set_size). `target` is the in-flight animation's destination, so a toggle
/// mid-animation can save the intended size instead of a half-animated one.
struct WindowAnim {
    gen: AtomicU64,
    target: Mutex<Option<(f64, f64)>>,
}

struct WindowPrefsState {
    compact_or_animating: AtomicBool,
    save_generation: AtomicU64,
}

/// True only for the first frontend load after macro_config.json was deleted.
/// This lets one visible config deletion reset WebView localStorage as well as
/// the backend and native window bounds.
struct ConfigResetState(AtomicBool);

/// Default pill height when there's no remembered compact size yet.
const DEFAULT_COMPACT_HEIGHT: f64 = 32.0;
const DEFAULT_EXPANDED_WIDTH: f64 = 1100.0;
const DEFAULT_EXPANDED_HEIGHT: f64 = 720.0;

#[derive(Clone, Debug, Deserialize, Serialize)]
struct SavedWindowBounds {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

#[derive(Default, Deserialize, Serialize)]
struct AppPrefsFile {
    window: Option<SavedWindowBounds>,
}

fn app_prefs_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|dir| dir.join("app_prefs.json"))
        .map_err(|error| error.to_string())
}

fn write_saved_window_bounds(app: &AppHandle, bounds: SavedWindowBounds) -> Result<(), String> {
    let path = app_prefs_path(app)?;
    let parent = path.parent().ok_or("App config directory is unavailable")?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let payload = serde_json::to_vec_pretty(&AppPrefsFile {
        window: Some(bounds),
    })
    .map_err(|error| error.to_string())?;
    fs::write(path, payload).map_err(|error| error.to_string())
}

fn capture_expanded_window_bounds(app: &AppHandle) -> Result<SavedWindowBounds, String> {
    let win = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    if win.is_maximized().unwrap_or(false) || win.is_minimized().unwrap_or(false) {
        return Err("window is not in a restorable state".into());
    }
    let position = win.outer_position().map_err(|error| error.to_string())?;
    let size = win.inner_size().map_err(|error| error.to_string())?;
    if size.height < 200 || size.width < 400 {
        return Err("compact window bounds are not persisted as expanded bounds".into());
    }
    Ok(SavedWindowBounds {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
    })
}

fn schedule_window_bounds_save(app: AppHandle) {
    let Some(state) = app.try_state::<WindowPrefsState>() else {
        return;
    };
    if state.compact_or_animating.load(Ordering::SeqCst) {
        return;
    }
    let generation = state.save_generation.fetch_add(1, Ordering::SeqCst) + 1;
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(250));
        let Some(state) = app.try_state::<WindowPrefsState>() else {
            return;
        };
        if state.compact_or_animating.load(Ordering::SeqCst)
            || state.save_generation.load(Ordering::SeqCst) != generation
        {
            return;
        }
        if let Ok(bounds) = capture_expanded_window_bounds(&app) {
            let _ = write_saved_window_bounds(&app, bounds);
        }
    });
}

fn restore_saved_window_bounds(app: &AppHandle) {
    let Ok(path) = app_prefs_path(app) else {
        return;
    };
    let Ok(payload) = fs::read(path) else {
        return;
    };
    let Ok(prefs) = serde_json::from_slice::<AppPrefsFile>(&payload) else {
        return;
    };
    let Some(saved) = prefs.window else {
        return;
    };
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let Ok(monitors) = win.available_monitors() else {
        return;
    };
    if monitors.is_empty() {
        return;
    }

    let saved_right = saved.x.saturating_add(saved.width as i32);
    let saved_bottom = saved.y.saturating_add(saved.height as i32);
    let monitor = monitors
        .iter()
        .max_by_key(|monitor| {
            let position = monitor.position();
            let size = monitor.size();
            let right = position.x.saturating_add(size.width as i32);
            let bottom = position.y.saturating_add(size.height as i32);
            let overlap_width = (saved_right.min(right) - saved.x.max(position.x)).max(0);
            let overlap_height = (saved_bottom.min(bottom) - saved.y.max(position.y)).max(0);
            i64::from(overlap_width) * i64::from(overlap_height)
        })
        .unwrap_or(&monitors[0]);
    let monitor_position = monitor.position();
    let monitor_size = monitor.size();
    let width = saved.width.clamp(800, monitor_size.width.max(800));
    let height = saved.height.clamp(480, monitor_size.height.max(480));
    let max_x = monitor_position
        .x
        .saturating_add(monitor_size.width.saturating_sub(width) as i32);
    let max_y = monitor_position
        .y
        .saturating_add(monitor_size.height.saturating_sub(height) as i32);
    let x = saved.x.clamp(monitor_position.x, max_x);
    let y = saved.y.clamp(monitor_position.y, max_y);
    let _ = win.set_size(Size::Physical(PhysicalSize::new(width, height)));
    let _ = win.set_position(Position::Physical(PhysicalPosition::new(x, y)));
}

#[tauri::command]
fn factory_reset_app_prefs(app: AppHandle) -> Result<(), String> {
    if let Ok(path) = app_prefs_path(&app) {
        if let Err(error) = fs::remove_file(path) {
            if error.kind() != std::io::ErrorKind::NotFound {
                return Err(error.to_string());
            }
        }
    }
    let win = app
        .get_webview_window("main")
        .ok_or("main window not found")?;
    if let Some(state) = app.try_state::<WindowPrefsState>() {
        state.compact_or_animating.store(false, Ordering::SeqCst);
        state.save_generation.fetch_add(1, Ordering::SeqCst);
    }
    if let Some(state) = app.try_state::<PreCompactSize>() {
        if let Ok(mut saved) = state.0.lock() {
            *saved = None;
        }
    }
    if let Some(state) = app.try_state::<LastCompactSize>() {
        if let Ok(mut saved) = state.0.lock() {
            *saved = None;
        }
    }
    if win.is_maximized().unwrap_or(false) {
        win.unmaximize().map_err(|error| error.to_string())?;
    }
    win.set_always_on_top(false)
        .map_err(|error| error.to_string())?;
    win.set_size(Size::Logical(LogicalSize::new(
        DEFAULT_EXPANDED_WIDTH,
        DEFAULT_EXPANDED_HEIGHT,
    )))
    .map_err(|error| error.to_string())?;
    win.center().map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn take_config_reset_flag(app: AppHandle) -> bool {
    app.try_state::<ConfigResetState>()
        .map(|state| state.0.swap(false, Ordering::SeqCst))
        .unwrap_or(false)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateInfo {
    current_version: String,
    version: String,
    date: Option<String>,
    body: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateDownloadProgress {
    chunk_length: usize,
    downloaded: u64,
    total: Option<u64>,
}

#[tauri::command]
async fn check_update(app: AppHandle) -> Result<Option<UpdateInfo>, String> {
    let update = app
        .updater()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?;

    Ok(update.map(|update| UpdateInfo {
        current_version: update.current_version.clone(),
        version: update.version.clone(),
        date: update.date.as_ref().map(ToString::to_string),
        body: update.body.clone(),
    }))
}

/// Downloads the available update and parks it in PendingUpdate; nothing is
/// installed yet. Returns the downloaded version, or None if no update exists.
#[tauri::command]
async fn download_update(app: AppHandle) -> Result<Option<String>, String> {
    let Some(update) = app
        .updater()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?
    else {
        return Ok(None);
    };

    let progress_app = app.clone();
    let finished_app = app.clone();
    let mut downloaded = 0_u64;
    let bytes = update
        .download(
            move |chunk_length, total| {
                downloaded = downloaded.saturating_add(chunk_length as u64);
                let _ = progress_app.emit(
                    "update-download-progress",
                    UpdateDownloadProgress {
                        chunk_length,
                        downloaded,
                        total,
                    },
                );
            },
            move || {
                let _ = finished_app.emit("update-download-finished", ());
            },
        )
        .await
        .map_err(|error| error.to_string())?;

    let version = update.version.clone();
    if let Some(state) = app.try_state::<PendingUpdate>() {
        if let Ok(mut guard) = state.0.lock() {
            *guard = Some((update, bytes));
        }
    }
    Ok(Some(version))
}

fn take_pending_update(app: &AppHandle) -> Option<(tauri_plugin_updater::Update, Vec<u8>)> {
    app.try_state::<PendingUpdate>()
        .and_then(|state| state.0.lock().ok().and_then(|mut guard| guard.take()))
}

/// Installs the parked update now. On Windows the plugin hands off to the NSIS
/// installer and exits this process; NSIS relaunches the app when it finishes.
#[tauri::command]
fn install_pending_update(app: AppHandle) -> bool {
    if !has_pending_update(&app) {
        return false;
    }
    request_shutdown(app, true)
}

/// One shared keep-alive client for all proxy traffic. The UI polls /state and
/// /logs several times a second; a fresh Client per call meant a fresh TCP
/// connection each time. No explicit timeout, matching the old Client::new().
fn http_client() -> &'static reqwest::blocking::Client {
    static CLIENT: OnceLock<reqwest::blocking::Client> = OnceLock::new();
    // Without a timeout a stalled sidecar leaves the WebView `await`ing forever, so a
    // click looks like it did nothing instead of reporting an error.
    CLIENT.get_or_init(|| {
        reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .expect("building the loopback HTTP client cannot fail")
    })
}

/// Where the Python sidecar script + read-only assets live.
/// Dev: sibling `python/` next to the cargo manifest.
/// Prod: bundled under the Tauri resource dir.
#[allow(dead_code)] // used only by the dev (debug) data_dir / spawn_sidecar branches
fn python_dir(app: &AppHandle) -> PathBuf {
    #[cfg(debug_assertions)]
    {
        let _ = app;
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        manifest
            .parent()
            .map(|p| p.join("python"))
            .unwrap_or_else(|| PathBuf::from("python"))
    }
    #[cfg(not(debug_assertions))]
    {
        app.path()
            .resource_dir()
            .map(|p| p.join("python"))
            .unwrap_or_else(|_| PathBuf::from("python"))
    }
}

/// Writable runtime dir for port file, config, saved logs.
/// Dev: same as python_dir (so the existing flow Just Works).
/// Prod: app-data dir (e.g. %LOCALAPPDATA%/com.htcgc.xyn/). Created on demand.
fn data_dir(app: &AppHandle) -> PathBuf {
    #[cfg(debug_assertions)]
    {
        python_dir(app)
    }
    #[cfg(not(debug_assertions))]
    {
        let dir = app
            .path()
            .app_data_dir()
            .unwrap_or_else(|_| PathBuf::from("."));
        let _ = fs::create_dir_all(&dir);
        dir
    }
}

fn port_file_path(app: &AppHandle, launcher_pid: u32) -> PathBuf {
    data_dir(app).join(format!("port_{launcher_pid}.json"))
}

fn update_error_path(app: &AppHandle) -> PathBuf {
    data_dir(app).join("update_install_error.txt")
}

fn sidecar_runtime_args(
    launcher_pid: u32,
    data_dir: &std::path::Path,
    app_version: &str,
    auth_token: &str,
) -> Vec<std::ffi::OsString> {
    vec![
        "--sidecar".into(),
        "--pid".into(),
        launcher_pid.to_string().into(),
        "--data-dir".into(),
        data_dir.as_os_str().to_owned(),
        "--app-version".into(),
        app_version.into(),
        "--auth-token".into(),
        auth_token.into(),
    ]
}

fn runtime_app_version() -> &'static str {
    // Cargo.toml is the release version source of truth. The generated Tauri
    // package context can remain stale across same-version local rebuilds.
    env!("CARGO_PKG_VERSION")
}

fn generate_backend_auth_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes).map_err(|error| format!("secure random failed: {error}"))?;
    let mut token = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(&mut token, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(token)
}

fn backend_auth_token(app: &AppHandle) -> Result<String, String> {
    app.try_state::<BackendAuthToken>()
        .map(|token| token.0.clone())
        .ok_or_else(|| "Backend authentication is unavailable".to_string())
}

#[tauri::command]
fn discard_pending_update(app: AppHandle) -> bool {
    take_pending_update(&app).is_some()
}

#[tauri::command]
fn take_update_install_error(app: AppHandle) -> Option<String> {
    let path = update_error_path(&app);
    let message = fs::read_to_string(&path).ok();
    let _ = fs::remove_file(path);
    message
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn spawn_sidecar(
    app: &AppHandle,
    launcher_pid: u32,
    app_version: &str,
    auth_token: &str,
) -> Result<Child, String> {
    let ddir = data_dir(app);
    // Best-effort: kill any leftover port file from a previous run with the same launcher PID.
    let _ = fs::remove_file(port_file_path(app, launcher_pid));

    // Dev: run the .py with the system `py` launcher so script edits are live.
    // Release: run the bundled, self-contained PyInstaller exe sitting next to the
    // app exe — the user needs no Python or pip packages installed.
    #[cfg(debug_assertions)]
    let mut cmd = {
        let dir = python_dir(app);
        let script = dir.join("xynmacro_core.py");
        if !script.exists() {
            return Err(format!("Sidecar script not found at {:?}", script));
        }
        let python_cmd = if cfg!(target_os = "windows") {
            "py"
        } else {
            "python3"
        };
        println!("[tauri] (dev) Spawning sidecar: {python_cmd} {:?}", script);
        let mut c = Command::new(python_cmd);
        c.arg(&script).current_dir(&dir);
        c
    };

    #[cfg(not(debug_assertions))]
    let mut cmd = {
        let exe = std::env::current_exe().map_err(|e| format!("current_exe failed: {e}"))?;
        let sidecar = exe
            .parent()
            .map(|d| d.join(SIDECAR_EXE))
            .ok_or_else(|| "could not resolve sidecar directory".to_string())?;
        if !sidecar.exists() {
            return Err(format!("Sidecar exe not found at {:?}", sidecar));
        }
        println!("[tauri] Spawning bundled sidecar: {:?}", sidecar);
        Command::new(sidecar)
    };

    cmd.args(sidecar_runtime_args(
        launcher_pid,
        &ddir,
        app_version,
        auth_token,
    ))
    .stdout(Stdio::inherit())
    .stderr(Stdio::inherit());

    // On Windows, suppress the console window the `py` launcher pops up for child python.exe.
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {e}"))
}

fn read_backend_port(app: &AppHandle, launcher_pid: u32) -> Option<u16> {
    let path = port_file_path(app, launcher_pid);
    for attempt in 1..=60 {
        if let Ok(text) = fs::read_to_string(&path) {
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                if let Some(port) = val.get("port").and_then(|p| p.as_u64()) {
                    println!("[tauri] Found port {port} on attempt {attempt}");
                    return Some(port as u16);
                }
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    None
}

fn wait_for_backend(port: u16, auth_token: &str) -> bool {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .build()
        .ok();
    let Some(client) = client else { return false };
    for i in 0..40 {
        let healthy = client
            .get(format!("http://127.0.0.1:{port}/health"))
            .header(BACKEND_AUTH_HEADER, auth_token)
            .send()
            .ok()
            .filter(|response| response.status().is_success())
            .and_then(|response| response.json::<serde_json::Value>().ok())
            .is_some_and(|body| {
                body.get("ok").and_then(|value| value.as_bool()) == Some(true)
                    && body.get("pid").and_then(|value| value.as_u64()).is_some()
                    && body
                        .get("version")
                        .and_then(|value| value.as_str())
                        .is_some()
            });
        if healthy {
            println!("[tauri] Backend healthy after {} attempts", i + 1);
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
    false
}

fn has_pending_update(app: &AppHandle) -> bool {
    app.try_state::<PendingUpdate>()
        .and_then(|state| state.0.lock().ok().map(|guard| guard.is_some()))
        .unwrap_or(false)
}

#[cfg(target_os = "windows")]
fn run_taskkill_bounded(args: &[&str], timeout: Duration) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x08000000;

    let Ok(mut taskkill) = Command::new("taskkill")
        .args(args)
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return;
    };
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if matches!(taskkill.try_wait(), Ok(Some(_))) {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let _ = taskkill.kill();
    let _ = taskkill.try_wait();
}

fn terminate_tracked_child(app: &AppHandle, timeout: Duration) {
    let child = app
        .try_state::<PythonProcess>()
        .and_then(|process| process.0.lock().ok().and_then(|mut guard| guard.take()));
    let Some(mut child) = child else { return };
    let pid = child.id();
    println!("[tauri] Terminating sidecar PID {pid}");
    let deadline = Instant::now() + timeout;

    if matches!(child.try_wait(), Ok(Some(_))) {
        return;
    }

    #[cfg(target_os = "windows")]
    run_taskkill_bounded(
        &["/F", "/T", "/PID", &pid.to_string()],
        Duration::from_millis(300),
    );
    #[cfg(not(target_os = "windows"))]
    let _ = child.kill();

    while Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let _ = child.kill();
    for _ in 0..10 {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(target_os = "windows")]
fn sidecar_executable_path() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|error| format!("current_exe failed: {error}"))?;
    exe.parent()
        .map(|dir| dir.join(SIDECAR_EXE))
        .ok_or_else(|| "could not resolve sidecar directory".to_string())
}

#[cfg(target_os = "windows")]
fn wait_for_sidecar_unlock() -> Result<(), String> {
    use std::os::windows::fs::OpenOptionsExt;

    let sidecar = sidecar_executable_path()?;
    if !sidecar.exists() {
        return Ok(());
    }
    let deadline = Instant::now() + Duration::from_millis(900);
    while Instant::now() < deadline {
        if fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&sidecar)
            .is_ok()
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    Err(format!(
        "Timed out waiting for {SIDECAR_EXE} to unlock; update cancelled"
    ))
}

#[cfg(not(target_os = "windows"))]
fn wait_for_sidecar_unlock() -> Result<(), String> {
    Ok(())
}

fn restart_with_update_error(app: &AppHandle, message: &str) {
    eprintln!("[tauri] Update install cancelled: {message}");
    let _ = fs::write(update_error_path(app), message);
    app.request_restart();
}

fn request_shutdown(app: AppHandle, hide_window: bool) -> bool {
    let Some(state) = app.try_state::<ShutdownState>() else {
        return false;
    };
    if state.started.swap(true, Ordering::SeqCst) {
        return false;
    }
    if hide_window {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.hide();
        }
    }
    let launcher_pid = state.launcher_pid;
    std::thread::spawn(move || {
        let installing_update = has_pending_update(&app);
        terminate_tracked_child(&app, Duration::from_millis(650));
        cleanup_port_file(&app, launcher_pid);
        if installing_update {
            if let Err(error) = wait_for_sidecar_unlock() {
                restart_with_update_error(&app, &error);
                return;
            }
            if let Some((update, bytes)) = take_pending_update(&app) {
                println!("[tauri] Installing downloaded update after cleanup");
                if let Err(error) = update.install(bytes) {
                    eprintln!("[tauri] Update install failed: {error}");
                    let message = error.to_string();
                    // The backend has already been stopped and the window hidden.
                    // Relaunch cleanly, surface the failure, and let the next
                    // update check offer the version again.
                    restart_with_update_error(&app, &message);
                    return;
                }
                // Windows normally exits inside install(); if it returns on any
                // platform, restart through Tauri after cleanup instead of leaving
                // a visible shell with no backend.
                app.request_restart();
                return;
            }
        }
        app.exit(0);
    });
    true
}

fn cleanup_port_file(app: &AppHandle, launcher_pid: u32) {
    let _ = fs::remove_file(port_file_path(app, launcher_pid));
}

/// Surface a fatal sidecar-startup problem to the UI. Without this the splash just
/// times out and the user is left with a fully-loaded window where nothing works and
/// no error is shown (the real cause only went to stderr, which a GUI user never sees).
fn notify_backend_error(app: &AppHandle, msg: &str) {
    if let Some(win) = app.get_webview_window("main") {
        let safe = msg.replace('\\', "\\\\").replace('\'', "\\'");
        let _ = win.eval(format!(
            "window.dispatchEvent(new CustomEvent('backend-error', {{ detail: {{ message: '{safe}' }} }}));"
        ));
    }
}

#[tauri::command]
fn get_backend_port(app: AppHandle) -> u16 {
    app.try_state::<BackendPort>()
        .and_then(|p| p.0.lock().ok().map(|g| *g))
        .unwrap_or(0)
}

#[tauri::command]
fn send_to_python(
    app: AppHandle,
    action: String,
    value: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let port = get_backend_port(app.clone());
    if port == 0 {
        return Err("Backend not ready".into());
    }
    let body = serde_json::json!({ "action": action, "value": value });
    let auth_token = backend_auth_token(&app)?;
    http_client()
        .post(format!("http://127.0.0.1:{port}/command"))
        .header(BACKEND_AUTH_HEADER, auth_token)
        .json(&body)
        .send()
        .map_err(|e| e.to_string())?
        .json::<serde_json::Value>()
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn proxy_get(app: AppHandle, path: String) -> Result<serde_json::Value, String> {
    let port = get_backend_port(app.clone());
    if port == 0 {
        return Err("Backend not ready".into());
    }
    let url = format!(
        "http://127.0.0.1:{port}{}",
        if path.starts_with('/') {
            path
        } else {
            format!("/{path}")
        }
    );
    let auth_token = backend_auth_token(&app)?;
    http_client()
        .get(url)
        .header(BACKEND_AUTH_HEADER, auth_token)
        .send()
        .map_err(|e| e.to_string())?
        .json::<serde_json::Value>()
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn proxy_post(
    app: AppHandle,
    path: String,
    body: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let port = get_backend_port(app.clone());
    if port == 0 {
        return Err("Backend not ready".into());
    }
    let url = format!(
        "http://127.0.0.1:{port}{}",
        if path.starts_with('/') {
            path
        } else {
            format!("/{path}")
        }
    );
    let mut req = http_client().post(url);
    req = req.header(BACKEND_AUTH_HEADER, backend_auth_token(&app)?);
    if let Some(b) = body {
        req = req.json(&b);
    }
    req.send()
        .map_err(|e| e.to_string())?
        .json::<serde_json::Value>()
        .map_err(|e| e.to_string())
}

/// Let Win11 round the window corners (~8px) so the native drop shadow follows the rounded
/// shape and bleeds off the edges like a normal app. CSS --window-radius is a hair smaller
/// (6px) so the opaque frame fills under DWM's clip — no transparent crescent at the corners.
#[cfg(target_os = "windows")]
fn round_window_corners(win: &tauri::WebviewWindow) {
    use windows_sys::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND,
    };
    let Ok(handle) = win.hwnd() else { return };
    let pref: i32 = DWMWCP_ROUND;
    unsafe {
        let _ = DwmSetWindowAttribute(
            handle.0 as _,
            DWMWA_WINDOW_CORNER_PREFERENCE as u32,
            &pref as *const _ as *const _,
            std::mem::size_of::<i32>() as u32,
        );
    }
}

#[cfg(not(target_os = "windows"))]
fn round_window_corners(_win: &tauri::WebviewWindow) {}

#[tauri::command]
fn wc(app: AppHandle, action: String, value: Option<serde_json::Value>) -> Result<(), String> {
    let Some(win) = app.get_webview_window("main") else {
        return Err("main window not found".into());
    };
    match action.as_str() {
        "minimize" => win.minimize().map_err(|e| e.to_string()),
        "maximize" => {
            let is_max = win.is_maximized().unwrap_or(false);
            if is_max {
                win.unmaximize()
            } else {
                win.maximize()
            }
            .map_err(|e| e.to_string())
        }
        "close" => win.close().map_err(|e| e.to_string()),
        "ontop" => {
            let on = value.as_ref().and_then(|v| v.as_bool()).unwrap_or(false);
            win.set_always_on_top(on).map_err(|e| e.to_string())
        }
        "drag" => win.start_dragging().map_err(|e| e.to_string()),
        "show" => win.show().map_err(|e| e.to_string()),
        "compact" => {
            if let Some(state) = app.try_state::<WindowPrefsState>() {
                state.compact_or_animating.store(true, Ordering::SeqCst);
            }
            if let Ok(bounds) = capture_expanded_window_bounds(&app) {
                let _ = write_saved_window_bounds(&app, bounds);
            }
            // Save the current expanded size so we can restore it on uncompact.
            // Target size = last remembered compact size, or fall back to current_width × default_height.
            let scale = win.scale_factor().unwrap_or(1.0);
            let cur = win.inner_size().map_err(|e| e.to_string())?;
            let cur_w = cur.width as f64 / scale;
            let cur_h = cur.height as f64 / scale;
            let anim = app.state::<WindowAnim>();
            let my_gen = anim.gen.fetch_add(1, Ordering::SeqCst) + 1;
            // If an uncompact animation is still in flight, its target is the true
            // expanded size — saving the half-animated current size would make the
            // next uncompact restore a shrunken window.
            let in_flight = anim.target.lock().ok().and_then(|mut g| g.take());
            let (lw, lh) = in_flight.unwrap_or((cur_w, cur_h));
            if let Some(state) = app.try_state::<PreCompactSize>() {
                if let Ok(mut g) = state.0.lock() {
                    *g = Some((lw, lh));
                }
            }
            let last_compact = app
                .try_state::<LastCompactSize>()
                .and_then(|s| s.0.lock().ok().and_then(|g| *g));
            let (target_w, target_h) = last_compact.unwrap_or((lw, DEFAULT_COMPACT_HEIGHT));
            if let Ok(mut g) = anim.target.lock() {
                *g = Some((target_w, target_h));
            }
            let win2 = win.clone();
            let app2 = app.clone();
            // Animate width and height together so a saved narrower pill smoothly slides in.
            std::thread::spawn(move || {
                let anim = app2.state::<WindowAnim>();
                let steps: u32 = 14;
                for i in 1..=steps {
                    if anim.gen.load(Ordering::SeqCst) != my_gen {
                        return;
                    }
                    let t = i as f64 / steps as f64;
                    let eased = 1.0 - (1.0 - t).powi(3);
                    let w = cur_w + (target_w - cur_w) * eased;
                    let h = cur_h + (target_h - cur_h) * eased;
                    let _ = win2.set_size(Size::Logical(LogicalSize::new(w, h)));
                    std::thread::sleep(std::time::Duration::from_millis(16));
                }
                if anim.gen.load(Ordering::SeqCst) == my_gen {
                    let _ = win2.set_size(Size::Logical(LogicalSize::new(target_w, target_h)));
                    if let Ok(mut g) = anim.target.lock() {
                        *g = None;
                    }
                }
            });
            Ok(())
        }
        "uncompact" => {
            if let Some(state) = app.try_state::<WindowPrefsState>() {
                state.compact_or_animating.store(true, Ordering::SeqCst);
            }
            // Save the current pill size so the next compact restores it.
            // Target = pre-compact expanded size (defaults to 1100×720 first run).
            let scale = win.scale_factor().unwrap_or(1.0);
            let cur = win.inner_size().map_err(|e| e.to_string())?;
            let cur_w = cur.width as f64 / scale;
            let cur_h = cur.height as f64 / scale;
            let anim = app.state::<WindowAnim>();
            let my_gen = anim.gen.fetch_add(1, Ordering::SeqCst) + 1;
            // Mirror of the compact arm: a mid-flight compact animation's target is
            // the true pill size to remember.
            let in_flight = anim.target.lock().ok().and_then(|mut g| g.take());
            let (pill_w, pill_h) = in_flight.unwrap_or((cur_w, cur_h));
            if let Some(state) = app.try_state::<LastCompactSize>() {
                if let Ok(mut g) = state.0.lock() {
                    *g = Some((pill_w, pill_h));
                }
            }
            let pre = app
                .try_state::<PreCompactSize>()
                .and_then(|s| s.0.lock().ok().and_then(|g| *g));
            let (target_w, target_h) =
                pre.unwrap_or((DEFAULT_EXPANDED_WIDTH, DEFAULT_EXPANDED_HEIGHT));
            if let Ok(mut g) = anim.target.lock() {
                *g = Some((target_w, target_h));
            }
            let win2 = win.clone();
            let app2 = app.clone();
            std::thread::spawn(move || {
                let anim = app2.state::<WindowAnim>();
                let steps: u32 = 14;
                for i in 1..=steps {
                    if anim.gen.load(Ordering::SeqCst) != my_gen {
                        return;
                    }
                    let t = i as f64 / steps as f64;
                    let eased = 1.0 - (1.0 - t).powi(3);
                    let w = cur_w + (target_w - cur_w) * eased;
                    let h = cur_h + (target_h - cur_h) * eased;
                    let _ = win2.set_size(Size::Logical(LogicalSize::new(w, h)));
                    std::thread::sleep(std::time::Duration::from_millis(16));
                }
                if anim.gen.load(Ordering::SeqCst) == my_gen {
                    let _ = win2.set_size(Size::Logical(LogicalSize::new(target_w, target_h)));
                    if let Ok(mut g) = anim.target.lock() {
                        *g = None;
                    }
                    if let Some(state) = app2.try_state::<WindowPrefsState>() {
                        state.compact_or_animating.store(false, Ordering::SeqCst);
                    }
                    schedule_window_bounds_save(app2.clone());
                }
            });
            Ok(())
        }
        other => Err(format!("Unknown wc action: {other}")),
    }
}

pub fn run() {
    let launcher_pid = std::process::id();
    let backend_auth_token =
        generate_backend_auth_token().expect("failed to create backend authentication token");
    let setup_auth_token = backend_auth_token.clone();

    tauri::Builder::default()
        // Must be first: a second shell must exit before setup can spawn a
        // competing sidecar and register duplicate global hotkeys.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(PythonProcess(Mutex::new(None)))
        .manage(BackendPort(Mutex::new(0_u16)))
        .manage(BackendAuthToken(backend_auth_token))
        .manage(PreCompactSize(Mutex::new(None)))
        .manage(LastCompactSize(Mutex::new(None)))
        .manage(WindowAnim { gen: AtomicU64::new(0), target: Mutex::new(None) })
        .manage(WindowPrefsState {
            compact_or_animating: AtomicBool::new(false),
            save_generation: AtomicU64::new(0),
        })
        .manage(ConfigResetState(AtomicBool::new(false)))
        .manage(PendingUpdate(Mutex::new(None)))
        .manage(ShutdownState {
            started: AtomicBool::new(false),
            launcher_pid,
        })
        .setup(move |app| {
            println!("[tauri] Launcher PID: {launcher_pid}");

            // macro_config.json is the user-visible reset anchor. Check before
            // the sidecar recreates it, then discard the native bounds and tell
            // the frontend to clear its active preferences once.
            let macro_config = data_dir(app.handle()).join("json").join("macro_config.json");
            let config_was_deleted = !macro_config.exists();
            if let Some(state) = app.try_state::<ConfigResetState>() {
                state.0.store(config_was_deleted, Ordering::SeqCst);
            }
            if config_was_deleted {
                if let Ok(path) = app_prefs_path(app.handle()) {
                    let _ = fs::remove_file(path);
                }
                // macro_config.json is the documented full-reset anchor. Clear
                // the remaining runtime preferences before Python seeds the
                // shipped calibration files for this launch. Logs and exports
                // are preserved deliberately.
                let root = data_dir(app.handle());
                for path in [
                    root.join("json").join("button_calibration.json"),
                    root.join("json").join("region_calibration.json"),
                    root.join("json").join("pixel_calibration.json"),
                    root.join("save_dir.txt"),
                ] {
                    let _ = fs::remove_file(path);
                }
            }

            if let Some(win) = app.get_webview_window("main") {
                // Round corners via DWM + keep the native drop shadow so the window has depth
                // on the desktop like a normal app, with the shadow bleeding off the edges
                // (no transparent gutter). CSS --window-radius (6px) sits just inside DWM's
                // ~8px so the opaque frame fills under DWM's clip and there's no crescent.
                round_window_corners(&win);
                let _ = win.set_shadow(true);
                if config_was_deleted {
                    let _ = win.set_always_on_top(false);
                }
            }
            if !config_was_deleted {
                restore_saved_window_bounds(app.handle());
            }

            let app_handle = app.handle().clone();
            let app_version = runtime_app_version();
            match spawn_sidecar(
                &app_handle,
                launcher_pid,
                app_version,
                &setup_auth_token,
            ) {
                Ok(child) => {
                    if let Some(proc) = app.try_state::<PythonProcess>() {
                        if let Ok(mut g) = proc.0.lock() {
                            *g = Some(child);
                        }
                    }
                }
                Err(e) => {
                    eprintln!("[tauri] {e}");
                }
            }

            // Port discovery + health check off the main thread so the window can show.
            let handle = app.handle().clone();
            let health_auth_token = setup_auth_token.clone();
            std::thread::spawn(move || {
                let Some(port) = read_backend_port(&handle, launcher_pid) else {
                    eprintln!("[tauri] Gave up waiting for port file");
                    notify_backend_error(&handle, "The macro backend never started. Python may be missing or was blocked by antivirus.");
                    return;
                };
                if !wait_for_backend(port, &health_auth_token) {
                    eprintln!("[tauri] Backend did not become healthy on port {port}");
                    notify_backend_error(&handle, "The macro backend started but never responded. Restart XynMacro; if this continues, reinstall it and check antivirus quarantine.");
                    return;
                }
                if let Some(p) = handle.try_state::<BackendPort>() {
                    if let Ok(mut g) = p.0.lock() {
                        *g = port;
                    }
                }
                println!("[tauri] Sidecar ready on port {port}");
                if let Some(win) = handle.get_webview_window("main") {
                    let _ = win.eval(format!(
                        "window.__BACKEND_PORT__ = {port}; window.dispatchEvent(new CustomEvent('backend-ready', {{ detail: {{ port: {port} }} }}));"
                    ));
                }
            });

            // Reveal the window once it should be painted. The frontend also calls
            // wc:show as soon as it paints; whichever fires first wins. Starting hidden
            // avoids the blank/transparent frame before the splash renders.
            let show_handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_millis(1200));
                if let Some(win) = show_handle.get_webview_window("main") {
                    let _ = win.show();
                }
            });

            Ok(())
        })
        .on_window_event(move |window, event| {
            if window.label() == "main"
                && matches!(event, tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_))
            {
                schedule_window_bounds_save(window.app_handle().clone());
            }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let app_handle = window.app_handle().clone();
                    request_shutdown(app_handle, true);
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            check_update,
            download_update,
            discard_pending_update,
            take_update_install_error,
            install_pending_update,
            factory_reset_app_prefs,
            take_config_reset_flag,
            get_backend_port,
            send_to_python,
            proxy_get,
            proxy_post,
            wc,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested { api, .. } = event {
                let already_cleaning = app
                    .try_state::<ShutdownState>()
                    .map(|state| state.started.load(Ordering::SeqCst))
                    .unwrap_or(false);
                if !already_cleaning {
                    api.prevent_exit();
                    request_shutdown(app.clone(), true);
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::{generate_backend_auth_token, runtime_app_version, sidecar_runtime_args};
    use std::path::Path;

    #[test]
    fn sidecar_receives_tauri_package_version() {
        let args = sidecar_runtime_args(4242, Path::new(r"C:\runtime"), "1.7.3", "test-auth-token");
        let args: Vec<_> = args.iter().map(|arg| arg.to_string_lossy()).collect();

        assert_eq!(
            args,
            [
                "--sidecar",
                "--pid",
                "4242",
                "--data-dir",
                r"C:\runtime",
                "--app-version",
                "1.7.3",
                "--auth-token",
                "test-auth-token",
            ]
        );
    }

    #[test]
    fn runtime_version_comes_from_the_cargo_package() {
        assert_eq!(runtime_app_version(), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn backend_auth_tokens_are_random_256_bit_hex_values() {
        let first = generate_backend_auth_token().unwrap();
        let second = generate_backend_auth_token().unwrap();

        assert_eq!(first.len(), 64);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_ne!(first, second);
    }
}
