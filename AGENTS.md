# Agent instructions

## Communication and product

- Respond to the owner in Russian. Keep application UI, source comments, and repository documentation in English.
- This is a standalone Windows application. Preserve the single `Start.bat` launch entry point.
- Work directly in this repository on `main`. Do not create branches or worktrees.
- Preserve unrelated local work and untracked files. Never include credentials, Telegram sessions, downloaded archives, runtime environments, or application databases in commits.

## Release authorization

Read [docs/RELEASING.md](docs/RELEASING.md) before preparing or publishing a release.

**Completing a feature is not permission to publish it.** Implement and validate the requested work, then:

1. Report what is ready and ask whether there will be more changes.
2. If the owner has more changes, implement them and repeat this checkpoint.
3. If the owner says there are no more changes, ask whether to release now.
4. Publish only after that approval.

An explicit instruction such as "release", "make a release", "push", "релиз", or "сделай пуш" already authorizes the full release process; do not ask for the same approval again. If the owner says both "no more changes" and "release it", proceed. A question about releases, a quoted example, or a request to document the process is not a release command. "No more changes" alone is not release approval.

Treat an authorized push as a release: review the complete outgoing changes, run appropriate checks, update relevant documentation, commit the intended files, push `main`, and verify the published commit. Do not reduce it to an unchecked upload. A later request to wait or make further changes overrides earlier publication approval.

Until approval, leave the finished work local and report that it has not been published. Do not infer standing permission to release every future feature from an earlier release request.

## Delivery and validation

- The application's release channel is the public `main` commit, delivered through **Check updates**. Any push to `main`, including documentation-only commits, is publicly available immediately. Never use a push merely to stage an unapproved release.
- Tags and GitHub Releases are not used by the updater. Do not create them or introduce release automation unless explicitly requested.
- Keep Windows validation manual (`workflow_dispatch`). Do not restore automatic push, pull-request, or tag triggers; the owner requested avoiding the resulting notification emails.
- Use tests appropriate to the changed behavior and inspect GUI previews for visual changes. Distinguish local/offscreen tests from real Windows and live Telegram validation.
- Preserve update compatibility, existing queues, partial downloads, original filenames, encrypted credentials, settings, and archive-check state.
- Never force-push, rewrite published history, or discard local changes to make a release succeed.
