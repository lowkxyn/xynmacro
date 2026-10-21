// XynMacro local patch. See XYNMACRO-PATCH.md for provenance and scope.
// SPDX-License-Identifier: Apache-2.0 OR MIT

use std::io;

use reqwest::ResponseBuilderExt;

// This app's installer must fit within 512 MiB, even without a truthful length header.
const MAX_DOWNLOAD_BYTES: usize = 512 * 1024 * 1024;
const MAX_METADATA_BYTES: usize = 1024 * 1024;

fn metadata_too_large() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "Updater metadata exceeds the 1 MiB (1048576 byte) limit",
    )
}

fn check_metadata_content_length(content_length: Option<u64>) -> io::Result<()> {
    if content_length.is_some_and(|length| length > MAX_METADATA_BYTES as u64) {
        return Err(metadata_too_large());
    }
    Ok(())
}

fn check_metadata_size(buffered: usize, incoming: usize) -> io::Result<()> {
    match buffered.checked_add(incoming) {
        Some(total) if total <= MAX_METADATA_BYTES => Ok(()),
        _ => Err(metadata_too_large()),
    }
}

pub(crate) async fn read_metadata(
    mut response: reqwest::Response,
) -> crate::Result<serde_json::Value> {
    // A stream's known length and its declared header can differ. Neither is
    // trusted as an allocation hint or as a replacement for the per-chunk guard.
    check_metadata_content_length(response.content_length())?;
    check_metadata_content_length(
        response
            .headers()
            .get(reqwest::header::CONTENT_LENGTH)
            .and_then(|length| length.to_str().ok())
            .and_then(|length| length.parse().ok()),
    )?;

    let url = response.url().clone();
    let mut buffer = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|error| error.with_url(url.clone()))?
    {
        check_metadata_size(buffer.len(), chunk.len())?;
        buffer.extend_from_slice(&chunk);
    }

    // Decode only the bounded in-memory body. Keep reqwest's JSON error variant
    // and source URL, matching the original Response::json error behavior.
    let buffered_response = http::Response::builder().url(url).body(buffer)?;
    Ok(reqwest::Response::from(buffered_response).json().await?)
}

fn download_too_large() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "Updater download exceeds the 512 MiB (536870912 byte) limit",
    )
}

pub(crate) fn check_content_length(content_length: Option<u64>) -> io::Result<()> {
    if content_length.is_some_and(|length| length > MAX_DOWNLOAD_BYTES as u64) {
        return Err(download_too_large());
    }
    Ok(())
}

pub(crate) fn check_download_size(downloaded: usize, incoming: usize) -> io::Result<()> {
    match downloaded.checked_add(incoming) {
        Some(total) if total <= MAX_DOWNLOAD_BYTES => Ok(()),
        _ => Err(download_too_large()),
    }
}

#[cfg(any(windows, test))]
pub(crate) fn check_shell_execute_result(result: isize) -> io::Result<()> {
    // ShellExecute returns an INT_PTR-compatible value, not a real HINSTANCE.
    // Only values greater than 32 indicate launch success. Preserve the shell
    // code in the error instead of interpreting it as a Win32 GetLastError code.
    if result <= 32 {
        return Err(io::Error::other(format!(
            "Windows refused to launch the update installer (ShellExecuteW code {result})"
        )));
    }
    Ok(())
}

// The minisign signature covers its trusted comment, where the Tauri signer records
// `timestamp:<n>\tfile:<signed file name>`, and bundle names are `<productName>_<version>_...`.
// Requiring that name to match this app and the announced version stops a tampered feed
// from relabelling an older signed installer, or the other (stable/beta) profile's
// installer, as a newer update. Signature bytes alone never bound the feed's version.
pub(crate) fn check_signed_file(
    trusted_comment: &str,
    app_name: &str,
    version: &str,
) -> io::Result<()> {
    let signed_file = trusted_comment
        .split('\t')
        .find_map(|field| field.strip_prefix("file:"));
    match signed_file {
        Some(name) if name.starts_with(&format!("{app_name}_{version}_")) => Ok(()),
        _ => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Signed update file {signed_file:?} is not {app_name} {version}"),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::{stream, FutureExt, Stream, StreamExt};

    fn metadata_response(body: reqwest::Body, content_length: Option<u64>) -> reqwest::Response {
        let mut response = http::Response::builder().url(
            "https://updates.example.invalid/latest.json"
                .parse()
                .unwrap(),
        );
        if let Some(length) = content_length {
            response = response.header(reqwest::header::CONTENT_LENGTH, length);
        }
        response.body(body).unwrap().into()
    }

    fn metadata_limit_chunks() -> impl Stream<Item = io::Result<Vec<u8>>> {
        stream::iter(0..16).map(|index| {
            let mut chunk = vec![b' '; MAX_METADATA_BYTES / 16];
            if index == 0 {
                chunk[..2].copy_from_slice(b"{}");
            }
            Ok(chunk)
        })
    }

    fn assert_metadata_too_large(error: crate::Error) {
        match error {
            crate::Error::Io(error) => {
                assert_eq!(error.kind(), io::ErrorKind::InvalidData);
                assert!(error.to_string().contains("1 MiB (1048576 byte)"));
            }
            error => panic!("expected a metadata size error, got {error:?}"),
        }
    }

    #[test]
    fn metadata_declared_length_accepts_missing_zero_and_limit() {
        for length in [None, Some(0), Some(1), Some(MAX_METADATA_BYTES as u64)] {
            assert!(check_metadata_content_length(length).is_ok(), "{length:?}");
        }
    }

    #[test]
    fn metadata_declared_oversize_is_rejected_before_polling_body() {
        for length in [MAX_METADATA_BYTES as u64 + 1, u64::MAX] {
            let chunks = stream::poll_fn(|_| -> std::task::Poll<Option<io::Result<Vec<u8>>>> {
                panic!("oversized declared metadata must not poll the body");
            });
            let response = metadata_response(reqwest::Body::wrap_stream(chunks), Some(length));
            assert_metadata_too_large(read_metadata(response).now_or_never().unwrap().unwrap_err());
        }
    }

    #[test]
    fn metadata_chunk_sizes_accept_exact_limit_and_empty_chunk() {
        for (buffered, incoming) in [
            (0, 0),
            (0, MAX_METADATA_BYTES),
            (MAX_METADATA_BYTES - 1, 1),
            (MAX_METADATA_BYTES, 0),
        ] {
            assert!(check_metadata_size(buffered, incoming).is_ok());
        }
    }

    #[test]
    fn metadata_chunk_sizes_reject_single_and_cumulative_oversize() {
        for (buffered, incoming) in [
            (0, MAX_METADATA_BYTES + 1),
            (MAX_METADATA_BYTES, 1),
            (MAX_METADATA_BYTES - 1, 2),
            (MAX_METADATA_BYTES + 1, 0),
        ] {
            assert_metadata_too_large(check_metadata_size(buffered, incoming).unwrap_err().into());
        }
    }

    #[test]
    fn metadata_chunk_sizes_reject_integer_overflow_without_allocating() {
        for (buffered, incoming) in [(usize::MAX, 1), (1, usize::MAX)] {
            assert_metadata_too_large(check_metadata_size(buffered, incoming).unwrap_err().into());
        }
    }

    #[test]
    fn metadata_parser_preserves_raw_json_and_both_release_formats() {
        let platform = serde_json::json!({
            "url": "https://updates.example.invalid/app.exe",
            "signature": "synthetic-signature"
        });
        let releases = [
            serde_json::json!({
                "version": "v2.0.0", "notes": "test notes", "extra": [1, true],
                "url": platform["url"], "signature": platform["signature"]
            }),
            serde_json::json!({
                "version": "2.0.0", "notes": "test notes", "extra": [1, true],
                "platforms": {"windows-x86_64": platform}
            }),
        ];
        for expected in releases {
            for length in [None, Some(0), Some(1), Some(MAX_METADATA_BYTES as u64)] {
                let bytes = serde_json::to_vec(&expected).unwrap();
                let chunks: Vec<_> = bytes
                    .chunks(7)
                    .map(|chunk| Ok::<_, io::Error>(chunk.to_vec()))
                    .collect();
                let response =
                    metadata_response(reqwest::Body::wrap_stream(stream::iter(chunks)), length);
                let parsed = read_metadata(response).now_or_never().unwrap().unwrap();
                assert_eq!(parsed, expected);
                let release: crate::updater::RemoteRelease =
                    serde_json::from_value(parsed).unwrap();
                assert_eq!(release.version.to_string(), "2.0.0");
                assert_eq!(release.notes.as_deref(), Some("test notes"));
                assert_eq!(
                    release.download_url("windows-x86_64").unwrap().as_str(),
                    platform["url"].as_str().unwrap()
                );
                assert_eq!(
                    release.signature("windows-x86_64").unwrap(),
                    "synthetic-signature"
                );
            }
        }
    }

    #[test]
    fn metadata_parser_accepts_exact_stream_limit_and_empty_chunk() {
        let chunks = metadata_limit_chunks().chain(stream::iter([Ok(Vec::new())]));
        let response = metadata_response(reqwest::Body::wrap_stream(chunks), None);
        assert_eq!(
            read_metadata(response).now_or_never().unwrap().unwrap(),
            serde_json::json!({})
        );
    }

    #[test]
    fn metadata_parser_rejects_cumulative_oversize_with_absent_or_lying_header() {
        for length in [None, Some(0), Some(1), Some(MAX_METADATA_BYTES as u64)] {
            let chunks = metadata_limit_chunks()
                .chain(stream::iter([Ok(vec![b' '])]))
                .chain(stream::poll_fn(|_| {
                    panic!("metadata reader must stop at the first oversized chunk");
                }));
            let response = metadata_response(reqwest::Body::wrap_stream(chunks), length);
            assert_metadata_too_large(read_metadata(response).now_or_never().unwrap().unwrap_err());
        }
    }

    #[test]
    fn metadata_parser_preserves_reqwest_json_errors_and_url() {
        for bytes in [b"".as_slice(), b"{", b"{} trailing", b"\"\xff\""] {
            let response = metadata_response(bytes.into(), None);
            let expected = metadata_response(bytes.into(), None)
                .json::<serde_json::Value>()
                .now_or_never()
                .unwrap()
                .unwrap_err();
            match read_metadata(response).now_or_never().unwrap().unwrap_err() {
                crate::Error::Reqwest(error) => {
                    assert!(error.is_decode());
                    assert_eq!(error.url(), expected.url());
                    assert_eq!(error.to_string(), expected.to_string());
                }
                error => panic!("expected the original reqwest JSON error, got {error:?}"),
            }
        }
    }

    #[test]
    fn metadata_parser_propagates_body_errors_before_parsing_partial_json() {
        let chunks = stream::iter([
            Ok(b"{}".to_vec()),
            Err(io::Error::new(
                io::ErrorKind::ConnectionReset,
                "synthetic body failure",
            )),
        ]);
        let response = metadata_response(reqwest::Body::wrap_stream(chunks), None);
        let expected_url = response.url().clone();
        match read_metadata(response).now_or_never().unwrap().unwrap_err() {
            crate::Error::Reqwest(error) => {
                assert!(error.is_decode());
                assert_eq!(error.url(), Some(&expected_url));
            }
            error => panic!("expected a reqwest body error, got {error:?}"),
        }
    }

    #[test]
    fn declared_length_accepts_missing_zero_and_limit() {
        for length in [None, Some(0), Some(1), Some(MAX_DOWNLOAD_BYTES as u64)] {
            assert!(check_content_length(length).is_ok(), "{length:?}");
        }
    }

    #[test]
    fn declared_oversize_is_rejected_without_reading_body() {
        for length in [MAX_DOWNLOAD_BYTES as u64 + 1, u64::MAX] {
            let error = check_content_length(Some(length)).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(error.to_string().contains("512 MiB"));
        }
    }

    #[test]
    fn chunk_sizes_accept_exact_limit_and_empty_final_chunk() {
        for (downloaded, incoming) in [
            (0, 0),
            (0, MAX_DOWNLOAD_BYTES),
            (MAX_DOWNLOAD_BYTES - 1, 1),
            (MAX_DOWNLOAD_BYTES, 0),
        ] {
            assert!(check_download_size(downloaded, incoming).is_ok());
        }
    }

    #[test]
    fn chunk_sizes_reject_single_and_cumulative_oversize() {
        for (downloaded, incoming) in [
            (0, MAX_DOWNLOAD_BYTES + 1),
            (MAX_DOWNLOAD_BYTES, 1),
            (MAX_DOWNLOAD_BYTES - 1, 2),
            (MAX_DOWNLOAD_BYTES + 1, 0),
        ] {
            assert_eq!(
                check_download_size(downloaded, incoming)
                    .unwrap_err()
                    .kind(),
                io::ErrorKind::InvalidData
            );
        }
    }

    #[test]
    fn chunk_sizes_reject_integer_overflow() {
        for (downloaded, incoming) in [(usize::MAX, 1), (1, usize::MAX)] {
            assert!(check_download_size(downloaded, incoming).is_err());
        }
    }

    #[test]
    fn absent_or_underreported_length_cannot_bypass_stream_limit() {
        for declared in [None, Some(0), Some(1)] {
            check_content_length(declared).unwrap();
            let mut downloaded = 0;
            for chunk_length in [MAX_DOWNLOAD_BYTES - 2, 1, 1] {
                check_download_size(downloaded, chunk_length).unwrap();
                downloaded += chunk_length;
            }
            assert!(check_download_size(downloaded, 1).is_err());
        }
    }

    #[test]
    fn shell_execute_rejects_entire_failure_range_and_preserves_code() {
        for code in 0..=32 {
            let error = check_shell_execute_result(code).unwrap_err();
            assert!(error
                .to_string()
                .contains(&format!("ShellExecuteW code {code})")));
        }
        // Follow the signed INT_PTR contract even for unexpected negative values.
        assert!(check_shell_execute_result(-1).is_err());
        assert!(check_shell_execute_result(isize::MIN).is_err());
    }

    #[test]
    fn shell_execute_accepts_success_boundary_and_pointer_sized_values() {
        for code in [33, 34, isize::MAX] {
            assert!(check_shell_execute_result(code).is_ok(), "{code}");
        }
    }

    // Trusted comments copied from the published 1.7.3 and 1.8.0-beta.4 signatures.
    const STABLE_1_7_3: &str = "timestamp:1786873748\tfile:XynMacro_1.7.3_x64-setup.exe";
    const BETA_4: &str = "timestamp:1789853152\tfile:XynMacro Beta_1.8.0-beta.4_x64-setup.exe";

    #[test]
    fn signed_file_accepts_matching_app_and_version() {
        assert!(check_signed_file(STABLE_1_7_3, "XynMacro", "1.7.3").is_ok());
        assert!(check_signed_file(BETA_4, "XynMacro Beta", "1.8.0-beta.4").is_ok());
        let msi = "timestamp:1\tfile:XynMacro_1.9.0_x64_en-US.msi";
        assert!(check_signed_file(msi, "XynMacro", "1.9.0").is_ok());
    }

    #[test]
    fn signed_file_rejects_older_installer_relabelled_as_newer() {
        let error = check_signed_file(STABLE_1_7_3, "XynMacro", "1.8.0").unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(check_signed_file(BETA_4, "XynMacro Beta", "1.8.0-beta.5").is_err());
    }

    #[test]
    fn signed_file_version_must_match_exactly_not_by_prefix() {
        // A prerelease of the same core version must not pass for the release, or vice versa.
        assert!(check_signed_file(BETA_4, "XynMacro Beta", "1.8.0").is_err());
        let stable = "timestamp:1\tfile:XynMacro_1.8.0_x64-setup.exe";
        assert!(check_signed_file(stable, "XynMacro", "1.8.0-beta.4").is_err());
        let two_digit = "timestamp:1\tfile:XynMacro_1.8.10_x64-setup.exe";
        assert!(check_signed_file(two_digit, "XynMacro", "1.8.1").is_err());
    }

    #[test]
    fn signed_file_rejects_the_other_profile() {
        let beta_as_stable = "timestamp:1\tfile:XynMacro Beta_1.9.0_x64-setup.exe";
        assert!(check_signed_file(beta_as_stable, "XynMacro", "1.9.0").is_err());
        let stable_as_beta = "timestamp:1\tfile:XynMacro_1.9.0_x64-setup.exe";
        assert!(check_signed_file(stable_as_beta, "XynMacro Beta", "1.9.0").is_err());
    }

    #[test]
    fn signed_file_rejects_comments_without_a_file_field() {
        for comment in ["", "timestamp:1", "timestamp:1\tfilename:XynMacro_1.9.0_x64-setup.exe"] {
            assert!(check_signed_file(comment, "XynMacro", "1.9.0").is_err(), "{comment:?}");
        }
    }
}
