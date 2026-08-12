# GitHub Actions desktop builds

Status on 2026-08-13: the native-runner policy is retained, but the checked-in
workflow/build scripts still describe the rejected Tk dev3 baseline. Before the
next candidate run, Phase 2R must add pywebview 6.2.1, platform WebView hidden
imports/data, the Windows `.exe.config`, HTML/CSS/JS/SVG assets, and a hidden
WebView packaged smoke. See
[`MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md`](MS_EVENT_STUDIO_UI_REBUILD_HANDOFF.md).

The workflow mirrors LMA Studio's native-runner policy:

- manual runs default to a macOS ARM64 candidate;
- `macos-14` builds and verifies the real `.app` with an ARM64 Python runtime;
- `windows-2022` builds the x64 onedir bundle;
- both platforms run the full unit suite plus the packaged scientific smoke
  test before archiving;
- artifacts include a ZIP and a SHA-256 sidecar;
- a `v*` tag builds both platforms and publishes a stable GitHub Release;
- a manual run publishes nothing unless `publish_prerelease` is explicitly
  enabled.

The macOS candidate is ad-hoc signed for bundle integrity but is not Apple
notarized. The future WebView candidate must receive packaged smoke, the
standard screenshot/agent pre-UAT gate, and mouse UAT on a real Apple Silicon
Mac before Phase 2R exit.

## First remote build

This local repository currently has no Git remote. After its GitHub repository
has been created, connect and push `main`:

```powershell
git remote add origin <YOUR_GITHUB_REPOSITORY_URL>
git push -u origin main
```

Do not run the workflow as a new UX candidate until the Phase 2R packaging
upgrade is committed. After that gate, open GitHub **Actions → Build and release
desktop packages → Run workflow**:

1. choose `macos`;
2. use the filesystem-safe WebView candidate label recorded by Phase 2R
   (recommended first label: `0.3.0-dev1`);
3. leave `publish_prerelease` off for the first audit;
4. download the `ms-event-studio-macos-arm64` artifact after the run succeeds;
5. verify the ZIP next to its sidecar with `shasum -a 256 -c <file>.sha256`;
6. unzip it on an Apple Silicon Mac and execute the Phase 2R packaged smoke and
   current Chinese UAT guide; do not reuse the dev3 legacy checklist as UX
   acceptance.

Use a `v*` tag only after both native candidates and mouse UAT are accepted.
