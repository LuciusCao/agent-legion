//! Buffered stdout sink for the event stream (#577).
//!
//! The original sink wrote every event line with `write_all` + `flush` — at
//! least two syscalls per event. Under many concurrent agent processes the
//! per-line write/flush fan-out saturates the host (fseventsd load, pipe
//! reader wakeups). This sink batches event lines in an 8 KiB [`BufWriter`]
//! and flushes when the buffer fills or when at least `flush_interval` has
//! elapsed since the previous flush. The interval check is LAZY (evaluated
//! on each `emit`, no timer thread): a burst of events within one interval
//! produces one flush, and the first event after a quiet gap goes out
//! immediately.
//!
//! The interval defaults to 200 ms and is tunable via
//! `VELITES_EVENT_FLUSH_INTERVAL_MS`; `0` restores the legacy
//! flush-per-event behavior.
//!
//! Trade-off (accepted in #577): a process killed before its buffer drains
//! (SIGKILL) loses the unflushed tail — bounded by one burst of events.
//! Normal completion, graceful SIGTERM cancellation, and harness-error
//! exits all flush on [`Drop`], and `events.jsonl` is an execution trace
//! the Host archives with retry/fallback semantics, so the bounded tail
//! loss is tolerable.
//!
//! All sink handles in the process share ONE buffer (the retry path in
//! `lib::run_real_provider` constructs a second handle mid-run): with
//! private buffers a later handle's flush could overtake events still
//! buffered in an earlier handle, breaking event order on the wire. Dropping
//! any handle flushes the shared buffer, so the retry path's short-lived
//! handles still publish their `message_end` + `auto_retry_start` pair
//! before the backoff sleep.

use std::io::{self, BufWriter, Write};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use crate::events::{Event, EventSink};

/// Env override for the flush interval in milliseconds (`0` = flush every
/// event, the pre-#577 behavior).
pub const ENV_FLUSH_INTERVAL_MS: &str = "VELITES_EVENT_FLUSH_INTERVAL_MS";

/// Flush interval when the env var is unset or invalid.
const DEFAULT_FLUSH_INTERVAL: Duration = Duration::from_millis(200);

/// Event lines accumulate up to this many bytes before a forced flush.
const BUFFER_CAPACITY: usize = 8 * 1024;

/// Parse the raw env value; anything unparseable falls back to the default
/// with a stderr warning (a tuning knob must never fail the run).
fn parse_flush_interval(raw: Option<&str>) -> Duration {
    match raw {
        None => DEFAULT_FLUSH_INTERVAL,
        Some(raw) => match raw.parse::<u64>() {
            Ok(ms) => Duration::from_millis(ms),
            Err(_) => {
                eprintln!(
                    "velites: invalid {ENV_FLUSH_INTERVAL_MS} value `{raw}`; using default {}ms",
                    DEFAULT_FLUSH_INTERVAL.as_millis()
                );
                DEFAULT_FLUSH_INTERVAL
            }
        },
    }
}

/// Inner-writer shim recording whether the BufWriter actually drained to the
/// underlying writer during the current `write_line`. BufWriter's drain
/// points vary with fill level and line size, so watching the inner writer
/// is the only reliable signal; a drain is a real flush and must resync the
/// interval clock, otherwise a sustained large-event flow keeps `last_flush`
/// stale and pays an explicit flush per event.
struct DrainTracker {
    inner: Box<dyn Write + Send>,
    drained: bool,
}

impl Write for DrainTracker {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        self.drained = true;
        self.inner.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

/// Buffered writer plus flush bookkeeping, behind the sink's mutex.
struct SinkState {
    writer: BufWriter<DrainTracker>,
    flush_interval: Duration,
    /// `None` = nothing flushed yet: the FIRST event must reach the pipe
    /// immediately regardless of the configured interval — the run's opening
    /// events (session / agent_start) must not linger in the buffer behind
    /// a long first model call. (A backdated `Instant` cannot express this:
    /// subtracting a huge interval can leave the clock's representable
    /// range.)
    last_flush: Option<Instant>,
    failure_reported: bool,
}

impl SinkState {
    fn new(writer: Box<dyn Write + Send>, flush_interval: Duration) -> Self {
        Self {
            writer: BufWriter::with_capacity(
                BUFFER_CAPACITY,
                DrainTracker {
                    inner: writer,
                    drained: false,
                },
            ),
            flush_interval,
            last_flush: None,
            failure_reported: false,
        }
    }

    fn write_line(&mut self, line: &[u8]) -> io::Result<()> {
        self.writer.get_mut().drained = false;
        self.writer.write_all(line)?;
        self.writer.write_all(b"\n")?;
        // A capacity-triggered drain is a real flush: resync the clock so a
        // sustained large-event flow doesn't pay an explicit flush per event.
        if self.writer.get_ref().drained {
            self.last_flush = Some(Instant::now());
            return Ok(());
        }
        let due = match self.last_flush {
            None => true,
            Some(last) => last.elapsed() >= self.flush_interval,
        };
        if due {
            self.writer.flush()?;
            self.last_flush = Some(Instant::now());
        }
        Ok(())
    }

    /// The one-stderr-line failure report the [`EventSink`] contract
    /// promises; subsequent failures stay silent.
    fn report_failure(&mut self) {
        if !self.failure_reported {
            self.failure_reported = true;
            eprintln!("velites: failed to write event to stdout; further event loss stays silent");
        }
    }
}

/// Emits events as compact NDJSON on stdout (the worker pipes this into
/// `events.jsonl`), batched through the process-wide shared buffer — see
/// the module docs for the flush policy and the accepted tail-loss window.
///
/// Per the [`EventSink`] contract a write failure must never fail the agent
/// loop; the FIRST failure is still reported as one stderr line, so a run
/// whose event stream died (closed pipe, full disk) leaves a trace.
pub struct StdoutJsonlSink {
    state: Arc<Mutex<SinkState>>,
}

impl StdoutJsonlSink {
    pub fn new() -> Self {
        static SHARED: OnceLock<Arc<Mutex<SinkState>>> = OnceLock::new();
        let state = SHARED.get_or_init(|| {
            let interval =
                parse_flush_interval(std::env::var(ENV_FLUSH_INTERVAL_MS).ok().as_deref());
            Arc::new(Mutex::new(SinkState::new(Box::new(io::stdout()), interval)))
        });
        Self {
            state: Arc::clone(state),
        }
    }
}

impl Default for StdoutJsonlSink {
    fn default() -> Self {
        Self::new()
    }
}

impl EventSink for StdoutJsonlSink {
    fn emit(&mut self, event: &Event) {
        let line = serde_json::to_string(event).expect("event serialization cannot fail");
        let mut state = self.state.lock().expect("event sink poisoned");
        if state.write_line(line.as_bytes()).is_err() {
            state.report_failure();
        }
    }
}

impl Drop for StdoutJsonlSink {
    fn drop(&mut self) {
        // Normal-completion guarantee (#577): the buffered tail must reach
        // the pipe when the run ends. stdout dying mid-run can surface its
        // FIRST real I/O error only here (earlier emits merely filled the
        // BufWriter), so a failed final flush gets the same one-line report
        // as emit — still never panicking or changing the exit code. A
        // poisoned lock keeps the silent-swallow behavior.
        if let Ok(mut state) = self.state.lock() {
            if state.writer.flush().is_err() {
                state.report_failure();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{
        AgentStartEvent, Message, MessageStartEvent, TurnEndEvent, TurnStartEvent,
    };

    /// In-memory writer: receives bytes only when the BufWriter flushes.
    #[derive(Clone, Default)]
    struct VecWriter(Arc<Mutex<Vec<u8>>>);

    impl Write for VecWriter {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.0.lock().expect("poisoned").extend_from_slice(buf);
            Ok(buf.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    struct FailingWriter;

    impl Write for FailingWriter {
        fn write(&mut self, _buf: &[u8]) -> io::Result<usize> {
            Err(io::Error::other("boom"))
        }

        fn flush(&mut self) -> io::Result<()> {
            Err(io::Error::other("boom"))
        }
    }

    fn test_sink_with_clock(
        interval: Duration,
        last_flush: Option<Instant>,
    ) -> (StdoutJsonlSink, VecWriter) {
        let writer = VecWriter::default();
        let mut state = SinkState::new(Box::new(writer.clone()), interval);
        state.last_flush = last_flush;
        let sink = StdoutJsonlSink {
            state: Arc::new(Mutex::new(state)),
        };
        (sink, writer)
    }

    fn test_sink(interval: Duration) -> (StdoutJsonlSink, VecWriter) {
        test_sink_with_clock(interval, None)
    }

    fn flushed(writer: &VecWriter) -> String {
        String::from_utf8(writer.0.lock().expect("poisoned").clone()).expect("utf8")
    }

    #[test]
    fn batches_events_until_drop_flushes_the_tail() {
        let (mut sink, writer) = test_sink(Duration::from_secs(3600));
        let event = Event::AgentStart(AgentStartEvent {});
        sink.emit(&event); // first event flushes immediately (never flushed yet)
        sink.emit(&event);
        sink.emit(&event);
        assert_eq!(flushed(&writer).lines().count(), 1, "burst stays buffered");
        drop(sink);
        let text = flushed(&writer);
        assert_eq!(text.lines().count(), 3, "drop flushes the tail");
        assert!(text.lines().all(|line| line.contains("agent_start")));
    }

    #[test]
    fn zero_interval_flushes_every_event() {
        let (mut sink, writer) = test_sink(Duration::ZERO);
        let event = Event::AgentStart(AgentStartEvent {});
        sink.emit(&event);
        sink.emit(&event);
        assert_eq!(flushed(&writer).lines().count(), 2);
    }

    #[test]
    fn elapsed_interval_flushes_pending_events() {
        let (mut sink, writer) = test_sink(Duration::from_millis(25));
        let event = Event::AgentStart(AgentStartEvent {});
        sink.emit(&event);
        sink.emit(&event);
        assert_eq!(flushed(&writer).lines().count(), 1, "within the interval");
        std::thread::sleep(Duration::from_millis(50));
        sink.emit(&event);
        assert_eq!(flushed(&writer).lines().count(), 3, "interval elapsed");
    }

    #[test]
    fn handles_sharing_one_state_keep_event_order() {
        // The retry path builds a second handle mid-run; a per-handle buffer
        // would let that handle's flush overtake events the first handle
        // still holds.
        let writer = VecWriter::default();
        let state = Arc::new(Mutex::new(SinkState::new(
            Box::new(writer.clone()),
            Duration::from_secs(3600),
        )));
        let mut first = StdoutJsonlSink {
            state: Arc::clone(&state),
        };
        let mut second = StdoutJsonlSink { state };
        first.emit(&Event::TurnStart(TurnStartEvent { turn_index: 1 })); // flushed
        first.emit(&Event::TurnStart(TurnStartEvent { turn_index: 2 })); // buffered
        second.emit(&Event::TurnEnd(TurnEndEvent { turn_index: 2 })); // buffered
        drop(second); // flushes the shared buffer
        let text = flushed(&writer);
        let tags: Vec<&str> = text
            .lines()
            .map(|line| {
                if line.contains("turn_start") {
                    "start"
                } else {
                    "end"
                }
            })
            .collect();
        assert_eq!(tags, ["start", "start", "end"]);
    }

    #[test]
    fn oversized_event_line_writes_through() {
        let (mut sink, writer) = test_sink(Duration::from_secs(3600));
        let big = "x".repeat(BUFFER_CAPACITY * 2);
        sink.emit(&Event::MessageStart(MessageStartEvent {
            message: Message::user(big),
        }));
        assert!(flushed(&writer).contains("message_start"));
    }

    #[test]
    fn write_failures_never_panic_and_stay_silent_after_the_first() {
        let mut sink = StdoutJsonlSink {
            state: Arc::new(Mutex::new(SinkState::new(
                Box::new(FailingWriter),
                Duration::ZERO,
            ))),
        };
        let event = Event::AgentStart(AgentStartEvent {});
        sink.emit(&event);
        sink.emit(&event);
        drop(sink);
    }

    #[test]
    fn first_event_flushes_immediately_even_with_huge_interval() {
        // Regression (#581 review): the old backdated-Instant trick could not
        // backdate past the clock's representable range, so a huge interval
        // on a freshly booted host buffered the opening events behind a long
        // first model call. `None` (never flushed) makes the first-event
        // guarantee unconditional.
        let (mut sink, writer) = test_sink(Duration::from_millis(u64::MAX));
        sink.emit(&Event::AgentStart(AgentStartEvent {}));
        assert_eq!(flushed(&writer).lines().count(), 1);
    }

    #[test]
    fn capacity_drain_resyncs_the_flush_clock() {
        // A capacity-triggered drain is a real flush: without the resync a
        // sustained large-event flow keeps last_flush stale and pays an
        // explicit flush per event once the interval is overdue.
        let stale = Instant::now();
        std::thread::sleep(Duration::from_millis(2));
        let (mut sink, _writer) = test_sink_with_clock(Duration::from_secs(3600), Some(stale));
        let big = "x".repeat(BUFFER_CAPACITY * 2);
        sink.emit(&Event::MessageStart(MessageStartEvent {
            message: Message::user(big),
        }));
        let last_flush = sink.state.lock().expect("poisoned").last_flush;
        assert!(last_flush > Some(stale), "the drain counts as a flush");
    }

    #[test]
    fn drop_reports_a_first_flush_failure_once() {
        // stdout died while emits only filled the BufWriter: the first real
        // I/O error surfaces at the final Drop flush and must still produce
        // the one stderr warning the EventSink contract promises.
        let mut sink = StdoutJsonlSink {
            state: Arc::new(Mutex::new(SinkState::new(
                Box::new(FailingWriter),
                Duration::from_secs(3600),
            ))),
        };
        // The emit path never flushes: the interval is not due.
        sink.state.lock().expect("poisoned").last_flush = Some(Instant::now());
        sink.emit(&Event::AgentStart(AgentStartEvent {}));
        assert!(
            !sink.state.lock().expect("poisoned").failure_reported,
            "no real I/O happened yet"
        );
        let state = Arc::clone(&sink.state);
        drop(sink);
        assert!(
            state.lock().expect("poisoned").failure_reported,
            "the drop flush reports the first failure"
        );
    }

    #[test]
    fn parse_flush_interval_falls_back_to_default() {
        assert_eq!(parse_flush_interval(None), DEFAULT_FLUSH_INTERVAL);
        assert_eq!(parse_flush_interval(Some("0")), Duration::ZERO);
        assert_eq!(
            parse_flush_interval(Some("500")),
            Duration::from_millis(500)
        );
        assert_eq!(parse_flush_interval(Some("nope")), DEFAULT_FLUSH_INTERVAL);
        assert_eq!(parse_flush_interval(Some("-5")), DEFAULT_FLUSH_INTERVAL);
    }
}
