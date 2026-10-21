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
    tauri_build::build()
}
