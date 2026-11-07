//! Process-wide live heap bytes of the lib test binary, counted by the
//! global allocator in `engine::operators::text_match`'s tests. Instruments
//! that span many threads (a Lance build's workers) read the peak here.

use std::sync::atomic::{AtomicIsize, Ordering};

static LIVE: AtomicIsize = AtomicIsize::new(0);
static PEAK: AtomicIsize = AtomicIsize::new(0);

/// Called by the allocator for every block it hands out or takes back.
pub(crate) fn record(bytes: isize) {
    let live = LIVE.fetch_add(bytes, Ordering::Relaxed) + bytes;
    PEAK.fetch_max(live, Ordering::Relaxed);
}

/// The live bytes now.
pub(crate) fn live() -> isize {
    LIVE.load(Ordering::Relaxed)
}

/// Restart the peak at the live bytes now.
pub(crate) fn reset_peak() {
    PEAK.store(LIVE.load(Ordering::Relaxed), Ordering::Relaxed);
}

/// The most live bytes since the last [`reset_peak`].
pub(crate) fn peak() -> isize {
    PEAK.load(Ordering::Relaxed)
}
