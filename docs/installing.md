# Installing Stealth

Stealth is installed from the project's
[Releases page](https://github.com/Anish25012/stealth/releases). Use the newest release that
starts with "Stealth" and ignore the one called "broker-list", which holds the company list the
app downloads by itself.

| File | For |
|---|---|
| `Stealth-<version>-windows-setup.exe` | Windows 10 and 11. This is the one to use |
| `Stealth-<version>-macos-<arch>.dmg` | Mac. Built and checked in CI, but untested on a real Mac ([#1](https://github.com/Anish25012/stealth/issues/1)) |
| `Stealth-<version>-linux-<arch>.tar.gz` | Linux. Best effort |
| `SHA256SUMS` | The checksum of every file above |

The installer doesn't need administrator rights. It installs to
`%LOCALAPPDATA%\Programs\Stealth` and adds Stealth to the Start menu.

For a step-by-step guide to setting it up once it's installed, see
[Getting started](getting-started.md).

## Why Windows warns about it

Stealth isn't code-signed. Signing proves who published a program, and Windows trusts signed
programs more. A certificate costs hundreds of pounds a year and needs a registered business or a
verified identity, which isn't worth it for a free tool shared with family and friends.

So the first time you run the installer, Windows SmartScreen may show **"Windows protected your
PC"**. To carry on:

1. Choose **More info**.
2. Check the app name is `Stealth-<version>-windows-setup.exe` and the publisher is
   "Unknown publisher".
3. Choose **Run anyway**.

If there's no **Run anyway** button, the PC may be set to install apps from the Microsoft Store
only. Change it in Windows **Settings** › **Apps** › **Advanced app settings** › **Choose where to
get apps**, or ask whoever looks after the PC.

Antivirus such as Norton or Avast may also warn about, block or quarantine an unsigned app. See
[Stealth and antivirus](antivirus.md) for what to do, and how to report it as a false positive.

## Checking the download is genuine

Do this before choosing **Run anyway** if you want to be sure the file is the one the project
built. It takes a minute.

**Check the checksum.** In PowerShell:

```powershell
Get-FileHash "$env:USERPROFILE\Downloads\Stealth-*-windows-setup.exe"
```

The hash shown must match the line for that file in `SHA256SUMS` on the release page. GitHub also
shows each file's SHA-256 next to it under **Assets**.

**Check where it was built (optional).** Every release file is built by GitHub Actions from the
public source code, never on someone's own PC, and GitHub records proof of that. With the
[GitHub CLI](https://cli.github.com/) installed:

```powershell
gh attestation verify "$env:USERPROFILE\Downloads\Stealth-0.1.0-windows-setup.exe" -R Anish25012/stealth
```

It should say the file was built by the `CI` workflow in `Anish25012/stealth`. If either check
fails, don't run the file, and please [open an issue](https://github.com/Anish25012/stealth/issues).

## Updating

Stealth checks GitHub for a new version once a day (you can turn this off in **Settings** ›
**Updates**). When there is one, it tells you in Settings and with a notice by the clock. It
doesn't download or install anything itself: download the new installer from the Releases page
and run it over the old one. Your records and settings are kept.

The list of companies is different: it updates by itself, and only if it's signed with the
project's key. A list that has been tampered with, or an older one, is ignored. See
[Fixing the broker list](../README.md#fixing-the-broker-list).

## Uninstalling

Windows **Settings** › **Apps** › **Stealth** › **Uninstall**. This removes the app but keeps your
encrypted records in `%LOCALAPPDATA%\Stealth`, so reinstalling picks up where you left off. To
remove those too, delete that folder after uninstalling. The email app password stays in Windows
Credential Manager under "stealth-privacy" until you delete it there, or revoke it in your email account.

## For maintainers: making a release

1. Set `__version__` in `src/stealth/__init__.py` and `version` in `pyproject.toml` to the new
   version, and commit.
2. Tag it and push the tag: `git tag v0.2.0 && git push origin v0.2.0`.
3. CI builds and tests on every platform as usual. The release job then checks the tag matches
   the version, writes `SHA256SUMS`, records build provenance, and creates a **draft** release
   with every file attached.
4. Read the draft on GitHub, install the Windows build on a real PC, then publish it. Apps only
   see published releases, never drafts or prereleases.
