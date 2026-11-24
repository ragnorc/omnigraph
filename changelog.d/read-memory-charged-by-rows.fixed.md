- A read near `query_memory_bytes` no longer turns from an answer into a refusal after `index build` or `optimize`: it is charged for the rows it holds, not the decoded pages they came from. See [settings][read-memory-settings].

[read-memory-settings]: ../docs/user/queries/settings.md
