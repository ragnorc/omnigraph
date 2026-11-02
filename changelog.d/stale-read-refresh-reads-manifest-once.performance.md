- A read after another process commits refreshes `__manifest` with one probe
  and one read of the new version instead of two each, about half its
  requests. See the [freshness probe][stale-read].

[stale-read]: ../docs/dev/lance.md#current-compatibility-fences
