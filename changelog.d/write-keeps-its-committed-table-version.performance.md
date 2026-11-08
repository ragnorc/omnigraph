- A write no longer re-reads the table version it just committed: once its
  publication advances the branch, the writer keeps that version in the
  read-handle cache, so the next write or read of the table on the same
  handle opens nothing while it stays cached.
