# GitHub Actions desktop builds

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
notarized. It must still receive mouse UAT on a real Mac before Phase 2 exit.

## First remote build

This local repository currently has no Git remote. After its GitHub repository
has been created, connect and push `main`:

```powershell
git remote add origin <YOUR_GITHUB_REPOSITORY_URL>
git push -u origin main
```

Then open GitHub **Actions → Build and release desktop packages → Run workflow**:

1. choose `macos`;
2. use a filesystem-safe candidate label such as `0.2.0-dev1`;
3. leave `publish_prerelease` off for the first audit;
4. download the `ms-event-studio-macos-arm64` artifact after the run succeeds;
5. verify the ZIP next to its sidecar with `shasum -a 256 -c <file>.sha256`;
6. unzip it on an Apple Silicon Mac and execute the Chinese guided test.

Use a `v*` tag only after both native candidates and mouse UAT are accepted.
