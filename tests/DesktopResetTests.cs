using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using Convenify.Desktop;

public static class DesktopResetTests
{
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    private static object Field(object app, string name) { return app.GetType().GetField(name, Private).GetValue(app); }
    private static void Call(object app, string name, params object[] args) { app.GetType().GetMethod(name, Private).Invoke(app, args); }
    private static void Check(bool value, string label) { if (!value) throw new Exception(label); Console.WriteLine("PASS " + label); }
    private static void Write(string path, object value) { Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllText(path, Json.Serialize(value), new UTF8Encoding(false)); }
    private static Dictionary<string, object> Read(string path) { return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path)); }

    private sealed class FakeEnvironment : ICredentialEnvironment {
        private readonly Dictionary<string, string> values = new Dictionary<string, string>();
        public int Writes;
        public string Get(string name, EnvironmentVariableTarget target) { string value; return values.TryGetValue(target + ":" + name, out value) ? value : null; }
        public void Set(string name, string value, EnvironmentVariableTarget target) { Writes++; values[target + ":" + name] = value; }
    }
    private static object Create(string root, FakeEnvironment environment) {
        object app = Activator.CreateInstance(typeof(DesktopApp), Private, null, new object[] { root, environment }, null);
        ((DispatcherTimer)Field(app, "timer")).Stop();
        return app;
    }
    private static bool IsDefault(SettingDefault entry) {
        if (entry.Control is TextBox) return ((TextBox)entry.Control).Text == (string)entry.Value;
        if (entry.Control is ComboBox) return ((ComboBox)entry.Control).SelectedIndex == (int)entry.Value;
        if (entry.Control is CheckBox) return ((CheckBox)entry.Control).IsChecked == (bool?)entry.Value;
        return ((PasswordBox)entry.Control).Password == "";
    }
    private static void Change(SettingDefault entry) {
        if (entry.Control is TextBox) ((TextBox)entry.Control).Text = "private fixture";
        else if (entry.Control is ComboBox) ((ComboBox)entry.Control).SelectedIndex = (((ComboBox)entry.Control).SelectedIndex + 1) % ((ComboBox)entry.Control).Items.Count;
        else if (entry.Control is CheckBox) ((CheckBox)entry.Control).IsChecked = !((CheckBox)entry.Control).IsChecked;
        else ((PasswordBox)entry.Control).Password = "fixture-secret";
    }
    public static void Run(string sourceRoot) {
        string tempParent = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar);
        string root = Path.Combine(tempParent, "convenify-reset-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root, "GUI"));
        File.Copy(Path.Combine(sourceRoot, "GUI", "MainWindow.xaml"), Path.Combine(root, "GUI", "MainWindow.xaml"));
        var manifest = Read(Path.Combine(sourceRoot, "GUI", "defaults.json"));
        var files = Json.ConvertToType<List<Dictionary<string, object>>>(manifest["files"]);
        files.Add(new Dictionary<string, object> { { "path", "future-settings.json" }, { "scope", "all" }, { "value", new Dictionary<string, object> { { "future", "default" } } } });
        ((Dictionary<string, object>)files.Single(f => (string)f["path"] == "config.json")["value"])["future_cli_option"] = "default";
        manifest["files"] = files;
        Write(Path.Combine(root, "GUI", "defaults.json"), manifest);
        try {
            Write(Path.Combine(root, "config.json"), new { library_path = "C:\\private-library", track_args = "private args", unknown_private_key = "private" });
            Write(Path.Combine(root, "playlists.json"), new Dictionary<string, object> { { "Saved", new { link = "spotify:playlist:fixture", path = "Saved", genre = "House" } } });
            var environment = new FakeEnvironment();
            var defaults = AppDefaults.Load(root);
            foreach (var credential in defaults.credentials) {
                environment.Set(credential.environment, "fixture-user", EnvironmentVariableTarget.User);
                environment.Set(credential.environment, "fixture-process", EnvironmentVariableTarget.Process);
                environment.Set(credential.environment, "fixture-machine", EnvironmentVariableTarget.Machine);
            }
            object app = Create(root, environment);
            var registry = (SettingsRegistry)Field(app, "defaultsRegistry");
            var controls = (Dictionary<string, Control>)Field(app, "fields");
            var window = (Window)Field(app, "window");
            var resetButton = (Button)window.FindName("ResetTab");
            Check(registry.Entries.Count() == controls.Count, "Every field is registered for reset before user configuration loads");
            Check(((TextBox)controls["d.output"]).Text == "C:\\private-library", "User library is hydrated after capturing factory defaults");

            // A new field goes through the same registration path as Field().
            var future = new TextBox { Text = "future default", Visibility = Visibility.Collapsed };
            Call(app, "RegisterField", "future.option", future, future, "Download music");
            future.Text = "future personalized";
            foreach (string scope in new[] { "Download music", "Playlist sync", "Discography", "Library care", "Traktor tools" }) {
                foreach (var entry in registry.Entries.Where(e => e.Scope == scope)) Change(entry);
                ((TextBox)controls["cfg.library"]).Text = "settings sentinel";
                Call(app, "Navigate", scope);
                Check(resetButton.Visibility == Visibility.Visible, scope + " exposes Reset to defaults");
                Call(app, "ResetConfiguration", scope, false);
                Check(registry.Entries.Where(e => e.Scope == scope).All(IsDefault), scope + " restores all fields, including hidden options");
                Check(((TextBox)controls["cfg.library"]).Text == "settings sentinel", scope + " reset leaves global settings alone");
            }
            Check(future.Text == "future default" && (string)Read(Path.Combine(root, "gui-data", "preferences.json"))["future.option"] == "future default",
                "Newly registered fields reset and persist without changing reset code");
            Check(((TextBox)controls["d.output"]).Text == "", "Factory reset never captures a personalized startup library as the default");

            ((TextBox)controls["d.query"]).Text = "keep operation input";
            foreach (var entry in registry.Entries.Where(e => e.Scope == "Settings & setup")) Change(entry);
            string legacy = Path.Combine(root, "Savify_automation", "spotify_credentials.json");
            Write(legacy, new { client_secret = "fixture-secret" });
            File.WriteAllText(Path.Combine(root, "config.json.bak"), "private backup");
            Call(app, "ResetConfiguration", "Settings & setup", false);
            Check(registry.Entries.Where(e => e.Scope == "Settings & setup").All(IsDefault), "Settings reset restores appearance, console and credentials controls");
            Check(((TextBox)controls["d.query"]).Text == "keep operation input" && Read(Path.Combine(root, "playlists.json")).Count == 1, "Settings reset preserves operation values and saved playlists");
            Check((string)Read(Path.Combine(root, "config.json"))["future_cli_option"] == "default" && !Read(Path.Combine(root, "config.json")).ContainsKey("unknown_private_key"), "Shared reset uses the entire canonical config, including future defaults");
            Check(!File.Exists(legacy) && !File.Exists(Path.Combine(root, "config.json.bak")), "Reset removes legacy credentials and stale configuration backups");
            Check(defaults.credentials.All(c => environment.Get(c.environment, EnvironmentVariableTarget.User) == null
                && environment.Get(c.environment, EnvironmentVariableTarget.Process) == null
                && environment.Get(c.environment, EnvironmentVariableTarget.Machine) == "fixture-machine"), "Credential reset removes user/process values without changing machine-level settings");

            string kept = Path.Combine(root, "gui-data", "runs", "existing", "traktor", "collection.original.nml");
            Directory.CreateDirectory(Path.GetDirectoryName(kept)); File.WriteAllText(kept, "recovery sentinel");
            string log = Path.Combine(root, "gui-data", "runs", "existing", "console.log"); File.WriteAllText(log, "log sentinel");
            File.WriteAllText(Path.Combine(root, "download-queue.txt"), "private queue");
            File.WriteAllText(Path.Combine(root, "download-queue_2.txt"), "private queue 2");
            Write(Path.Combine(root, "future-settings.json"), new { future = "personalized" });
            Write(Path.Combine(root, "gui-data", "preferences.json"), new { unknown_future_preference = "private" });
            File.WriteAllText(Path.Combine(root, "gui-data", "preferences.json.bak"), "private previous values");
            foreach (var entry in registry.Entries) Change(entry);
            Call(app, "ResetConfiguration", null, true);
            Check(registry.Entries.All(IsDefault), "Reset everything restores every registered field");
            Check(Read(Path.Combine(root, "playlists.json")).Count == 0 && !File.Exists(Path.Combine(root, "download-queue.txt"))
                && !File.Exists(Path.Combine(root, "download-queue_2.txt")), "Reset everything clears saved playlists and installation-local queues");
            Check((string)Read(Path.Combine(root, "future-settings.json"))["future"] == "default", "New manifest-managed state files automatically participate in Reset everything");
            Check(File.ReadAllText(kept) == "recovery sentinel" && File.ReadAllText(log) == "log sentinel", "Reset everything preserves every existing run log and recovery file");
            Check(!File.Exists(Path.Combine(root, "gui-data", "preferences.json.bak")) && !Read(Path.Combine(root, "gui-data", "preferences.json")).ContainsKey("unknown_future_preference"), "Reset leaves no old preference values in backup or unknown keys");
            var restored = Create(root, environment);
            Check(((SettingsRegistry)Field(restored, "defaultsRegistry")).Entries.All(IsDefault), "Defaults remain applied after restarting");
            Call(app, "SetBusy", true);
            int writes = environment.Writes;
            bool refused = false;
            try { Call(app, "ResetConfiguration", null, true); } catch (TargetInvocationException ex) { refused = ex.InnerException is InvalidOperationException; }
            Check(refused && writes == environment.Writes && !resetButton.IsEnabled, "Resets are blocked while a background task runs");
            Call(app, "SetBusy", false);
            bool escaped = false;
            try { AppDefaults.LocalPath(root, "../outside.json"); } catch (InvalidDataException) { escaped = true; }
            Check(escaped, "Reset storage rejects paths outside the installation");
            files.Add(new Dictionary<string, object> { { "path", "gui-data/runs/existing/console.log" }, { "scope", "all" }, { "value", null } });
            Write(Path.Combine(root, "GUI", "defaults.json"), manifest);
            bool protectedRun = false;
            try { AppDefaults.Load(root); } catch (InvalidDataException) { protectedRun = true; }
            Check(protectedRun && File.ReadAllText(log) == "log sentinel", "Future reset definitions cannot register saved run files for deletion");
        } finally {
            string resolved = Path.GetFullPath(root);
            if (Path.GetDirectoryName(resolved) == tempParent && Path.GetFileName(resolved).StartsWith("convenify-reset-", StringComparison.Ordinal))
                Directory.Delete(resolved, true);
        }
    }
}
