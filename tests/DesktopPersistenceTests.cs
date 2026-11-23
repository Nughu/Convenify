using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Controls;
using System.Windows.Threading;
using Convenify.Desktop;

public static class DesktopPersistenceTests
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    private static object ReadField(object app, string name) { return app.GetType().GetField(name, Private).GetValue(app); }
    private static void Invoke(object app, string name, params object[] args) { app.GetType().GetMethod(name, Private).Invoke(app, args); }
    private static Dictionary<string, object> Read(string path) { return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8)); }
    private static void Check(bool value, string label) { if (!value) throw new Exception(label); Console.WriteLine("PASS " + label); }
    private static object Create(string root) {
        object app = Activator.CreateInstance(typeof(DesktopApp), Private, null, new object[] { root }, null);
        ((DispatcherTimer)ReadField(app, "timer")).Stop();
        return app;
    }

    private static void CheckRunRetention(object app, string root) {
        var fields = (Dictionary<string, Control>)ReadField(app, "fields");
        var save = (CheckBox)fields["ui.save_run_files"];
        var window = (System.Windows.Window)ReadField(app, "window");
        var button = (Button)window.FindName("OpenRun");
        var console = (TextBox)ReadField(app, "console");
        Check(save.IsChecked == false && !button.IsEnabled && (string)button.Content == "Show run files",
            "Run-file saving starts off and Show run files starts disabled");
        Check(System.Windows.Controls.ToolTipService.GetShowOnDisabled(button) && ((string)button.ToolTip).Contains("Enable Save run files"),
            "Disabled Show run files explains how to enable saving on hover");

        Invoke(app, "PrepareRun", new Dictionary<string, object> { { "action", "diagnostics" } });
        string temporary = (string)ReadField(app, "runFolder");
        Check(File.Exists(Path.Combine(temporary, "job.json")) && ReadField(app, "viewedLog") == null
            && !File.Exists(Path.Combine(temporary, "run.json")) && !Directory.Exists(Path.Combine(root, "gui-data", "runs")),
            "Unsaved runs only create temporary task transport, without logs or history");
        Invoke(app, "SetBusy", true);
        Check(!save.IsEnabled, "Run saving cannot change halfway through a task");
        Invoke(app, "Append", "[DEBUG] unsaved debug fixture\n");
        Invoke(app, "Finish", 0);
        Check(!Directory.Exists(temporary) && !button.IsEnabled && console.Text.Contains("were not saved") && save.IsEnabled,
            "Finished unsaved runs remove temporary settings and leave Show run files disabled");
        Check(((string)((Button)window.FindName("SaveLog")).ToolTip).Contains("console buffer"), "Manual export explains its buffer limit when automatic saving is off");

        save.IsChecked = true;
        Check(!button.IsEnabled, "Enabling saving does not invent files for the previous unsaved run");
        Invoke(app, "PrepareRun", new Dictionary<string, object> { { "action", "diagnostics" } });
        string retained = (string)ReadField(app, "runFolder");
        Check(button.IsEnabled && File.Exists(Path.Combine(retained, "run.json"))
            && (bool)Read(Path.Combine(retained, "job.json"))["save_run_files"], "Saved runs retain settings, history and an enabled file button");
        Invoke(app, "Finish", 0);
        Check((string)Read(Path.Combine(retained, "run.json"))["status"] == "Finished" && Directory.Exists(retained), "Saved run completion retains history");

        save.IsChecked = false;
        Check(button.IsEnabled, "Turning off future saving keeps the last saved run accessible");
        foreach (int code in new[] { 2, 130 }) {
            Invoke(app, "PrepareRun", new Dictionary<string, object> { { "action", "diagnostics" } });
            temporary = (string)ReadField(app, "runFolder");
            Invoke(app, "Finish", code);
            Check(!Directory.Exists(temporary) && !button.IsEnabled && Directory.Exists(retained),
                "Unsaved failure/cancellation cleans temporary settings and preserves older saved runs (exit " + code + ")");
        }
        Directory.CreateDirectory(Path.Combine(root, "python"));
        File.WriteAllText(Path.Combine(root, "python", "python.exe"), "invalid test executable");
        bool failed = false;
        try { Invoke(app, "Start", new Dictionary<string, object> { { "action", "diagnostics" } }); }
        catch (TargetInvocationException) { failed = true; }
        Check(failed && !Directory.Exists((string)ReadField(app, "runFolder")) && !button.IsEnabled,
            "Worker startup failure also cleans the unsaved task workspace");
        save.IsChecked = true;
    }

    public static void Run(string sourceRoot)
    {
        string tempParent = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar);
        string root = Path.Combine(tempParent, "convenify-persistence-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root, "GUI"));
        File.Copy(Path.Combine(sourceRoot, "GUI", "MainWindow.xaml"), Path.Combine(root, "GUI", "MainWindow.xaml"));
        var config = new Dictionary<string, object> {
            { "library_path", "" }, { "track_args", "-vv -g \"%artist%\"" },
            { "album_args", "-g \"%album%\"" }, { "playlist_args", "-g \"%playlist%\"" },
            { "custom", new Dictionary<string, object> { { "keep", true } } }
        };
        File.WriteAllText(Path.Combine(root, "config.json"), Json.Serialize(config), new UTF8Encoding(false));
        var originalPlaylists = new Dictionary<string, object> {
            { "library-path", "legacy sentinel" },
            { "H\u00e4user", new Dictionary<string, object> {
                { "link", "https://open.spotify.com/playlist/fixture" }, { "path", "House" },
                { "genre", "House" }, { "custom", "retain me" }
            } }
        };
        File.WriteAllText(Path.Combine(root, "playlists.json"), Json.Serialize(originalPlaylists), new UTF8Encoding(false));
        try {
            object app = Create(root);
            var fields = (Dictionary<string, Control>)ReadField(app, "fields");
            Check((string)((ComboBox)fields["ui.theme"]).SelectedItem == "Default" && ((CheckBox)fields["ui.dark_mode"]).IsChecked == true,
                "New preferences use Default with dark mode enabled");
            ((ComboBox)fields["ui.theme"]).SelectedItem = "Plum";
            Check((string)Read(Path.Combine(root, "gui-data", "preferences.json"))["ui.theme"] == "Plum", "Theme changes save immediately without requiring library or credential settings");
            ((CheckBox)fields["ui.dark_mode"]).IsChecked = false;
            ((ComboBox)fields["ui.theme"]).SelectedItem = "Forest";
            Check(((CheckBox)fields["ui.dark_mode"]).IsChecked == false
                && !(bool)Read(Path.Combine(root, "gui-data", "preferences.json"))["ui.dark_mode"], "Light mode saves immediately and remains selected when changing themes");
            var console = (TextBox)ReadField(app, "console");
            Invoke(app, "ClearConsole");
            Invoke(app, "Append", "[INFO] downloading\n[DEBUG] candidate details\n[ERROR] failed candidate\n");
            Check(!console.Text.Contains("candidate details") && console.Text.Contains("downloading") && console.Text.Contains("failed candidate"), "DEBUG starts hidden while regular output and errors stay visible");
            ((CheckBox)fields["ui.show_debug"]).IsChecked = true;
            Check(console.Text.Contains("candidate details"), "Show DEBUG restores buffered messages immediately");
            ((CheckBox)fields["ui.show_debug"]).IsChecked = false;
            Check(!console.Text.Contains("candidate details"), "Turning Show DEBUG off immediately filters the existing view");
            Invoke(app, "ClearConsole");
            ((CheckBox)fields["ui.show_debug"]).IsChecked = true;
            Check(console.Text.Length == 0, "Changing the DEBUG filter does not resurrect a cleared view");
            CheckRunRetention(app, root);
            string library = Path.Combine(root, "M\u00fcsik \u6771\u4eac");
            ((TextBox)fields["cfg.library"]).Text = library;
            Invoke(app, "SaveSettings"); // Empty credential boxes: no environment writes.
            var saved = Read(Path.Combine(root, "config.json"));
            Check((string)saved["library_path"] == library && Directory.Exists(library), "Unicode library folder saves correctly");
            Check((string)saved["track_args"] == (string)config["track_args"] && saved.ContainsKey("custom"), "Saving settings preserves CLI arguments and unknown keys");
            Check(File.Exists(Path.Combine(root, "config.json.bak")), "Settings replacement retains a backup");

            var playlists = (ObservableCollection<PlaylistRow>)ReadField(app, "playlists");
            playlists[0].Genre = "Bass";
            Invoke(app, "SavePlaylists");
            var savedPlaylists = Read(Path.Combine(root, "playlists.json"));
            var entry = (Dictionary<string, object>)savedPlaylists["H\u00e4user"];
            Check((string)entry["genre"] == "Bass" && (string)entry["custom"] == "retain me" && savedPlaylists.ContainsKey("library-path"), "Playlist saves preserve metadata and legacy template keys");

            ((PasswordBox)fields["cfg.client_id"]).Password = "fixture-client-id-not-for-disk";
            ((PasswordBox)fields["cfg.client_secret"]).Password = "fixture-secret-not-for-disk";
            ((PasswordBox)fields["cfg.acoustid"]).Password = "fixture-acoustid-not-for-disk";
            ((TextBox)fields["d.queue"]).Text = "# Gr\u00fc\u00dfe\nspotify:track:fixture\n";
            ((CheckBox)fields["t.apply"]).IsChecked = true;
            playlists[0].Genre = "Unsaved draft";
            app.GetType().GetField("playlistDirty", Private).SetValue(app, true);
            Invoke(app, "SavePreferences");
            string preferencesPath = Path.Combine(root, "gui-data", "preferences.json");
            string preferences = File.ReadAllText(preferencesPath, Encoding.UTF8);
            Check(!preferences.Contains("not-for-disk") && !preferences.Contains("cfg.client") && !preferences.Contains("t.apply"), "Credentials and destructive Apply are excluded from preferences");
            object restored = Create(root);
            var restoredFields = (Dictionary<string, Control>)ReadField(restored, "fields");
            Check((string)((ComboBox)restoredFields["ui.theme"]).SelectedItem == "Forest"
                && ((CheckBox)restoredFields["ui.dark_mode"]).IsChecked == false
                && ((CheckBox)restoredFields["ui.show_debug"]).IsChecked == true
                && ((CheckBox)restoredFields["ui.save_run_files"]).IsChecked == true, "Theme, light/dark mode, DEBUG visibility and run saving survive restart independently");
            var restoredPlaylists = (ObservableCollection<PlaylistRow>)ReadField(restored, "playlists");
            Check(((TextBox)restoredFields["d.queue"]).Text.Contains("Gr\u00fc\u00dfe") && restoredPlaylists[0].Genre == "Unsaved draft" && ((CheckBox)restoredFields["t.apply"]).IsChecked == false, "Queue and unsaved playlist drafts survive restart safely");
            var unknownTheme = Read(preferencesPath);
            unknownTheme["ui.theme"] = "Removed preset";
            File.WriteAllText(preferencesPath, Json.Serialize(unknownTheme), new UTF8Encoding(false));
            var fallbackFields = (Dictionary<string, Control>)ReadField(Create(root), "fields");
            Check((string)((ComboBox)fallbackFields["ui.theme"]).SelectedItem == "Default"
                && ((TextBox)fallbackFields["d.queue"]).Text.Contains("Gr\u00fc\u00dfe"), "An unknown theme falls back safely while preserving other preferences");
            foreach (var legacy in new[] { new[] { "Midnight", "Plum" }, new[] { "Violet / Pink", "Default" } }) {
                unknownTheme["ui.theme"] = legacy[0];
                unknownTheme.Remove("ui.dark_mode");
                File.WriteAllText(preferencesPath, Json.Serialize(unknownTheme), new UTF8Encoding(false));
                var migrated = (Dictionary<string, Control>)ReadField(Create(root), "fields");
                Check((string)((ComboBox)migrated["ui.theme"]).SelectedItem == legacy[1] && ((CheckBox)migrated["ui.dark_mode"]).IsChecked == true,
                    legacy[0] + " migrates to " + legacy[1] + " with dark mode enabled");
            }

            File.WriteAllText(Path.Combine(root, "playlists.json"), "{ invalid JSON", new UTF8Encoding(false));
            object damaged = Create(root);
            bool refused = false;
            try { Invoke(damaged, "SavePlaylists"); } catch (TargetInvocationException ex) { refused = ex.InnerException is InvalidOperationException; }
            Check(refused && File.ReadAllText(Path.Combine(root, "playlists.json")) == "{ invalid JSON", "Malformed playlist files are not overwritten");

            File.WriteAllText(Path.Combine(root, "config.json"), "{ invalid config", new UTF8Encoding(false));
            refused = false;
            try { Invoke(app, "SaveSettings"); } catch (TargetInvocationException) { refused = true; }
            Check(refused && File.ReadAllText(Path.Combine(root, "config.json")) == "{ invalid config", "Malformed configuration is not overwritten");
        }
        finally {
            string resolved = Path.GetFullPath(root);
            // Delete only this test's new directory directly under the temp folder.
            if (Path.GetDirectoryName(resolved) == tempParent && Path.GetFileName(resolved).StartsWith("convenify-persistence-", StringComparison.Ordinal))
                Directory.Delete(resolved, true);
        }
    }
}
