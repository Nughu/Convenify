using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;

namespace Convenify.Desktop
{
    // Shared by reset actions, credential handling and the clean-release exporter.
    // New configuration files/credentials belong in defaults.json, not a reset list.
    public sealed class DefaultFile
    {
        public string path { get; set; }
        public string scope { get; set; }
        public object value { get; set; }
    }
    public sealed class CredentialDefinition
    {
        public string key { get; set; }
        public string label { get; set; }
        public string environment { get; set; }
    }
    public sealed class AppDefaults
    {
        public int schema { get; set; }
        public DefaultFile[] files { get; set; }
        public CredentialDefinition[] credentials { get; set; }

        public Dictionary<string, object> Config {
            get { return (Dictionary<string, object>)files.Single(f => f.path == "config.json").value; }
        }
        public static AppDefaults Load(string root) {
            var manifest = new JavaScriptSerializer().Deserialize<AppDefaults>(File.ReadAllText(Path.Combine(root, "GUI", "defaults.json"), Encoding.UTF8));
            if (manifest == null || manifest.schema != 1 || manifest.files == null || manifest.credentials == null)
                throw new InvalidDataException("GUI/defaults.json is missing or invalid.");
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var file in manifest.files) {
                LocalPath(root, file.path, false);
                string relative = file.path.Replace('\\', '/');
                if (relative.Equals("gui-data/runs", StringComparison.OrdinalIgnoreCase) || relative.StartsWith("gui-data/runs/", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Saved run files cannot be registered as resettable configuration.");
                if (!paths.Add(file.path) || (file.scope != "settings" && file.scope != "all"))
                    throw new InvalidDataException("Duplicate or invalid default file: " + file.path);
            }
            if (manifest.files.Count(f => f.path == "config.json" && f.value is Dictionary<string, object>) != 1)
                throw new InvalidDataException("The default shared configuration is missing.");
            if (manifest.credentials.Select(c => c.key).Distinct().Count() != manifest.credentials.Length
                || manifest.credentials.Select(c => c.environment).Distinct().Count() != manifest.credentials.Length)
                throw new InvalidDataException("Duplicate credential definition.");
            return manifest;
        }

        // Configuration reset is limited to declared files inside the install.
        // Refuse junctions/symlinks rather than following them into user data.
        public static string LocalPath(string root, string relative, bool checkLinks = true) {
            if (String.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative)
                || relative.Replace('\\', '/').Split('/').Any(s => s == ".." || s == "" || s == "."))
                throw new InvalidDataException("Invalid configuration path: " + relative);
            string prefix = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            string path = Path.GetFullPath(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)));
            if (!path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Configuration path is outside the installation.");
            if (!checkLinks) return path;
            for (string current = path; current != null && current.Length >= prefix.Length; current = Path.GetDirectoryName(current)) {
                if ((File.Exists(current) || Directory.Exists(current)) && (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Configuration reset cannot follow a link: " + current);
            }
            return path;
        }

        public static void RemoveOldCopies(string path) {
            File.Delete(path + ".bak");
            string parent = Path.GetDirectoryName(path);
            if (!Directory.Exists(parent)) return;
            string prefix = Path.GetFileName(path) + ".";
            foreach (string candidate in Directory.EnumerateFiles(parent, prefix + "*.tmp")) {
                string name = Path.GetFileName(candidate);
                Guid id;
                if (Guid.TryParseExact(name.Substring(prefix.Length, name.Length - prefix.Length - 4), "N", out id))
                    File.Delete(candidate);
            }
        }
    }

    public interface ICredentialEnvironment
    {
        string Get(string name, EnvironmentVariableTarget target);
        void Set(string name, string value, EnvironmentVariableTarget target);
    }
    internal sealed class WindowsCredentialEnvironment : ICredentialEnvironment
    {
        public string Get(string name, EnvironmentVariableTarget target) { return Environment.GetEnvironmentVariable(name, target); }
        public void Set(string name, string value, EnvironmentVariableTarget target) { Environment.SetEnvironmentVariable(name, value, target); }
    }
}
