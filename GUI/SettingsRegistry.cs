using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows.Controls;

namespace Convenify.Desktop
{
    internal sealed class SettingDefault
    {
        public string Key, Scope, InitialConfigKey;
        public Control Control;
        public object Value;
    }

    // Register controls when they are created, BEFORE loading user values.
    // Reset enumerates the registry, so hidden and newly added fields participate.
    internal sealed class SettingsRegistry
    {
        private readonly Dictionary<string, SettingDefault> entries = new Dictionary<string, SettingDefault>();
        public IEnumerable<SettingDefault> Entries { get { return entries.Values; } }
        public void Register(string key, string scope, Control control) {
            object value;
            if (control is TextBox) value = ((TextBox)control).Text;
            else if (control is CheckBox) value = ((CheckBox)control).IsChecked;
            else if (control is ComboBox) value = ((ComboBox)control).SelectedIndex;
            else if (control is PasswordBox) value = "";
            else throw new InvalidOperationException("Register default handling for " + control.GetType().Name);
            entries.Add(key, new SettingDefault { Key = key, Scope = scope, Control = control, Value = value });
        }
        public void InheritConfig(string key, string configKey) { entries[key].InitialConfigKey = configKey; }
        public bool HasScope(string scope) { return entries.Values.Any(e => e.Scope == scope); }
        public void Reset(string scope) {
            foreach (var entry in entries.Values.Where(e => scope == null || e.Scope == scope)) {
                var control = entry.Control;
                if (control is TextBox) ((TextBox)control).Text = (string)entry.Value;
                else if (control is CheckBox) ((CheckBox)control).IsChecked = (bool?)entry.Value;
                else if (control is ComboBox) ((ComboBox)control).SelectedIndex = (int)entry.Value;
                else if (control is PasswordBox) ((PasswordBox)control).Clear();
            }
        }
    }
}
