# Convenify Desktop

Double-click **Convenify.exe** in the repository's root folder. The window uses
Windows WPF and the bundled Python and FFmpeg; no new Python GUI package, web
server, browser, administrator access or separate runtime installation is needed
on a Windows 10/11 machine with .NET Framework 4.8. Keep the executable beside
`GUI`, `python`, `savify-new` and `Savify_automation`. The optional
`Convenify-GUI.bat` and `Convenify.vbs` also open the executable.

The window opens maximized within the monitor's usable area. Forms use two
columns when space permits and return to one column in a narrower window.
Related short controls share rows, while paths, queue text and tables retain the
space they need. The form and resizable log share the available height, and the
restored window size is limited to the screen's usable dimensions.
Dragging the log divider resizes the form above it. The action bar keeps a fixed
height immediately above the console, so its Start button stays visible even
when the console takes up most of the window.

Hover over an option or its label for an explanation of its effect, defaults and
relevant requirements. Actions, navigation and log controls also have tooltips.
In **Settings & setup > Appearance**, choose **Default** (vibrant magenta/purple),
**Ocean**, **Forest**, or **Plum**, then use **Dark mode** to select the dark or
light variant of that palette. Dark mode is enabled by default. Both choices
update immediately and save independently for the next launch, separately from
library and credential settings. Older Violet / Pink and Midnight preferences
migrate to Default and Plum. The sidebar uses `GithubBanner.png` from
the Convenify folder; keep that image beside the executable.

## First run

1. Open **Settings & setup** and choose the default music folder.
2. Enter your Spotify client ID and secret, then select **Save settings**.
   Existing Windows user environment credentials are recognized automatically.
   Blank credential fields keep existing values. Changing a value replaces it.
3. Run **Check environment**. This is an offline dependency check; it does not
   test the account against Spotify. **Install Deno** and **Update downloader**
   are separate, explicit network operations with their output shown in the log.
4. Open **Download music**, enter a Spotify link or search, and start the task.

AcoustID application credentials and a Chromaprint-capable FFmpeg build or
`fpcalc.exe` are required for library repair and fingerprint verification. The
GUI exposes these in Settings and Library care. Ordinary duration audits and
Traktor previews can run without Spotify credentials.

## Pages

| Page | Available operations |
| --- | --- |
| Download music | Track, album, playlist and artist links; Spotify text searches; queue import/export and editing; genre blocks; seven audio formats; bitrate; folder grouping; M3U output; cover art; artist albums; matching controls; existing-file verification/replacement; cookies, proxy, request delays and temporary storage. |
| Playlist sync | Create, edit, remove and select playlist definitions; destination and genre per playlist; sync all or a subset; save in the existing `playlists.json` format. |
| Discography | Collect artist release links by market; append to a queue or write a text file; transfer the result to the queue editor. |
| Library care | Report-only audit, validated repair, or direct NML replacement; duration bounds; fingerprint verification; unknown-file repair; reference checks; resume/rescan; file limit; Spotify lookups; reports/backups; FFmpeg and fpcalc. |
| Traktor tools | Preview/apply cleanup using one or more repair logs; clean re-import NML; restore playlist positions after re-import; volume mappings and legacy log encoding. |
| Run history | Status, full console logs, reports, retry queues, repeating a task, and transferring repair logs / cleanup manifests to Traktor tools. |
| Settings & setup | Saved GUI themes, library and credentials, offline diagnostics, Deno installation and downloader updates. |
| Help | In-app workflow and recovery guide. |

Controls follow the operation. Repair controls do not appear on Download music;
report-only audits hide replacement options; NML mode hides directory checks and
rescan; unknown-file repair requires fingerprint verification; lossless formats
hide bitrate; genre and artwork controls are specific to MP3. Existing-file
verification is hidden when replacement is selected. Hidden settings are excluded
from the relevant task, so changing modes cannot silently enable an old option.

Automatic download grouping uses `Track/%artist%`, `Album/%artist%/%album%`,
`Playlist/%playlist%`, or `Artist/%artist%/%album%`. Source subfolders can be
turned off, and grouping can be customized or disabled. Playlist sync uses each
playlist's destination and defaults to keeping files together there. The GUI's
audio/matching form is independent of the legacy per-type argument strings in
`config.json`; saving settings preserves those strings.

Queue syntax matches the console downloader, with extra validation that reports
the offending line instead of silently skipping invalid entries:

```text
# Artist / collection
genre:"Drum and Bass"
https://open.spotify.com/track/example
https://open.spotify.com/album/example genre:"Liquid"
https://open.spotify.com/track/example genre:""

# A blank line or comment ends a genre block
spotify:playlist:example
```

Genre tagging only changes newly created MP3 files. Saving playlist definitions
does not download anything. **Sync selected playlists** uses the current list;
**Save playlists** writes the shared file. Unsaved playlist edits and the queue
are retained as GUI drafts between sessions.

## Background work and logs

One task runs at a time. A second executable instance for the same folder is
blocked. Work happens in a noninteractive Python process. Its stdout and stderr
are read asynchronously, with bounded dispatcher batches and a bounded display
buffer. Navigation, the console, resizing and Stop remain available.

**Save run files** is off by default and remembered between launches. Enable it
before starting a task to retain its settings, full console and Savify logs,
download summaries and retry queues in Run history. The checkbox is locked while
a task runs. With saving off, no diagnostic logs or history entry are written;
the temporary job settings are removed when the task finishes, fails or is
stopped. Existing saved runs are kept. Required task outputs (music, discography
lists, audit reports/checkpoints, repair records and recovery backups) are always
preserved.

**Show run files** opens the current or last run's saved folder in this session.
It is greyed out if that run did not save files, with a tooltip explaining how to
enable saving for the next run. Earlier saved runs remain available in Run history.

The log divider can be dragged. **Follow output** controls automatic scrolling;
**Copy** copies the selection or visible log; **Save log** exports the complete
disk log when available, or the recent buffered output (including hidden DEBUG
messages) when automatic saving was off. **Clear view** clears buffered messages
but does not delete saved files. To inspect an earlier run while
a task is active, use its **Open files** button; the integrated log continues
showing the current process.

**Show DEBUG** includes or hides DEBUG-level messages immediately, including
recent buffered output and logs opened from Run history. It is off by default
and remembered between launches. Savify sends DEBUG messages to the GUI
automatically, independently of log saving, so no separate task verbosity setting
is needed. This is a display filter: saved logs retain all process output even
when the display skips lines under heavy output. The unfiltered display buffer is
bounded; with automatic saving off, old or skipped messages cannot be recovered.
Clearing the view clears its hidden messages too. The CLI's ASCII banner is
suppressed when a task is started by the GUI.

Status meanings:

- **Finished**: the operation exited successfully.
- **Needs attention**: failed downloads, audit findings, or missing dependencies.
  Inspect the output/report for details.
- **Failed**: validation, configuration or execution error.
- **Stopped**: the user cancelled the task.
- **Interrupted**: a previous session ended before it could save a final status.

**Stop task** terminates the worker and its entire process tree through a Windows
Job Object, including FFmpeg and downloader children. The worker does not start
until it has joined that job. Closing the app during a task asks whether to stop
and waits asynchronously for shutdown. A crash closes the job handle and also
terminates descendants. Completed files and repairs remain; a forced stop may
leave temporary files. Folder audits can resume from their existing state folder.
Repeating an NML replacement downloads every listed track again.

## Traktor workflow

1. Finish a repair. With run-file saving enabled, its run folder contains
   `replacements.jsonl` recording only replacements installed during that
   invocation. Otherwise, these records are kept in the audit state folder's
   `replacement-runs/` directory. The console prints the path in either case.
2. In Traktor tools, select the active `collection.nml` and this log. Create a
   preview first. **Use latest GUI repair** and the history shortcuts help fill
   these inputs.
3. Close Traktor, select **Apply changes**, and run the cleanup. The existing
   cleanup engine verifies an original backup and atomically replaces the NML.
4. Import the generated `reimport-repaired.nml` in Traktor using **File tags**,
   then analyze the repaired tracks.
5. Close Traktor and choose **Restore playlist positions after re-import**, using
   the manifest from the applied cleanup. Preview or apply the restoration.

After an interrupted apply, inspect the manifest, proposed collection and original
backup before repeating it. A preview manifest cannot restore playlist positions.
With run-file saving off, Traktor outputs are kept in a new
`collection.nml.savify-<run-id>/` folder beside the collection, including its
manifest, re-import file and original backup. Their paths appear in the console.

## Files and compatibility

- `config.json`: shared library path. GUI writes preserve unrelated keys and the
  CLI's track/album/playlist argument strings.
- `playlists.json`: shared playlist definitions. Existing metadata on retained
  entries and legacy template keys are preserved. Malformed JSON is not silently
  overwritten.
- `gui-data/preferences.json`: theme, dark mode, DEBUG visibility, run-file saving, form values,
  queue and playlist drafts; no Spotify
  or AcoustID credential values. Destructive Traktor Apply is not remembered.
- `gui-data/runs/<timestamp>-<id>/` (only with **Save run files** enabled):
  `job.json`, `run.json`, `console.log`, Savify diagnostic logs, plus download
  summaries/retry links, repair logs or Traktor artifacts as applicable.
- Existing audit state directories retain their reports, checkpoints and backups.

JSON saves use a temporary file and replacement with a `.bak` of the previous
version. API credentials use the same Windows user environment variables as CLI
setup. The runner redacts their literal values from its display and console log.
Task files can contain your local paths, queries and a proxy URL, so review them
before sharing. `gui-data` is ignored by Git.

All existing `.bat` launchers and CLI commands remain available. Legacy changes
cover UTF-8 config/playlist reading (so GUI-saved Unicode names work) and GUI-only
logging, console-title and ASCII banner behavior. CLI verbosity, silent mode and
automatic diagnostic log files are preserved. No console prompt loops were
removed or repurposed.

## Development and offline verification

The executable is built from the checked-in C# sources with the Windows .NET
Framework compiler. Close Convenify before rebuilding:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File GUI\Build.ps1
```

`GUI/Launch.ps1` is an optional source launcher for development. Normal use runs
the compiled executable and does not depend on PowerShell script policy.

```powershell
python\python.exe tests\test_gui_worker.py
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File GUI\Launch.ps1 -SmokeTest
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\Test-DesktopRunner.ps1
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File tests\Test-DesktopPersistence.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\Test-DesktopLog.ps1
```

Tests cover task/option routing, genre parsing, Click/yt-dlp argument boundaries,
validation and exit status propagation, a real offline audit, a real Traktor
preview that preserves its input, WPF page/dependency behavior, stdout/stderr,
heavy log output and cancellation of a real descendant process. They do not
download music, install tools, change credentials or modify a real collection.
Persistence checks use an isolated temporary workspace to verify Unicode paths,
config/playlist compatibility, backups, draft recovery, theme migration and
credential exclusion. Log checks cover filtering, partial lines, retained DEBUG
messages, clearing and bounded memory under heavy output. Banner checks verify
that GUI suppression preserves normal CLI output.
