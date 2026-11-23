using System;
using System.Collections.Generic;

namespace Convenify.Desktop
{
    internal static class OptionHelp
    {
        // Download and playlist sync share the same underlying audio options.
        private static readonly Dictionary<string, string> Download = new Dictionary<string, string> {
            { "format", "The file format used for downloaded audio. MP3 supports the genre and cover-art options here. FLAC and WAV do not use a bitrate setting; converting to them cannot improve the source recording." },
            { "quality", "Sets the output audio quality or target bitrate. Higher bitrates usually create larger files. Best uses the downloader's highest-quality setting; it cannot improve the original source." },
            { "skip_cover_art", "Leave album artwork out of newly downloaded MP3 files. With this off, the downloader embeds available cover art." },
            { "m3u", "Write an M3U playlist alongside the downloads so a media player can open the downloaded tracks as a playlist." },
            { "group", "Create nested folders using %artist%, %album% or %playlist%, separated by /. For example, %artist%/%album% puts each album under its artist." },
            { "workers", "Maximum number of tracks processed at the same time (1-16; default 4). More workers can finish faster but use more CPU, network bandwidth and service requests." },
            { "search_results", "How many candidate recordings to search before choosing a match (1-50; default 10). More candidates can help difficult matches but require more work." },
            { "match_threshold", "Minimum candidate matching score from 0 to 1 (default 0.82). A higher score is stricter and may leave more tracks unmatched. Title, artist and duration checks still apply." },
            { "no_youtube_music", "Use regular YouTube searches only. With this off, matching can also search YouTube Music." },
            { "no_match_cache", "Ignore previously successful Spotify-to-YouTube mappings and do not save new mappings during this run. Each track is matched again." },
            { "match_overrides", "Choose a JSON file mapping Spotify track IDs or URIs to specific YouTube URLs or video IDs. Use it to choose a recording manually when automatic matching picks poorly." },
            { "redownload", "Process existing downloads again and replace a file only after a new recording passes validation. When off, existing files are normally skipped." },
            { "verify_existing", "Check the duration and source records of existing audio before deciding whether to skip it. This adds work to the run and is unnecessary when replacement is selected." },
            { "cookies", "An optional exported cookies file for yt-dlp to use when accessing the audio source. Leave blank for normal requests without imported browser cookies." },
            { "proxy", "Route yt-dlp's download requests through this proxy URL, such as http://localhost:8080. Leave blank to use its normal connection." },
            { "request_delay", "Wait this many seconds between yt-dlp extraction requests. A delay can reduce request frequency. Leave blank to keep the downloader's default." },
            { "temp_path", "Folder for Savify's temporary data and working files. Leave blank to use its default data folder. This is separate from your finished music files." },
        };
        private static readonly Dictionary<string, string> Fields = new Dictionary<string, string> {
            { "d.source", "Choose one Spotify link or a text search, or use the queue editor to process multiple Spotify links in order." },
            { "d.query", "Paste a Spotify track, album, playlist or artist URL/URI. For a text search, enter an artist and title and choose what to search for below." },
            { "d.search_type", "Choose whether a text query searches Spotify for a track, album, playlist or artist. Spotify links already identify their type, so this option is hidden for links." },
            { "d.queue", "Process one Spotify link per line. # starts a comment. genre:\"Drum and Bass\" sets the genre for a block, or can follow a link for one entry. Blank lines and comments end the block. The editor is saved as a draft between launches." },
            { "d.artist_albums", "For artist entries, download tracks from all of the artist's albums. With this off, artist downloads use top tracks only." },
            { "d.output", "Base folder for finished downloads. Source subfolders and the folder-grouping pattern are applied inside this folder." },
            { "d.organize", "Add a Track, Album, Playlist or Artist folder under the music folder according to each link's type. Folder grouping is then applied inside it." },
            { "d.genre", "Write this genre to newly created MP3 files. Queue genre blocks or per-link genre values can override it. Existing files are not retagged by this option." },
            { "d.group_mode", "Automatic groups tracks by artist, albums and artists by artist/album, and playlists by playlist name. Keep files together adds no grouping folders. Custom uses the pattern you enter below." },
            { "s.output", "Base music folder for playlist destinations entered as relative paths. A playlist with an absolute destination uses that path directly." },
            { "s.group_mode", "Keep files together saves tracks directly in each playlist's destination. Automatic adds a playlist-name folder. Custom uses the grouping pattern below within each destination." },
            { "p.name", "A unique name for this saved playlist definition. Select an existing row to edit it, or choose New to add another playlist." },
            { "p.link", "The Spotify playlist URL or URI to sync. The selected playlist's tracks are fetched when you start a sync." },
            { "p.folder", "Destination for this playlist. A relative path such as Electronic/House is placed under the sync music folder; an absolute path is used directly." },
            { "p.genre", "Genre tag for MP3s newly created while syncing this playlist. Leave blank to keep the downloader's normal tags. Existing files are not retagged." },
            { "c.artist", "Spotify artist URL, artist URI or artist ID whose albums, singles and compilations should be collected. This task gathers links without downloading audio." },
            { "c.market", "Choose the country's Spotify catalog used to collect releases. Only releases available in that market are returned, so the resulting links can differ. Use a two-letter code: DE = Germany (default), US = United States, GB = United Kingdom." },
            { "c.mode", "Append adds an artist heading and release links to the end of a queue, keeping its contents. Write a new discography file saves only the collected links and asks before replacing an existing file." },
            { "c.output", "Text file that will receive the collected Spotify links. Use the button below to load it into the download queue after collection finishes." },
            { "a.mode", "Audit folder reports issues. Audit and repair also replaces problem audio with validated recordings and backs up originals. NML replacement processes every supported track listed in the exported NML." },
            { "a.directory", "Scan supported audio files in this folder and its subfolders. Folder audits keep checkpoints in the reports folder so later runs can resume." },
            { "a.nml", "An exported Traktor NML listing tracks to replace. Supported tracks are replaced at their original paths after validation, even if they were processed in an earlier run." },
            { "a.full_verification", "Identify audio using fingerprints as well as checking duration. This is slower and requires an AcoustID key plus Chromaprint support. Duration checks alone cannot establish recording identity." },
            { "a.rescan", "Recheck files already marked verified or repaired in the audit checkpoint. With this off, completed checkpoint entries can be skipped when the audit resumes." },
            { "a.duration_tolerance", "Maximum allowed difference in seconds between a file's duration and the expected recording duration. Default: 8 seconds. A smaller value flags smaller discrepancies." },
            { "a.min_duration", "Flag files shorter than this duration. Enter seconds, MM:SS or HH:MM:SS; leave blank for no lower bound. Repair modes also use this bound when evaluating replacements." },
            { "a.max_duration", "Flag files longer than this duration. Enter seconds, MM:SS or HH:MM:SS; leave blank for no upper bound. Repair modes also use this bound when evaluating replacements." },
            { "a.limit", "Stop after processing this many files. Useful for trying the operation on a small batch. Leave blank to process the full selection." },
            { "a.spotify", "Use Spotify catalog information to help identify recordings and expected durations. Auto uses saved credentials when available; Enabled requires them; Disabled skips Spotify lookups." },
            { "a.max_candidates", "Maximum number of replacement candidates to try for each file (default 5). More attempts may find a valid recording but can make repairs take longer." },
            { "a.repair_unknown", "Also attempt replacement for unrecognized or ambiguous recordings found during fingerprint verification. A replacement still needs positive identity verification. Reference comparisons can also run unless disabled separately." },
            { "a.no_reference_check", "Skip temporary reference downloads for inconclusive fingerprint results during full verification and repair. Those files remain for review unless another enabled repair rule applies." },
            { "a.match_overrides", "JSON mapping track_id values from the audit report to manually selected YouTube URLs. Use this to choose a specific replacement recording. Replacement validation still applies." },
            { "a.cookies", "Optional exported cookies file used by yt-dlp when downloading replacement recordings. Leave blank to use normal requests." },
            { "a.state_dir", "Stores reports, checkpoints and original-file backups. Blank uses _savify_audit under the music folder for audits, or _savify_audit/nml-<filename> beside the NML for direct replacement. Reuse the same folder to resume an audit." },
            { "a.ffmpeg", "Folder containing FFmpeg and FFprobe for audio inspection and conversion. The bundled ffmpeg folder is selected by default." },
            { "a.fpcalc", "Optional path to Chromaprint's fpcalc.exe for audio fingerprints. Needed when the chosen FFmpeg lacks Chromaprint support. Fingerprint checks also require an AcoustID key." },
            { "t.mode", "Cleanup removes successfully repaired tracks from the collection and playlists and prepares a re-import NML. Restore uses the cleanup manifest to restore playlist positions after re-import." },
            { "t.collection", "The active Traktor collection.nml to inspect or update. Close Traktor before applying changes so it cannot overwrite the edited collection." },
            { "t.repair_logs", "One repair-log path per line. Cleanup extracts successfully replaced file paths from replacements.jsonl, events.jsonl, report.csv, audit.log or console logs." },
            { "t.manifest", "manifest.json from an applied cleanup. After importing the repaired tracks back into Traktor, use this to restore their original playlist positions." },
            { "t.log_encoding", "Text encoding for legacy console logs, for example cp1252. Usually leave blank. This only affects reading cleanup logs." },
            { "t.volume_maps", "Map a named Traktor volume to its filesystem root, one NAME=ROOT per line (for example DJ Disk=J:). Standard drive-letter volumes need no mapping." },
            { "t.apply", "Write the cleanup or restored playlist positions to the active collection after creating an original backup. With this unchecked, generate a preview and leave the collection unchanged." },
            { "cfg.library", "Default music folder shared with the command-line launchers. Save settings writes it to config.json and updates the download, sync and audit folder fields." },
            { "cfg.client_id", "Client ID of your Spotify developer application, used with its client secret for catalog requests. Save settings stores it as a Windows user environment variable. Leave blank to keep the saved value." },
            { "cfg.client_secret", "Client secret for your Spotify developer application. Save settings stores it as a Windows user environment variable. Leave blank to keep the saved value; credentials are excluded from GUI preferences and task snapshots." },
            { "cfg.acoustid", "AcoustID application key for fingerprint identification during library repair or full verification. Save settings stores it as a Windows user environment variable. Leave blank to keep the saved key." },
            { "ui.theme", "Choose Default (vibrant magenta and purple), Ocean, Forest or Plum. Each palette has a light and dark variant controlled by Dark mode. Changes apply immediately and are saved for the next launch." },
            { "ui.dark_mode", "Use the dark variant of the selected color theme. Turn this off for its light variant. Dark mode is enabled by default, and your choice is saved independently of the theme." },
            { "ui.follow_output", "Automatically scroll to new process output. Turn this off to read earlier messages while a task continues." },
            { "ui.show_debug", "Show or hide DEBUG-level messages from Savify, including recent buffered messages and saved logs opened from Run history. Diagnostics are captured automatically; no separate task option is needed. This display filter does not affect log saving." },
            { "ui.save_run_files", "Keep this task's settings, full console and Savify logs, reports and retry queues in Run history. Off by default; choose before starting a task. When off, only the recent console buffer is kept for manual Save log export. Music, requested output files, library checkpoints, repair records and required recovery backups are always kept." }
        };
        private static readonly Dictionary<string, string> Actions = new Dictionary<string, string> {
            { "Reset to defaults", "After confirmation, restore every setting and input on this operation tab, including hidden options. Other tabs and saved run files are kept. Playlist sync reloads saved definitions and discards unsaved playlist edits. Defaults are saved immediately." },
            { "Reset settings", "After confirmation, restore the Settings tab and console preferences, reset shared CLI configuration, and remove saved user credentials and legacy credential files. Other operation tabs, playlists, queues, music and saved run files are kept." },
            { "Reset everything", "After confirmation, reset every tab and shared CLI setting, remove user credentials, and clear saved playlists, installation-local queue files and drafts. Music, run history, reports, repair records and recovery backups are kept. Use the clean-release exporter to exclude personal files from a release." },
            { "Import file", "Load a text file into the queue editor. If the editor contains a draft, you will be asked before replacing it." },
            { "Load saved queue", "Load download-queue.txt from the Convenify folder into the editor. You will be asked before replacing an existing draft." },
            { "Save queue as...", "Save the queue editor's current contents to a text file of your choice." },
            { "Select all", "Tick every saved playlist for the next sync." },
            { "Select none", "Untick every playlist. Tick the playlists you want before starting a sync." },
            { "New", "Clear the playlist editor so Add / update playlist creates a new definition." },
            { "Remove selected", "Remove the highlighted playlist row from the editor. Music files are kept. Save playlists writes this change to playlists.json." },
            { "Save playlists", "Write the current playlist definitions to playlists.json for the GUI and command-line sync. The previous file is kept as a backup." },
            { "Add / update playlist", "Add the entered playlist to the list, or update the highlighted row. Save playlists writes the edited definitions to disk." },
            { "Open this file in download queue", "Load this existing text file into the download queue for review. It does not start a download." },
            { "Add repair logs", "Choose one or more repair logs and append their paths to the cleanup input list." },
            { "Use latest GUI repair", "Use the most recent GUI replacement log as the cleanup input." },
            { "Refresh", "Reload the latest 200 run records from disk. Damaged records are skipped." },
            { "View log", "Show the highlighted run's console output in the integrated log. Available when no task is running; very large logs show their tail." },
            { "Open files", "Open the highlighted run's folder containing its settings snapshot, console log and any reports." },
            { "Open report", "Open the main report or output artifact recorded for the highlighted run." },
            { "Load retry queue", "Load the highlighted run's failed Spotify links into the download queue for another attempt." },
            { "Run again", "Repeat the highlighted task using its saved settings. This repeats the whole task, not just failed items. Destructive operations still ask for confirmation." },
            { "Use repair log", "Send this run's replacement log to Traktor cleanup without starting the cleanup." },
            { "Use cleanup manifest", "Load this applied cleanup's collection and manifest into Traktor playlist restoration. Re-import the repaired tracks before running restoration." },
            { "Save settings", "Save the default music folder and any credentials you entered. Blank credential fields keep their saved values. Existing command-line configuration is preserved." },
            { "Check environment", "Run an offline check of the bundled tools, Python dependencies and available credentials. This does not test credentials against online services." },
            { "Install Deno", "After confirmation, install Deno through winget for YouTube support. This downloads software; installation output appears in the log." },
            { "Update downloader", "After confirmation, update yt-dlp and ytmusicapi in Convenify's bundled Python. Requires an internet connection; progress appears in the log." },
            { "Open Convenify folder", "Open this Convenify installation folder in File Explorer." },
            { "Open desktop guide", "Open the local desktop guide with setup instructions, workflows and troubleshooting information." },
            { "Start download", "Download the entered link or search result using the currently visible audio and matching options." },
            { "Download queue", "Process the queue editor's Spotify links in order using the current options and any queue genre overrides." },
            { "Sync selected playlists", "Download tracks from the checked playlists using the current list and destinations, including unsaved edits." },
            { "Collect releases", "Collect Spotify release links for this artist and market, then append or write them to the chosen text file." },
            { "Start audit", "Scan the chosen folder and create a report of audio issues without replacing music files." },
            { "Start repair", "After confirmation, audit the folder and install validated replacements where needed, backing up original audio." },
            { "Replace NML tracks", "After confirmation, replace every supported track listed in the exported NML at its original path, retaining original backups." },
            { "Apply collection changes", "After confirmation, apply the selected Traktor operation to collection.nml and keep an original backup. Close Traktor first." },
            { "Create preview", "Generate a preview of the selected Traktor operation without modifying the active collection." }
        };

        public static string ForField(string key)
        {
            string text;
            if (Fields.TryGetValue(key, out text)) return text;
            if ((key.StartsWith("d.") || key.StartsWith("s.")) && Download.TryGetValue(key.Substring(2), out text)) return text;
            throw new ArgumentException("Missing option explanation: " + key);
        }
        public static string ForAction(string label)
        {
            string text;
            if (Actions.TryGetValue(label, out text)) return text;
            throw new ArgumentException("Missing action explanation: " + label);
        }
    }
}
