//! SPIKE (throwaway, remote-optimize-1214): pause points a parent test
//! process drives through `OMNIGRAPH_SPIKE_PAUSE_AT=<point>[:<ms>],...`.
//! A point with no `:<ms>` (or `:0`) parks forever, so the parent can kill
//! the process there; a positive value sleeps that long and continues. Each
//! crossing prints `SPIKE_PAUSED <point>` on stdout.

use std::time::Duration;

pub async fn pause(point: &str) {
    let Ok(spec) = std::env::var("OMNIGRAPH_SPIKE_PAUSE_AT") else {
        return;
    };
    for item in spec.split(',') {
        let (name, ms) = item.split_once(':').unwrap_or((item, "0"));
        if name != point {
            continue;
        }
        println!("SPIKE_PAUSED {point}");
        let ms: u64 = ms.parse().unwrap_or(0);
        if ms == 0 {
            std::future::pending::<()>().await;
        } else {
            tokio::time::sleep(Duration::from_millis(ms)).await;
        }
    }
}
