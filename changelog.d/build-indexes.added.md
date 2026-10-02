- `omnigraph build-indexes --branch <branch>` builds the declared indexes a
  branch's tables lack and keeps the ones they have, in one graph commit on
  that branch. `optimize` reaches only `main`, and `rebuild-full-text-indexes`
  replaces every full-text index; this is the command a branch needs after a
  type gains its first rows there. See [building indexes][build-indexes-guide].

[build-indexes-guide]: ../docs/user/operations/maintenance.md#build-indexes
