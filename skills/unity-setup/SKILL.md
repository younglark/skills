---
name: Unity setup (Linux CLI)
description: >-
  Use when setting up Unity on a Linux box or CI host: install the Unity CLI,
  sign in (browser OAuth or service account, including 2FA), install an Editor
  version and modules, and activate or return a license.
---
# Unity setup (Linux CLI)

Install and license Unity from the terminal with the Unity CLI (no Hub GUI). Prefer this on headless or egress-limited Linux machines.

Official overview: https://docs.unity.com/en-us/unity-cli/use-unity-cli

**Success:** `unity --version` works, `unity auth status` is signed in, needed Editor appears in `unity editors -i`, `unity license status` shows `active: true`, and a batchmode build of a small test project succeeds.

**Stop and ask before:** returning someone else's seat, removing activations in Unity ID that you did not create for this task, or using a serial/service-account secret the user did not provide for this machine.

## Prerequisites

- Linux: glibc **2.34+** (Ubuntu 22.04+ / Debian 12+). Check: `ldd --version`.
- Disk: Editor tarball is ~3-4.5GB, unpacked install ~6.5GB; leave several GB free under the install home (default `~/Unity/Hub/Editor`).
- Runtime deps (Debian 13 names): libgtk-3-0t64, libglu1-mesa, libnss3, libasound2t64, libxss1, libxtst6, libcap2, xvfb, ca-certificates, cpio, 7zip.
- Network:
  - Editor downloads: `download.unity3d.com` (usually reachable).
  - Sign-in / license APIs: `api.unity.com` / `services.api.unity.com`.
  - `curl -I https://api.unity.com/` may return **403** "Request forbidden by administrative rules". Sign-in can still work: see section 2.

## 1. Install Unity CLI

```bash
curl -fsSL https://unity.com/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"   # non-login shells may lack it
unity --version
unity doctor
```

- Default binary: `~/.local/bin/unity`. Update later: `unity self-update`.
- In non-TTY shells output defaults to TSV; pass `--format human` to read it.
- List releases: `unity releases [--lts]` or `unity editors -r`. Inspect one before install: `unity editors info <ver> --format human`.

## 2. Sign in (`unity auth`)

CLI **does not** accept email/password flags.

```bash
unity auth login          # prints a Sign-in URL and waits
unity auth status
```

- On a headless box, run `unity auth login` in the background (log its output), and give the user the printed `services.api.unity.com/app-linking/...` Sign-in URL to open **on their own device**. The callback goes through Unity's app-linking channel, so this works even when the box gets 403 from `api.unity.com`. The user completes login and 2FA there; the CLI then prints "You are now signed in."
- Service account (CI): `unity auth login --client-id <id> --secret-from-stdin`. Service-account sessions **cannot activate licenses**.
- Never put passwords, OTP codes, or client secrets in chat logs.

## 3. Install an Editor

```bash
unity install <VERSION> [-m <module...>] --accept-eula -y --non-interactive
unity editors -i --format human
```

- Takes several minutes; run in the background. Resume with `--resume` after a timeout.
- `unity install <VERSION> --list-modules` only works once that Editor is installed; before that use `unity editors info <ver>`.
- Add modules later: `unity install-modules -e <VERSION> -m <id...> --accept-eula -y`.

### Linux module notes

On a **Linux** Editor, module IDs include `linux-il2cpp` and `linux-server` but **not** a Linux Mono module. Mono Linux players are built in (`Editor/Data/PlaybackEngines/LinuxStandaloneSupport/Variations/*mono*`). Install `linux-il2cpp` only for IL2CPP Linux builds. On macOS/Windows Editors, module names differ.

`ldd` on bundled `libembree.so` / `libOpenImageDenoise` may report `libtbb` not found; those ship inside the Editor folder, so it's a false positive.

## 4. License activate / return

```bash
unity license status
unity license activate --personal --accept-eula
unity license activate --serial <SERIAL>
unity license activate --file path/to/license.ulf
unity license return -y
```

- Auth and license are **separate**: signed in does not mean a license is active.
- `--personal` may attach an **Assigned Pro/Plus** seat if the Unity ID has one; trust the `unity license status` product field and tell the user which product it took.
- **Activation limit**: return on the old machine (`unity license return -y`) or remove activations at [Unity ID](https://id.unity.com/). Leave unrelated org seats alone unless asked.
- Batchmode logs may show `Access token is unavailable` / `No ULF license found` (code 500) even when the license works; check for `Pro License: YES` or a successful run instead.

## 5. Verify with a batchmode build

```bash
E=~/Unity/Hub/Editor/<VERSION>/Editor/Unity
xvfb-run -a $E -batchmode -nographics -quit -createProject <TEST_PROJECT_DIR> -logFile /tmp/create.log
xvfb-run -a $E -batchmode -nographics -quit -projectPath <TEST_PROJECT_DIR> -executeMethod BuildScript.Build -logFile /tmp/build.log
```

- Always pass `-projectPath`; without it Unity treats the current folder as a project.
- `BuildScript.Build` (in `Assets/Editor/`): ensure a scene exists, switch to the target (e.g. StandaloneLinux64), set the scripting backend (`PlayerSettings.SetScriptingBackend(BuildTargetGroup.Standalone, ScriptingImplementation.Mono2x)`), call `BuildPipeline.BuildPlayer`, and `EditorApplication.Exit(0/1)`.
- Look for `Build Finished, Result: Success.` Ignore ALSA/FMOD audio errors and the REST port 38000-38100 error on headless boxes.

## Unity 2021.x on newer distros (Debian 13 / Ubuntu 24+)

1. **Script compilation hangs** at `bee_backend ... --stdin-canary` with 0% CPU. Fix: rename `Editor/Data/bee_backend` to `bee_backend_real` and replace it with a wrapper script that drops `--stdin-canary` and execs `bee_backend_real "$@"`. Redo after reinstalling the Editor; undo with `mv bee_backend_real bee_backend`.
2. **`No usable version of libssl was found` / exit 134** during compilation: the bundled .NET 5 needs OpenSSL 1.1. Install Debian 11's `libssl1.1` package (from deb.debian.org) alongside libssl3. It is end-of-life; mention that to the user.

## Common failures

| Symptom | Likely cause | What to try |
| --- | --- | --- |
| `api.unity.com` 403 administrative rules | Egress proxy | Finish the app-linking sign-in URL on the user's device; allowlist for other API needs |
| `AUTH_LOGIN_FAILED` / login timeout | OAuth or 2FA not finished | Keep `unity auth login` running; user completes it in browser |
| `Missing module "…" Skipping` | Wrong module id for this OS Editor | `unity editors info <ver>` |
| Download timeout near 100% | Transient | Wait for retries; `--resume`; check disk |
| `No valid Unity Editor license found` | Never activated | `unity license activate --personal --accept-eula` |
| Activation limit | Seats full | Return on old machine or remove activations in Unity ID |
| Hang at `bee_backend --stdin-canary` | Unity 2021 Linux bug | bee_backend wrapper (above) |
| `No usable version of libssl` | OpenSSL 3 only | Install libssl1.1 |
