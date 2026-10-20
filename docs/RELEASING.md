# Release procedure

This is the operating procedure for agents maintaining Telegram Archive Desktop. Follow it together with [AGENTS.md](../AGENTS.md). The owner's latest explicit instructions take precedence.

## 1. Decide whether publication is authorized

Feature work and release work are separate stages. Finishing an implementation, passing tests, or receiving "looks good" does not by itself authorize publication.

After finishing and checking a feature:

1. Describe the result and relevant validation or limitations.
2. Ask the owner whether there will be more changes. Communicate with the owner in Russian.
3. If more changes are requested, keep the work local, implement and check them, then ask again.
4. When the owner says there are no more changes, ask whether to release now.
5. Only an affirmative release decision starts publication.

Do not ask both questions repeatedly while work is still in progress. Do not treat silence or elapsed time as approval. If the owner declines or postpones a release, keep the changes local.

Explicit commands to **release** or **push** authorize the complete procedure below without another confirmation. For example, "make a release", "release it", "сделай релиз", "релиз", "сделай пуш", and "commit and push" are publication instructions when directed at the current work. A single answer that says there are no more changes and requests a release satisfies both checkpoints.

Discussing the word "release", asking what a push does, or asking to write these instructions does not authorize publication. An earlier release approval does not automatically authorize releases after later feature tasks. A later instruction to wait or add changes supersedes earlier approval.

## 2. Understand what a release means here

| Item | Current behavior |
| --- | --- |
| Repository | `https://github.com/MastaMan/Telegram-Downloader.git` |
| Working and delivery branch | `main`; no feature branches or worktrees |
| Release identity | The full Git commit SHA published on remote `main` |
| Update discovery | `updater.py` queries the public GitHub `commits/main` endpoint |
| ZIP installation | Downloads the source archive pinned to that SHA, validates it, and replaces managed files |
| Git installation | Requires a clean `main` checkout and a fast-forward update |
| User action | Click **Check updates** while the application is idle; installation closes and restarts the application |
| New installation | Extract the repository ZIP and launch `Start.bat` |
| Windows CI | Manual **Windows validation** workflow only |

There is no separate promotion step after pushing `main`: the commit is immediately available to the updater. This applies to documentation-only commits too. Do not push unfinished work to "save it remotely" before release approval.

"Push automatically means release" describes the agent's workflow: an authorized push includes preparation, validation, publication, and verification. It does not mean a GitHub Action creates a release, and it does not mean installed applications update themselves without the user clicking **Check updates**.

The updater does not consult tags, semantic versions, or GitHub Releases. The current `__version__` in `tgzip/__init__.py` is not its update selector. Do not invent a version bump, tag, release asset, or signed installer as a prerequisite. If the owner explicitly requests those artifacts, prepare them in addition to this process and describe their actual relationship to the updater.

## 3. Inspect the candidate

Start from the existing checkout, not a new branch or worktree:

```sh
git status --short
git branch --show-current
git remote -v
git diff --stat
git diff
git diff --cached
git fetch origin main
git log --oneline origin/main..HEAD
git log --oneline HEAD..origin/main
```

- Verify that the branch is `main` and `origin` is the intended repository.
- Review both uncommitted edits and all commits that would be published. Never assume the outgoing range contains only the latest task.
- Identify unrelated files and staged changes; do not include, delete, or silently unstage the owner's work.
- Exclude `.DS_Store`, personal ZIPs such as `Archive.zip`, screenshots made only for investigation, credentials, session files, databases, logs, virtual environments, and downloaded archives.
- If remote `main` advanced, reconcile it without overwriting local work. A clean checkout with no local commits may fast-forward with `git merge --ff-only origin/main`. Divergence needs deliberate review and integration on `main`; never force-push or reset away work.
- Recheck and retest any affected behavior after integration. If a conflict needs the owner's decision, explain the specific conflict and keep publication pending.

## 4. Validate the actual release contents

Use an available supported standard CPython environment with the pinned requirements. Prefer Python 3.14 for the normal Windows launcher path. Additional interpreter checks should match the affected compatibility paths.

Typical commands after activating the chosen development environment:

```sh
python bootstrap.py --check-python
python -m compileall -q tgzip bootstrap.py updater.py tests
python -m unittest discover -s tests -v
```

Set `QT_QPA_PLATFORM=offscreen` before running GUI tests on a headless host. On PowerShell:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```

On macOS/Linux:

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

An existing prepared environment may be used directly; do not reinstall dependencies on every release without a reason. On a non-Windows host, set `TG_TEST_POWERSHELL` to an available PowerShell executable if launcher execution tests are relevant. Paths under `/private/tmp` from earlier sessions are disposable, not project prerequisites.

Choose checks by scope:

| Changed area | Required evidence |
| --- | --- |
| Runtime Python code | Relevant regression checks and the available full unittest suite |
| Interface/layout | Offscreen GUI checks plus a rendered preview inspected at normal and minimum supported window sizes |
| Dependencies, interpreter setup, or launcher | Environment probe, relevant bootstrap/runtime tests, and supported interpreter checks for the changed paths |
| Updater or source packaging | Update staging/apply/recovery tests and inspection of managed-file compatibility |
| Persistence, downloading, or verification | Relevant resume, cancellation, migration, collision, and subprocess-lifetime coverage |
| Documentation/instructions only | Review for accuracy, resolve local links, inspect the diff, and run whitespace checks; do not rerun runtime tests solely for unchanged application code |

For a GUI preview without Telegram credentials:

```sh
python -m tgzip --demo --screenshot preview.png
```

Update `docs/interface-preview.png` when the released interface changes visibly. Do not commit incidental preview files. Demo mode and mock tests are not live Telegram validation.

Record actual results, including skips and unavailable environments. Never describe macOS/offscreen checks as Windows testing, or mocked network operations as successful real-account downloads. Do not copy an older test count as evidence for a new code revision.

Keep `.github/workflows/tests.yml` manual. Do not re-enable automatic runs to satisfy a release checklist. A manual workflow can be started deliberately when Windows validation is needed and available; report its actual result. An account/billing failure before tests run is not a test result. Do not mark failures as passed, delete tests, or silently weaken checks to publish.

Fix failures caused by the candidate before publishing. If an essential check cannot run, state the limitation; do not claim verified behavior. Escalate an unresolved release-blocking defect to the owner rather than publishing it as a successful release.

## 5. Check compatibility and documentation

- Keep `Start.bat` as the only normal Windows launch entry point.
- Preserve the existing application-data location, encrypted credentials/session, selected chat, settings, queue history, `.part` files, completed archives, and quarantine records.
- Database changes must preserve existing data and resume behavior. Archive names and existing files must not be overwritten to simplify an upgrade.
- Review new runtime files against the allowlist in `updater.py`. A new file accepted only by the new updater may still be omitted by an older installed updater. Plan compatibility before publishing; do not assume a repository ZIP and an in-app update install identical sets of files.
- Root `AGENTS.md` is a source-maintenance document and is currently outside the ZIP self-updater allowlist. `docs/RELEASING.md` is an allowed documentation file. Neither is a runtime dependency.
- Update the user guide/README for changed controls or behavior, development notes for architectural changes, and validation notes when new testing evidence or limitations matter.
- Keep documentation in English. Release explanations to the owner remain in Russian.
- Do not overwrite or publish an existing untracked `Archive.zip`. If a distributable ZIP is explicitly requested, build a clean source-only package and inspect its contents before attaching it.

## 6. Commit and publish after approval

Review and stage only the intended files by explicit path. Avoid `git add .` and `git add -A` in a checkout containing unrelated files.

```sh
git diff --check
git add -- <intended-paths>
git diff --cached --check
git diff --cached --stat
git diff --cached
git commit -m "<Describe the delivered change>"
git fetch origin main
git log --oneline HEAD..origin/main
git log --oneline origin/main..HEAD
```

The angle-bracket values above are placeholders, not literal arguments. Review every staged file, including anything that was staged before this task. Do not make an empty commit just to label a release.

If remote commits appeared since validation, reconcile and repeat affected checks before pushing. Once the outgoing candidate is reviewed and ready:

```sh
git push origin main
git rev-parse HEAD
git ls-remote origin refs/heads/main
git status --short
```

Compare the full local SHA with the SHA reported for remote `main`. A successful local commit is not a published release. A failed or ambiguous push is not proof of publication: inspect the remote before retrying. Do not use `--force`.

When possible, also read the public `commits/main` endpoint that the application uses and confirm that it exposes the intended SHA. If someone advanced `main` after the push, inspect that history before making a claim about what the updater now serves.

Do not launch an updater against the owner's running application or modify their download data merely to verify publication. Local updater tests and remote commit checks should be reported separately from an actual Windows end-to-end update.

## 7. Report the outcome precisely

For a completed release, report:

- What the release changes.
- The published short SHA and a link to `https://github.com/MastaMan/Telegram-Downloader/commit/<full-sha>`.
- Checks performed, results, and material limitations.
- How to receive it: **Check updates**, or the repository source ZIP for a new installation.

Do not say that a GitHub Release, tag, ZIP asset, Windows test, or live Telegram check exists unless it was actually created or performed.

If publication has not been approved, state that the work is local and ask the next pending question from section 1. If a release fails, state which steps succeeded and what remains blocked; do not say "released".

## 8. Correct a published defect

Keep published history intact. Prepare a corrective commit or a reviewed revert on `main`, validate it, and follow the same authorization and publication rules. A corrective push produces a new update SHA.

Source rollback does not automatically undo database migrations, downloaded files, filename restoration, or external runtime changes. Review compatibility before reverting; do not delete user data or reset the repository to hide a failed release.
