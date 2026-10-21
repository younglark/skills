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

**Success:** `unity --version` works, `unity auth status` is signed in, needed Editor appears in `unity editors -i`, and `unity license status` shows `active: true` with required modules present.

**Stop and ask before:** returning someone else's seat, removing activations in Unity ID that you did not create for this task, or using a serial/service-account secret the user did not provide for this machine.

## Prerequisites

- Linux: glibc **2.34+** (Ubuntu 22.04+ / Debian 12+). Check: `ldd --version`.
- Disk: Editor tarball is ~3GB; leave several GB free under the install home (default often `~/Unity/Hub/Editor`).
- Network:
  - Editor downloads: `download.unity3d.com` (usually reachable).
  - Sign-in / many license APIs: `api.unity.com` / `services.api.unity.com`.
  - If `curl -I https://api.unity.com/` returns **403** with "Request forbidden by administrative rules", Hub/CLI OAuth and some Hub flows fail until those hosts are allowlisted—or OAuth is finished on a network that can reach them while the CLI waits for the local callback. CDN allowlist alone is not enough for auth.

## 1. Install Unity CLI

```bash
curl -fsSL https://unity.com/install.sh | bash
unity --version
```

- Default binary: `~/.local/bin/unity` (ensure `~/.local/bin` is on `PATH`).
- Update later: `unity self-update` (or package manager if installed via brew/apt).
- Sanity: `unity doctor`.

## 2. Sign in (`unity auth`)

CLI **does not** accept email/password flags. Options:

```bash
unity auth login          # opens browser OAuth; wait for callback
unity auth status
unity auth list
```

- Service account (CI): `unity auth login --client-id <id> --client-secret <secret>` (prefer `--secret-from-stdin`).
- Browser flow often starts at `id.unity.com` / `login.unity.com` and may require **2FA**. Complete 2FA in the browser; keep `unity auth login` running until it exits 0.
- If the printed Sign-in URL hits `api.unity.com` and egress blocks it, the page may 403. Workarounds: allowlist `api.unity.com` and `services.api.unity.com`, or finish OAuth on a reachable network while the CLI waits for the callback.
- Never put passwords, OTP codes, or client secrets in chat logs; use secure forms / handoff for human 2FA.

## 3. Install an Editor

```bash
unity editors -i
unity install <VERSION> -m <module...> --accept-eula -y --non-interactive
# example
unity install 2021.3.44f1 --accept-eula -y --non-interactive
```

- List module IDs for a version: `unity install <VERSION> --list-modules`.
- Add modules later: `unity install-modules -e <VERSION> -m <id...> --accept-eula -y`.
- Large downloads can hit **timeout** near 100%; CLI often retries. Resume with `--resume` if needed. Watch progress in command output / Hub download cache (often under `~/.config/unityhub/downloads/`).
- Confirm: `unity editors -i` shows version and path (e.g. `~/Unity/Hub/Editor/<VERSION>/Editor/Unity`).

### Linux module notes

On a **Linux** Editor install, Hub module IDs often include `linux-il2cpp` and `linux-server`, and **may not** include `linux-mono`. A missing `-m` id is skipped with a warning (`Skipping`); the Editor can still install. Linux Editor can usually build **Mono** Linux players without a separate mono module; install `linux-il2cpp` when IL2CPP Linux builds are required.

On **macOS/Windows** Editors, Linux Build Support module names differ—always `--list-modules` for that Editor.

## 4. License activate / return

```bash
unity license status
unity license
unity license activate --personal --accept-eula
# or
unity license activate --serial <SERIAL>
unity license activate --file path/to/license.ulf
unity license return -y
```

- Auth and license are **separate**: signed in does not always mean a license is active. Check `unity license status` (`active: true` and a product row).
- `--personal` may still attach an **Assigned Pro/Plus** seat if the Unity ID has one; trust `unity license status` product field, not the flag name.
- **Activation limit**: free a seat before retrying.
  - On a machine that holds the license: Hub → Preferences → Licenses → Return, or `unity license return -y`, or Hub sign-out for Personal.
  - Without that machine: [Unity ID](https://id.unity.com/) → subscriptions / seats / serial activations UI → remove selected activations (labels vary by locale). Personal serial activations are often capped (e.g. 2). Leave unrelated org Pro seats alone unless the user asks.
- After freeing seats, re-run `unity license activate ...` on the target machine.

## 5. Quick verify checklist

1. `unity --version` works.
2. `unity auth status` shows signed in.
3. `unity editors -i` lists the needed version.
4. `unity license status` shows `active: true`.
5. Needed platform modules present (`--list-modules` / `editors` modules column).

## Common failures

| Symptom | Likely cause | What to try |
| --- | --- | --- |
| `api.unity.com` 403 administrative rules | Egress proxy | Allowlist Unity API hosts; do not assume download CDN allowlist is enough |
| `AUTH_LOGIN_FAILED` / login timeout | OAuth or 2FA not finished | Keep browser flow open; complete 2FA; check CLI still waiting |
| `Missing module "…" Skipping` | Wrong module id for this OS Editor | `unity install <ver> --list-modules` |
| Download 100% then timeout / retry | Transient / slow validate | Wait for retries; `--resume`; check disk space |
| Activation limit | Seats full | Return on old machine or remove activations in Unity ID |
| License none active after login | Never activated | `unity license activate --personal --accept-eula` or serial/file |
