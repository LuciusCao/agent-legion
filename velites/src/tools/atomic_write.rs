//! Atomic file replacement shared by the `write` and `json` tools: a fresh
//! temp file next to the target, then `rename` over it.
//!
//! #922 R-2: the temp file is created IN-PROCESS — outside the OS sandbox
//! that confines the bash tool — so its creation must not depend on what
//! already exists in the working directory. The temp name carries
//! a random suffix (unpredictable), and it is opened with `create_new`
//! (O_CREAT|O_EXCL) plus, on unix, O_NOFOLLOW: any pre-existing entry at
//! that name, a symlink included, fails the open instead of being followed.
//! `rename` replaces the target's directory entry itself and never follows
//! a symlink there (the target path is already resolved inside the cwd).

use std::io::Write;
use std::path::{Path, PathBuf};

/// Atomically replace `target` with `bytes`. On failure no temp file is
/// left behind (one this call did not create is never touched).
pub(crate) fn atomic_write(target: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let tmp = tmp_path(target);
    // A failed open created nothing, so there is nothing to clean up.
    let mut file = open_new(&tmp)?;
    let written = file.write_all(bytes);
    drop(file);
    let result = written.and_then(|()| std::fs::rename(&tmp, target));
    if result.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    result
}

/// Create `path` exclusively; never follows a symlink at `path`.
fn open_new(path: &Path) -> std::io::Result<std::fs::File> {
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    options.open(path)
}

/// The temp path for one atomic write: the FULL file name plus a suffix
/// with a random nonce (`a.md` → `a.md.velites-tmp-<hex>`). The full name
/// keeps same-stem targets (`a.md` / `a.txt`) apart; the nonce makes the
/// name unpredictable and unique per write.
fn tmp_path(target: &Path) -> PathBuf {
    let file_name = target
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("velites");
    let nonce = uuid::Uuid::new_v4().simple();
    target.with_file_name(format!("{file_name}.velites-tmp-{nonce}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leftover_tmp_files(dir: &Path) -> Vec<String> {
        std::fs::read_dir(dir)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .filter(|name| name.contains(".velites-tmp"))
            .collect()
    }

    #[test]
    fn tmp_path_carries_the_full_file_name_and_a_fresh_nonce() {
        let first = tmp_path(Path::new("/d/a.md"));
        let second = tmp_path(Path::new("/d/a.md"));
        assert_ne!(first, second);
        for tmp in [&first, &second] {
            assert_eq!(tmp.parent(), Some(Path::new("/d")));
            let name = tmp.file_name().unwrap().to_str().unwrap();
            assert!(name.starts_with("a.md.velites-tmp-"), "{name}");
        }
        // Same stem, different extensions: no shared tmp prefix.
        let other = tmp_path(Path::new("/d/a.txt"));
        let name = other.file_name().unwrap().to_str().unwrap();
        assert!(name.starts_with("a.txt.velites-tmp-"), "{name}");
    }

    #[test]
    fn atomic_write_replaces_content_and_leaves_no_tmp() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("out.md");
        atomic_write(&target, b"first").unwrap();
        atomic_write(&target, b"second").unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), b"second");
        assert!(leftover_tmp_files(dir.path()).is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn open_new_refuses_an_existing_symlink() {
        let dir = tempfile::tempdir().unwrap();
        let outside = dir.path().join("outside.txt");
        std::fs::write(&outside, "untouched").unwrap();
        let link = dir.path().join("link");
        std::os::unix::fs::symlink(&outside, &link).unwrap();
        assert!(open_new(&link).is_err());
        assert_eq!(std::fs::read_to_string(&outside).unwrap(), "untouched");
    }

    #[cfg(unix)]
    #[test]
    fn atomic_write_ignores_a_symlink_at_the_legacy_tmp_name() {
        // The pre-#922 fixed temp name must carry no meaning any more.
        let dir = tempfile::tempdir().unwrap();
        let work = dir.path().join("work");
        std::fs::create_dir(&work).unwrap();
        let outside = dir.path().join("outside.txt");
        std::fs::write(&outside, "untouched").unwrap();
        std::os::unix::fs::symlink(&outside, work.join("out.md.velites-tmp")).unwrap();
        atomic_write(&work.join("out.md"), b"payload").unwrap();
        assert_eq!(std::fs::read_to_string(&outside).unwrap(), "untouched");
        assert_eq!(std::fs::read(work.join("out.md")).unwrap(), b"payload");
    }
}
