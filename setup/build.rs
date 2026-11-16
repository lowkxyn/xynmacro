use std::{env, fs, path::PathBuf};

fn main() {
    // XYN_PAYLOAD is the NSIS installer this setup runs. Without it the setup builds with an
    // empty payload (for UI work only) and refuses to install.
    println!("cargo:rerun-if-env-changed=XYN_PAYLOAD");
    println!("cargo:rerun-if-env-changed=XYN_INSTALL_FILES");
    let out = PathBuf::from(env::var("OUT_DIR").unwrap()).join("payload.exe");
    match env::var("XYN_PAYLOAD") {
        Ok(path) => {
            println!("cargo:rerun-if-changed={path}");
            fs::copy(&path, &out).unwrap_or_else(|e| panic!("XYN_PAYLOAD {path}: {e}"));
        }
        Err(_) => {
            println!("cargo:warning=XYN_PAYLOAD not set; this setup has no installer inside");
            fs::write(&out, b"").unwrap();
        }
    }
    // "name|bytes;name|bytes" in the order NSIS writes them, for the progress bar's file names.
    let files = env::var("XYN_INSTALL_FILES").unwrap_or_default();
    println!("cargo:rustc-env=XYN_INSTALL_FILES={files}");
    // The setup is run straight from a Downloads folder. Windows looks there first for
    // the DLLs an exe imports, so a file planted beside it would be loaded. This limits
    // that search to System32; the C runtime is linked in (.cargo/config.toml) so nothing
    // it imports lives anywhere else.
    println!("cargo:rustc-link-arg-bins=/DEPENDENTLOADFLAG:0x800");
    if !env::var("CARGO_CFG_TARGET_FEATURE").unwrap_or_default().contains("crt-static") {
        println!("cargo:warning=built without a static C runtime (RUSTFLAGS overrides .cargo/config.toml); do not ship this setup");
    }
    tauri_build::build()
}
