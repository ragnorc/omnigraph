- A write keeps the table version it committed in the read-handle cache once
  its publication advances the branch, so the next write or read of that table
  on the same handle does not reopen it.
