# Remove repaired tracks from Traktor Pro 3's collection

`traktor_reset_repaired.py` reads the output of the Savify repair you are already
running. It removes **only tracks reported as successfully `repaired`** from the
specified `collection.nml`, backs up the original, and creates a clean NML for
re-import. It never opens, modifies, moves, or deletes music files. No change to
your running Savify installation is needed.

Requirements: Python 3.11 or newer, using only Python's standard library. You can
use the same Python installation that runs your repair tool. Run the commands
below from the folder containing `traktor_reset_repaired.py`.

## 1. Finish or pause the repair, and keep Traktor closed

Prefer waiting for the repair to finish. If needed, stop it cleanly with Ctrl+C;
only completed repairs can be processed. A snapshot of a growing log cannot
include repairs that happen after the snapshot. Do not start Traktor while the
repair is still replacing music files.

Use the **active** Traktor root directory, shown in Preferences → File Management
→ Root Dir, not an old version's folder or an exported playlist. A typical Windows
path is `C:\Users\YOURNAME\Documents\Native Instruments\Traktor YOURVERSION\collection.nml`.
Traktor loads this file on startup and writes it on exit, so close it before applying
changes. On Windows, the script also checks for running Traktor executables.

## 2. Preview the removals

Replace the collection path below with your actual path. Your log can be the
existing `audit.log` from the current run:

```powershell
python traktor_reset_repaired.py "C:\Users\YOURNAME\Documents\Native Instruments\Traktor YOURVERSION\collection.nml" --repair-log "J:\DJing\Musiq\_savify_audit\audit.log"
```

**Without `--apply`, the collection is unchanged.** The script prints the number
of completed repairs, matched collection entries, and affected playlist
references. It writes a new report folder next to the collection, named
`collection.nml.savify-DATE-ID`.

`events.jsonl` is the recommended input when available. It contains structured,
historical results and is already produced by your repair tool:

```powershell
python traktor_reset_repaired.py "C:\Users\YOURNAME\Documents\Native Instruments\Traktor YOURVERSION\collection.nml" --repair-log "J:\DJing\Musiq\_savify_audit\events.jsonl"
```

Supported inputs:

| Input | Selection rule |
| --- | --- |
| `events.jsonl` | Only `event=file_result` records whose status is exactly `repaired`; earlier successful repairs remain included after later scans |
| `audit.log` or saved console output | An `INFO: [n] Checking FULL_PATH` followed by its completed `INFO: repaired: ...` result |
| `report.csv` | Rows whose status is exactly `repaired`; this is a latest-results snapshot and may omit older repairs after subsequent scans |

`duration_match`, `verified`, `unknown`, `mismatch`, `corrupt`, `repair_failed`,
and `error` never select a track for removal. Neither downloading a temporary
candidate nor backing up the original counts as a completed repair.

An unmatched or uncertain path is reported, not guessed. Windows drive letters,
case, slash direction, Traktor's `/:` separators, Unicode names, and XML escapes
are handled. A matching filename or title alone is never enough. Multiple
collection entries for the same repaired absolute path are all removed.

For several logs, repeat `--repair-log PATH`. UTF-8 and BOM-marked UTF-16 console
logs are supported automatically. For a legacy console encoding, specify
`--log-encoding cp1252` (only if that is the actual encoding).

If the collection uses a volume *label* instead of a drive letter and cannot be
matched, explicitly map it, for example `--volume-map "DJ Disk=J:"`. No mapping
is normally necessary for a Windows `VOLUME="J:"` entry. Unmapped entries remain
untouched and are listed in the report. POSIX paths use case-sensitive matching;
named macOS volumes likewise need an explicit root mapping, such as
`--volume-map "Macintosh HD=/"` or `--volume-map "DJ Disk=/Volumes/DJ Disk"`.

## 3. Apply the cleanup

Repeat the preview command with `--apply`:

```powershell
python traktor_reset_repaired.py "C:\Users\YOURNAME\Documents\Native Instruments\Traktor YOURVERSION\collection.nml" --repair-log "J:\DJing\Musiq\_savify_audit\audit.log" --apply
```

This creates a new report folder, including a byte-for-byte
`collection.original.nml` backup, before atomically replacing the live collection.
The updated XML is parsed again before installation. If the collection changes
while the script is working, or the backup or replacement fails, it will not
overwrite the newer collection. A failure after the swap is reported explicitly
as an applied change with a report-finalization problem.

The script removes the selected collection entries and their corresponding
playlist references, updates the affected `ENTRIES` counts, and preserves
unrelated collection entries, playlist order, folders, and other XML data.
**Playlist memberships are saved for restoration after the tracks are re-imported**;
they do not come back merely by re-importing the audio.

It does not edit global preferences, History files, the Stripes/Transients caches,
or any audio tags. XML formatting and empty-element syntax can change during
serialization; unrelated metadata values remain intact.

## 4. Re-import only the repaired tracks, then analyze

Deleting collection entries does not by itself import them again. Automatic
startup import works only if the relevant Music Folders and import settings are
configured. For this repair, prefer the targeted import below:

1. Start Traktor after applying the cleanup.
2. Right-click **Track Collection → Import another Collection**.
3. Choose **`reimport-repaired.nml` from the report folder created by the applied
   cleanup**. This contains only the repaired tracks removed by that operation.
4. If asked about tag priority, choose **File** to read the preserved basic tags
   and artwork from the repaired audio.
5. Analyze the repaired tracks. The import file includes a **Savify repaired
   tracks** playlist to make them easy to select. You can enable **Analyze new
   imported tracks** in Preferences → File Management, or select these tracks and
   invoke Analyze manually.

The generated import entries contain only location and basic artist/title hints.
They contain no old cues, beatgrids, tempo, key, audio IDs, analysis locks, play
counts, or old import dates. The actual music tags come from the repaired files
when you choose File.

**Avoid a broad Music Folders import that includes `_savify_audit/backups`.**
Those are real copies of the old audio and can be imported as separate tracks.
Disable automatic startup import for the initial cleanup/re-import if the
configured root contains those backups, and use the targeted NML instead. Do not
move or delete the active repair's backups just to make the import work.

## 5. Optional: restore the old playlist positions

After re-importing and analyzing, close Traktor so it saves the fresh entries.
Use the **manifest from the applied cleanup**:

```powershell
python traktor_reset_repaired.py "C:\Users\YOURNAME\Documents\Native Instruments\Traktor YOURVERSION\collection.nml" --restore-playlists "C:\PATH\TO\collection.nml.savify-DATE-ID\manifest.json"
```

Review the preview, then repeat with `--apply`:

```powershell
python traktor_reset_repaired.py "C:\Users\YOURNAME\Documents\Native Instruments\Traktor YOURVERSION\collection.nml" --restore-playlists "C:\PATH\TO\collection.nml.savify-DATE-ID\manifest.json" --apply
```

This restores the repaired tracks' original positions, including repeated
appearances in a playlist, using fresh path references. It leaves the newly
analyzed collection entries untouched and creates another collection backup.
It requires the repaired tracks to exist in the collection again and refuses
to overwrite playlists whose membership/order changed after cleanup. Avoid
editing affected playlists until this optional restoration is done. If there is
no stable UUID, a playlist must be uniquely identifiable by its folder/name path.

## Reports and recovery

Each invocation makes a new report directory. `--report-dir PATH` can select a
specific directory, but it must not exist already; earlier reports are never
overwritten.

| File | Purpose |
| --- | --- |
| `manifest.json` | Whether changes were applied; source hashes; removed full paths; unmatched paths; unsupported entries; saved playlist positions |
| `collection.proposed.nml` | The exact proposed collection, even for a preview |
| `collection.original.nml` | Byte-for-byte pre-edit backup, created only when changes are applied |
| `reimport-repaired.nml` | Clean, targeted import of the repaired tracks; never the old analysis |

To undo the whole operation, keep Traktor closed and copy
`collection.original.nml` back over the selected `collection.nml`. That also
restores the old analysis, so use the generated clean import instead when you
want to keep the reset. Never import the original backup as the clean re-import.

If interrupted immediately after an atomic swap, the backup and prewritten
manifest still exist. The manifest's `updated_sha256` identifies the proposed
collection even if `applied` could not yet be marked true. Inspect the files before
repeating an apply. Keep the report folder until the whole workflow is complete.

Repeated cleanup before re-import finds no matching entries and makes no further
changes. **Do not run an old repair log again after the tracks have been
re-imported/analyzed:** that would deliberately remove those entries again. Use
only the new repair batch's records for a later cleanup. Playlist restoration
itself is idempotent when no intervening playlist edits have occurred.

Exit status: `0` means the requested preview/apply completed (inspect unmatched
counts in the report); `2` means invalid input, setup, or operation failure;
`130` means interrupted.

## Validation and limits

All 50 automated tests pass using synthetic NML documents and real filesystem operations.
They cover log parsing, Unicode, path/drive distinctions, duplicate entries,
counts, metadata preservation, clean re-imports, playlist restoration, failures,
concurrent edits, and a collection with **13,004 entries**. Windows process checks
are simulated; the suite runs on Linux. The script has **not been run against
your actual collection or inside Traktor Pro 3**. The preview and backup are
therefore part of the normal workflow.

Run the tests without installing additional packages:

```powershell
python -m unittest discover -s tests -v
```

## References

- [Native Instruments: Traktor Pro 3 files and folders](https://support.native-instruments.com/support/solutions/articles/69000879348-files-folders): active root directory, startup loading and exit saving, caches.
- [Native Instruments: importing a collection and choosing File tags](https://support.native-instruments.com/support/solutions/articles/69000879494-how-to-restore-the-traktor-track-collection-from-a-backup).
- [Traktor Pro 3 manual](https://www.native-instruments.com/fileadmin/ni_media/downloads/manuals/traktor/TRAKTOR_PRO_3_Manual_English_2023_09.pdf): File Management, startup import, and analyzing newly imported tracks.
- [Traktor community example of Windows NML locations and playlist keys](https://community.native-instruments.com/discussion/28833/traktor-pro-3-plus-3-11-1-17-playlists-partially-empty-cause-and-fix-windows-mac): drive-letter and drive-in-DIR representations.
