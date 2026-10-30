#![cfg(target_os = "linux")]

use super::process::*;
use std::time::{Duration, Instant};

fn sleeping_child() -> i32 {
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        std::thread::sleep(Duration::from_secs(30));
        unsafe { libc::_exit(0) }
    }
    child
}

fn socket() -> i32 {
    let mut sockets = [0i32; 2];
    assert_eq!(
        unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_DGRAM, 0, sockets.as_mut_ptr()) },
        0
    );
    unsafe { libc::close(sockets[1]) };
    sockets[0]
}

#[test]
fn process_state_distinguishes_live_reaped_and_unknown() {
    let child = sleeping_child();
    assert_eq!(process_state(child), ProcessState::Live);
    cleanup(child);
    assert_eq!(process_state(child), ProcessState::Unknown);
    cleanup(child);
}

#[test]
fn reaped_process_is_not_killed_again() {
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        unsafe { libc::_exit(0) }
    }
    let status = loop {
        if let ProcessState::Reaped(status) = process_state(child) {
            break status;
        }
        std::thread::sleep(Duration::from_millis(1));
    };
    assert_eq!(status, 0);
    assert_eq!(process_state(child), ProcessState::Unknown);
    cleanup(child);
}

#[test]
fn wait_child_preserves_nonzero_exit_after_reap() {
    let child = unsafe { libc::fork() };
    assert!(child >= 0);
    if child == 0 {
        unsafe { libc::_exit(7) }
    }
    let socket = socket();
    let error = super::namespace::wait_child(child, socket)
        .expect_err("nonzero child exit must be preserved");
    assert!(error.to_string().contains("status 1792"));
    assert_eq!(process_state(child), ProcessState::Unknown);
}

#[test]
fn writer_deadline_cleans_both_participants() {
    let target = sleeping_child();
    let writer = sleeping_child();
    let socket = socket();
    let error = wait_map_writer(target, writer, Instant::now() + Duration::from_millis(50))
        .expect_err("blocked writer must time out");
    assert!(error.to_string().contains("timed out"));
    abort_with_writer(target, socket, Some(writer));
    assert_eq!(process_state(target), ProcessState::Unknown);
    assert_eq!(process_state(writer), ProcessState::Unknown);
}

#[test]
fn writer_failure_cleans_target_and_writer() {
    let target = sleeping_child();
    let writer = unsafe { libc::fork() };
    assert!(writer >= 0);
    if writer == 0 {
        unsafe { libc::_exit(7) }
    }
    let socket = socket();
    let error = wait_map_writer(target, writer, Instant::now() + Duration::from_secs(1))
        .expect_err("writer failure must fail closed");
    assert!(error.to_string().contains("status 1792"));
    abort_with_writer(target, socket, Some(writer));
    assert_eq!(process_state(target), ProcessState::Unknown);
    assert_eq!(process_state(writer), ProcessState::Unknown);
}

#[test]
fn target_early_exit_stops_writer_without_reaping_target_first() {
    let target = unsafe { libc::fork() };
    assert!(target >= 0);
    if target == 0 {
        unsafe { libc::_exit(0) }
    }
    let writer = sleeping_child();
    let socket = socket();
    let error = wait_map_writer(target, writer, Instant::now() + Duration::from_secs(1))
        .expect_err("target early exit must stop writer");
    assert!(error.to_string().contains("child exited"));
    assert!(matches!(process_state(writer), ProcessState::Live));
    abort_with_writer(target, socket, Some(writer));
    assert_eq!(process_state(target), ProcessState::Unknown);
    assert_eq!(process_state(writer), ProcessState::Unknown);
}
