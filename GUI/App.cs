using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Win32;

namespace Convenify.Desktop
{
    // Two columns when the available width supports them, otherwise one.
    // Full-width children (headings, paths, tables, expanders) start a new row.
    // Collapsed options do not leave empty grid cells behind.
    public sealed class ResponsivePanel : Panel
    {
        public double MinimumColumnWidth { get; set; }
        public double ColumnGap { get; set; }
        public static readonly DependencyProperty FullWidthProperty = DependencyProperty.RegisterAttached(
            "FullWidth", typeof(bool), typeof(ResponsivePanel),
            new FrameworkPropertyMetadata(false, FrameworkPropertyMetadataOptions.AffectsParentMeasure));
        public static void SetFullWidth(UIElement element, bool value) { element.SetValue(FullWidthProperty, value); }
        public static bool GetFullWidth(UIElement element) { return (bool)element.GetValue(FullWidthProperty); }

        public ResponsivePanel() { MinimumColumnWidth = 460; ColumnGap = 18; }
        protected override Size MeasureOverride(Size available) { return Layout(available.Width, false); }
        protected override Size ArrangeOverride(Size finalSize) { Layout(finalSize.Width, true); return finalSize; }

        private Size Layout(double width, bool arrange)
        {
            bool finite = !Double.IsInfinity(width);
            int columns = finite && width >= MinimumColumnWidth * 2 + ColumnGap ? 2 : 1;
            double cellWidth = finite ? Math.Max(0, (width - ColumnGap * (columns - 1)) / columns) : Double.PositiveInfinity;
            double top = 0, rowHeight = 0, desiredWidth = 0;
            int column = 0;
            foreach (UIElement child in InternalChildren)
            {
                if (child.Visibility == Visibility.Collapsed) continue;
                bool full = columns == 1 || GetFullWidth(child);
                if (full && column != 0) { top += rowHeight; column = 0; rowHeight = 0; }
                double childWidth = full ? width : cellWidth;
                if (!arrange) child.Measure(new Size(childWidth, Double.PositiveInfinity));
                double left = column == 0 ? 0 : cellWidth + ColumnGap;
                double height = child.DesiredSize.Height;
                if (arrange) child.Arrange(new Rect(left, top, Math.Max(0, childWidth), height));
                desiredWidth = Math.Max(desiredWidth, left + child.DesiredSize.Width);
                rowHeight = Math.Max(rowHeight, height);
                if (full || ++column == columns) { top += rowHeight; column = 0; rowHeight = 0; }
            }
            return new Size(finite ? width : desiredWidth, top + rowHeight);
        }
    }

    public sealed class PlaylistRow
    {
        public bool Selected { get; set; }
        public string Name { get; set; }
        public string Link { get; set; }
        public string Folder { get; set; }
        public string Genre { get; set; }
    }
    public sealed class HistoryRow
    {
        public string Started { get; set; }
        public string Task { get; set; }
        public string Status { get; set; }
        public string Folder { get; set; }
        public string Artifact { get; set; }
    }

    public sealed class DesktopApp
    {
        private readonly string root, dataRoot;
        private readonly Window window;
        private readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
        private readonly Dictionary<string, Control> fields = new Dictionary<string, Control>();
        private readonly Dictionary<string, FrameworkElement> rows = new Dictionary<string, FrameworkElement>();
        private readonly SettingsRegistry defaultsRegistry = new SettingsRegistry();
        private readonly AppDefaults defaults;
        private readonly ICredentialEnvironment credentialEnvironment;
        private const string SettingsScope = "Settings & setup";
        private readonly Dictionary<string, ResponsivePanel> pages = new Dictionary<string, ResponsivePanel>();
        private readonly ProcessRunner runner = new ProcessRunner();
        private readonly ConsoleLogBuffer logBuffer = new ConsoleLogBuffer();
        private readonly ObservableCollection<PlaylistRow> playlists = new ObservableCollection<PlaylistRow>();
        private readonly Dictionary<string, object> playlistExtras = new Dictionary<string, object>();
        private readonly DispatcherTimer timer;
        private readonly TextBox console;
        private readonly Button runButton, cancelButton;
        private readonly ListBox navigation;
        private DataGrid playlistGrid, historyGrid;
        private readonly List<Button> mutationButtons = new List<Button>();
        private bool building = true, busy, closingAfterCancel, playlistsReadable = true, playlistDirty;
        private string currentPage = "Download music", runFolder, viewedLog, artifact, lastRepairLog, activeAction;
        private bool saveRunFiles;
        private DateTime started;
        private Dictionary<string, object> activeMetadata;
        private readonly string[] pageNames = { "Download music", "Playlist sync", "Discography", "Library care", "Traktor tools", "Run history", "Settings & setup", "Help" };
        private readonly string[] descriptions = {
            "Bring tracks, albums and playlists into your local library.",
            "Manage your saved playlists and download their new tracks.",
            "Collect an artist's releases as links, ready for your download queue.",
            "Check your collection and replace audio that needs attention.",
            "Prepare repaired tracks for a clean re-import into Traktor.",
            "Revisit reports, full console logs and unfinished downloads.",
            "Set up your library, accounts and bundled tools.",
            "A short guide to your music workspace." };

        private T Named<T>(string name) where T : class { return window.FindName(name) as T; }
        private static void Help(FrameworkElement element, string explanation) {
            element.ToolTip = explanation;
            AutomationProperties.SetHelpText(element, explanation);
            ToolTipService.SetInitialShowDelay(element, 450);
            ToolTipService.SetBetweenShowDelay(element, 100);
            ToolTipService.SetShowDuration(element, 45000);
            ToolTipService.SetShowOnDisabled(element, true);
        }
        private string Get(string key) {
            var control = fields[key];
            var text = control as TextBox;
            if (text != null) return text.Text.Trim();
            var combo = control as ComboBox;
            if (combo != null) return Convert.ToString(combo.SelectedItem, CultureInfo.InvariantCulture);
            var secret = control as PasswordBox;
            return secret == null ? "" : secret.Password.Trim();
        }
        private bool On(string key) { return ((CheckBox)fields[key]).IsChecked == true; }
        private int Index(string key) { return ((ComboBox)fields[key]).SelectedIndex; }
        private void Set(string key, string value) { ((TextBox)fields[key]).Text = value ?? ""; }
        private void Show(string key, bool show) { rows[key].Visibility = show ? Visibility.Visible : Visibility.Collapsed; }
        private bool Visible(string key) { return rows[key].Visibility == Visibility.Visible; }
        private void Safe(Action action) {
            try { action(); }
            catch (Exception ex) { MessageBox.Show(window, ex.Message, "Convenify", MessageBoxButton.OK, MessageBoxImage.Warning); }
        }
        private void Note(Panel parent, string text) {
            var note = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, FontSize = 12, Margin = new Thickness(0, 4, 0, 9) };
            note.SetResourceReference(TextBlock.ForegroundProperty, "MutedBrush");
            ResponsivePanel.SetFullWidth(note, true);
            parent.Children.Add(note);
        }
        private ResponsivePanel Card(Panel parent, string title, string description = null, bool fullWidth = false) {
            var body = new ResponsivePanel { MinimumColumnWidth = 210, ColumnGap = 16 };
            var border = new Border { BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(9), Padding = new Thickness(20, 17, 20, 15), Margin = new Thickness(0, 0, 0, 14), Child = body };
            border.SetResourceReference(Border.BackgroundProperty, "SurfaceBrush");
            border.SetResourceReference(Border.BorderBrushProperty, "BorderBrush");
            ResponsivePanel.SetFullWidth(border, fullWidth);
            parent.Children.Add(border);
            var heading = new TextBlock { Text = title, TextWrapping = TextWrapping.Wrap, FontSize = 17, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 9) };
            ResponsivePanel.SetFullWidth(heading, true);
            body.Children.Add(heading);
            if (description != null) Note(body, description);
            return body;
        }
        private StackPanel Field(Panel parent, string key, string label, Control control, string hint = null) {
            var row = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
            ResponsivePanel.SetFullWidth(row, true);
            if (!String.IsNullOrEmpty(label)) row.Children.Add(new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap, FontWeight = FontWeights.SemiBold, FontSize = 12, Margin = new Thickness(0, 0, 0, 6) });
            if (!String.IsNullOrEmpty(label)) AutomationProperties.SetName(control, label);
            AutomationProperties.SetAutomationId(control, key);
            row.Children.Add(control);
            if (hint != null) Note(row, hint);
            parent.Children.Add(row);
            RegisterField(key, control, row, ScopeOf(parent));
            string explanation = OptionHelp.ForField(key);
            Help(row, explanation);
            Help(control, explanation);
            return row;
        }
        private string ScopeOf(DependencyObject element) {
            while (element != null) {
                foreach (var page in pages) if (ReferenceEquals(element, page.Value)) return page.Key;
                element = LogicalTreeHelper.GetParent(element);
            }
            throw new InvalidOperationException("Attach a setting to its page before registering it.");
        }
        private void RegisterField(string key, Control control, FrameworkElement row, string scope) {
            fields.Add(key, control);
            rows.Add(key, row);
            defaultsRegistry.Register(key, scope, control);
        }
        private void LibraryField(Panel parent, string key, string label) {
            PathField(parent, key, label, Value(defaults.Config, "library_path"));
            defaultsRegistry.InheritConfig(key, "library_path");
        }
        private void Compact(params string[] keys) {
            foreach (string key in keys) ResponsivePanel.SetFullWidth(rows[key], false);
        }
        private TextBox Text(Panel parent, string key, string label, string value = "", string hint = null, int lines = 1) {
            var control = new TextBox { Text = value, MinWidth = 100 };
            if (lines > 1) { control.AcceptsReturn = true; control.VerticalContentAlignment = VerticalAlignment.Top; control.VerticalScrollBarVisibility = ScrollBarVisibility.Auto; control.TextWrapping = TextWrapping.Wrap; control.Height = lines * 21 + 18; }
            Field(parent, key, label, control, hint);
            control.TextChanged += (s, e) => { if (!building) RefreshVisibility(); };
            return control;
        }
        private TextBox PathField(Panel parent, string key, string label, string value, string type = "folder", string hint = null) {
            var text = new TextBox { Text = value, MinWidth = 100 };
            var row = Field(parent, key, label, text, hint);
            row.Children.Remove(text);
            var dock = new DockPanel();
            var button = new Button { Content = "Browse...", Margin = new Thickness(8, 0, 0, 0), Padding = new Thickness(12, 7, 12, 7) };
            Help(button, "Choose " + label.ToLowerInvariant() + (type == "save" ? " and a filename for the output." : " using a file or folder picker.") + "\n\n" + OptionHelp.ForField(key));
            DockPanel.SetDock(button, Dock.Right);
            dock.Children.Add(button);
            dock.Children.Add(text);
            row.Children.Insert(1, dock);
            button.Click += (s, e) => Safe(() => Browse(key, type));
            text.TextChanged += (s, e) => { if (!building) RefreshVisibility(); };
            return text;
        }
        private ComboBox Select(Panel parent, string key, string label, string[] options, int selected = 0, string hint = null) {
            var control = new ComboBox();
            foreach (string option in options) control.Items.Add(option);
            control.SelectedIndex = selected;
            Field(parent, key, label, control, hint);
            control.SelectionChanged += (s, e) => { if (!building) RefreshVisibility(); };
            return control;
        }
        private CheckBox Toggle(Panel parent, string key, string label, bool selected = false) {
            var control = new CheckBox { Content = new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap }, IsChecked = selected };
            Field(parent, key, null, control);
            AutomationProperties.SetName(control, label);
            control.Checked += (s, e) => { if (!building) RefreshVisibility(); };
            control.Unchecked += (s, e) => { if (!building) RefreshVisibility(); };
            return control;
        }
        private Button Button(Panel parent, string label, Action action, bool mutation = false) {
            var button = new Button { Content = label, Margin = new Thickness(0, 2, 8, 5) };
            Help(button, OptionHelp.ForAction(label));
            button.Click += (s, e) => Safe(action);
            parent.Children.Add(button);
            if (mutation) mutationButtons.Add(button);
            return button;
        }
        private WrapPanel Actions(Panel parent) { var panel = new WrapPanel(); ResponsivePanel.SetFullWidth(panel, true); parent.Children.Add(panel); return panel; }
        private void Browse(string key, string type) {
            if (type == "folder") {
                using (var dialog = new System.Windows.Forms.FolderBrowserDialog()) {
                    dialog.Description = "Choose a folder";
                    if (Directory.Exists(Get(key))) dialog.SelectedPath = Get(key);
                    if (dialog.ShowDialog() == System.Windows.Forms.DialogResult.OK) Set(key, dialog.SelectedPath);
                }
            } else if (type == "save") {
                var dialog = new SaveFileDialog { Filter = "Text files|*.txt|All files|*.*", FileName = Path.GetFileName(Get(key)), OverwritePrompt = true };
                if (dialog.ShowDialog(window) == true) Set(key, dialog.FileName);
            } else {
                var dialog = new OpenFileDialog { CheckFileExists = true, Filter = type == "nml" ? "Traktor collections|*.nml|All files|*.*" : type == "json" ? "JSON files|*.json|All files|*.*" : type == "exe" ? "Programs|*.exe|All files|*.*" : "All files|*.*" };
                if (dialog.ShowDialog(window) == true) Set(key, dialog.FileName);
            }
        }

        private DesktopApp(string path) : this(path, new WindowsCredentialEnvironment()) { }
        private DesktopApp(string path, ICredentialEnvironment environment) {
            root = Path.GetFullPath(path);
            dataRoot = Path.Combine(root, "gui-data");
            defaults = AppDefaults.Load(root);
            credentialEnvironment = environment;
            using (var stream = File.OpenRead(Path.Combine(root, "GUI", "MainWindow.xaml"))) window = (Window)XamlReader.Load(stream);
            ThemeCatalog.Apply(window, ThemeCatalog.Default, true);
            string bannerPath = Path.Combine(root, "GithubBanner.png");
            if (File.Exists(bannerPath)) {
                try {
                    var banner = new BitmapImage();
                    banner.BeginInit();
                    banner.CacheOption = BitmapCacheOption.OnLoad;
                    banner.UriSource = new Uri(bannerPath, UriKind.Absolute);
                    banner.EndInit();
                    banner.Freeze();
                    Named<Image>("BrandBanner").Source = banner;
                } catch (Exception) { /* A missing or damaged decorative image must not prevent startup. */ }
            }
            // WPF's native maximization respects the taskbar and current monitor.
            // Also fit the restore bounds to the usable screen, including on
            // smaller or scaled displays; avoid a minimum height larger than it.
            Rect workArea = SystemParameters.WorkArea;
            window.MinWidth = Math.Min(760, workArea.Width);
            window.MinHeight = Math.Min(480, workArea.Height);
            window.Width = Math.Max(window.MinWidth, Math.Min(1360, workArea.Width * 0.9));
            window.Height = Math.Max(window.MinHeight, Math.Min(900, workArea.Height * 0.9));
            console = Named<TextBox>("Console");
            runButton = Named<Button>("RunButton");
            cancelButton = Named<Button>("CancelButton");
            navigation = Named<ListBox>("Navigation");
            var formScroll = Named<ScrollViewer>("FormScroll");
            formScroll.PreviewMouseWheel += (s, e) => {
                // Single-line inputs otherwise swallow wheel events in their
                // internal ScrollViewer, making a long form feel unresponsive.
                var node = e.OriginalSource as DependencyObject;
                while (node != null && node != formScroll) {
                    var box = node as TextBox;
                    var combo = node as ComboBox;
                    if ((box != null && box.AcceptsReturn) || node is DataGrid || (combo != null && combo.IsDropDownOpen)) return;
                    node = node is Visual ? VisualTreeHelper.GetParent(node) : LogicalTreeHelper.GetParent(node);
                }
                formScroll.ScrollToVerticalOffset(formScroll.VerticalOffset - e.Delta);
                e.Handled = true;
            };
            foreach (string name in pageNames) { pages[name] = new ResponsivePanel(); navigation.Items.Add(name); }
            navigation.ItemContainerGenerator.StatusChanged += (s, e) => {
                for (int i = 0; i < pageNames.Length; i++) {
                    var item = navigation.ItemContainerGenerator.ContainerFromIndex(i) as ListBoxItem;
                    if (item != null) Help(item, descriptions[i]);
                }
            };
            Help(cancelButton, "Stop the running worker and all of its child processes. Completed downloads and repairs are kept; temporary files may remain.");
            var followLog = Named<CheckBox>("FollowLog");
            RegisterField("ui.follow_output", followLog, followLog, SettingsScope);
            Help(followLog, OptionHelp.ForField("ui.follow_output"));
            var showDebug = Named<CheckBox>("ShowDebug");
            RegisterField("ui.show_debug", showDebug, showDebug, SettingsScope);
            AutomationProperties.SetAutomationId(showDebug, "ui.show_debug");
            Help(showDebug, OptionHelp.ForField("ui.show_debug"));
            RoutedEventHandler filterChanged = (s, e) => {
                if (building) return;
                RebuildConsole();
                Safe(SavePreferences);
            };
            showDebug.Checked += filterChanged;
            showDebug.Unchecked += filterChanged;
            var saveRuns = Named<CheckBox>("SaveRunFiles");
            RegisterField("ui.save_run_files", saveRuns, saveRuns, SettingsScope);
            AutomationProperties.SetAutomationId(saveRuns, "ui.save_run_files");
            Help(saveRuns, OptionHelp.ForField("ui.save_run_files"));
            RoutedEventHandler retentionChanged = (s, e) => { if (!building) Safe(SavePreferences); };
            saveRuns.Checked += retentionChanged;
            saveRuns.Unchecked += retentionChanged;
            Help(Named<Button>("CopyLog"), "Copy the selected console text, or all text currently held in the log view if nothing is selected.");
            Help(Named<Button>("ClearLog"), "Clear the buffered console output, including hidden DEBUG messages. Any saved log file is kept, and new output will continue to appear.");
            UpdateRunFilesButton();
            Help(Named<GridSplitter>("LogDivider"), "Drag up or down to change how much of the window is used by the form and process log.");
            var config = new Dictionary<string, object>();
            try { config = ReadObject(Path.Combine(root, "config.json")); }
            catch (Exception ex) { Append("Settings need attention: " + ex.Message + "\n"); }
            BuildDownloads();
            BuildPlaylists();
            BuildDiscography();
            BuildAudit();
            BuildTraktor();
            BuildHistory();
            BuildSettings();
            BuildHelp();
            // Defaults have now been captured from the declarations. Only now
            // hydrate shared settings and per-operation user preferences.
            foreach (var entry in defaultsRegistry.Entries.Where(e => e.InitialConfigKey != null))
                Set(entry.Key, config.ContainsKey(entry.InitialConfigKey) ? Value(config, entry.InitialConfigKey) : Value(defaults.Config, entry.InitialConfigKey));
            LoadPlaylists();
            LoadPreferences();
            ThemeCatalog.Apply(window, Get("ui.theme"), On("ui.dark_mode"));
            RebuildConsole();
            building = false;
            RefreshVisibility();
            navigation.SelectionChanged += (s, e) => { if (navigation.SelectedItem != null) Navigate(navigation.SelectedItem.ToString()); };
            navigation.SelectedIndex = 0;
            runButton.Click += (s, e) => Safe(() => Start(BuildJob()));
            cancelButton.Click += (s, e) => Safe(Stop);
            Help(Named<Button>("ResetTab"), OptionHelp.ForAction("Reset to defaults"));
            Named<Button>("ResetTab").Click += (s, e) => Safe(() => ConfirmReset(currentPage, false));
            Named<Button>("ClearLog").Click += (s, e) => ClearConsole();
            Named<Button>("CopyLog").Click += (s, e) => Safe(() => Clipboard.SetText(console.SelectedText.Length > 0 ? console.SelectedText : console.Text));
            Named<Button>("SaveLog").Click += (s, e) => Safe(ExportLog);
            Named<Button>("OpenRun").Click += (s, e) => Safe(() => OpenPath(runFolder));
            window.Closing += (s, e) => {
                if (busy && !closingAfterCancel) {
                    if (MessageBox.Show(window, "A task is running. Stop its worker and child processes, then close Convenify? Completed downloads and repairs are kept.", "Close Convenify", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes) { e.Cancel = true; return; }
                    e.Cancel = true;
                    closingAfterCancel = true;
                    Safe(Stop);
                    return;
                }
                try { SavePreferences(); } catch { }
            };
            window.Closed += (s, e) => { timer.Stop(); runner.Dispose(); };
            timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(100) };
            timer.Tick += (s, e) => DrainOutput();
            timer.Start();
            Append("Welcome to Convenify. Choose a task from the sidebar.\nOutput from the running process will appear here. Enable Save run files before starting a task to keep its full logs and run history.\n");
        }

        private void BuildDownloads() {
            var page = pages["Download music"];
            var source = Card(page, "What would you like to download?");
            Select(source, "d.source", "Source", new[] { "Link or search", "Download queue" });
            Text(source, "d.query", "Spotify link or search", "", "Paste a track, album, playlist or artist link, or enter an artist and title.");
            Select(source, "d.search_type", "Search for", new[] { "track", "album", "playlist", "artist" });
            Text(source, "d.queue", "Queue editor", "", "One Spotify link per line. # starts a comment. Use genre:\"Drum and Bass\" on its own line for a block, or after a link to override it. A blank line resets the block.", 7);
            var queueButtons = Actions(source);
            rows["d.queue_buttons"] = queueButtons;
            Button(queueButtons, "Import file", () => ImportQueue(null));
            Button(queueButtons, "Load saved queue", () => ImportQueue(Path.Combine(root, "download-queue.txt")));
            Button(queueButtons, "Save queue as...", SaveQueue);
            Toggle(source, "d.artist_albums", "Include all artist albums (otherwise top tracks only)");
            var output = Card(page, "Destination & audio");
            LibraryField(output, "d.output", "Music folder");
            Toggle(output, "d.organize", "Create Track / Album / Playlist / Artist subfolders", true);
            DownloadOptions(page, output, "d.", true);
        }

        private void DownloadOptions(Panel page, Panel output, string prefix, bool download) {
            Select(output, prefix + "format", "Audio format", new[] { "mp3", "aac", "flac", "m4a", "opus", "vorbis", "wav" });
            Select(output, prefix + "quality", "Quality / bitrate", new[] { "best", "320k", "256k", "192k", "128k", "96k", "32k", "worst" });
            if (download) Text(output, prefix + "genre", "Genre for newly downloaded MP3s (optional)");
            Toggle(output, prefix + "skip_cover_art", "Skip MP3 cover artwork");
            Toggle(output, prefix + "m3u", "Create an M3U playlist file");
            Compact(prefix + "format", prefix + "quality", prefix + "skip_cover_art", prefix + "m3u");
            Select(output, prefix + "group_mode", "Folder grouping", new[] { "Automatic by source", "Keep files together", "Custom grouping" }, download ? 0 : 1);
            Text(output, prefix + "group", "Grouping pattern", "%artist%/%album%", "Use %artist%, %album% and %playlist%, separated by /. Example: %artist%/%album%.");
            var expander = new Expander { Header = "Matching, existing files & connection", Margin = new Thickness(0, 0, 0, 14) };
            Help(expander, "Expand to adjust recording matching, handling of existing files, request settings and diagnostic output.");
            ResponsivePanel.SetFullWidth(expander, true);
            page.Children.Add(expander);
            var advanced = new ResponsivePanel { Margin = new Thickness(0, 12, 0, 0) };
            expander.Content = advanced;
            var matching = Card(advanced, "Choose accurate recordings");
            Text(matching, prefix + "workers", "Concurrent tracks (1-16)", "4");
            Text(matching, prefix + "search_results", "Search candidates (1-50)", "10");
            Text(matching, prefix + "match_threshold", "Minimum matching score (0-1)", "0.82");
            Toggle(matching, prefix + "no_youtube_music", "Search regular YouTube only");
            Toggle(matching, prefix + "no_match_cache", "Ignore previously saved match mappings");
            PathField(matching, prefix + "match_overrides", "Manual match overrides (optional JSON)", "", "json");
            Toggle(matching, prefix + "redownload", "Replace existing downloads after validating a new recording");
            Toggle(matching, prefix + "verify_existing", "Verify existing files before skipping them");
            Compact(prefix + "workers", prefix + "search_results");
            var connection = Card(advanced, "Connection & diagnostics");
            PathField(connection, prefix + "cookies", "Cookies file (optional)", "", "file");
            Text(connection, prefix + "proxy", "Proxy URL (optional)");
            Text(connection, prefix + "request_delay", "Delay between requests in seconds (blank = default)");
            PathField(connection, prefix + "temp_path", "Temporary data folder (optional)", "");
        }

        private void BuildPlaylists() {
            var page = pages["Playlist sync"];
            var card = Card(page, "Your saved playlists", "Tick the playlists to sync. Destinations can be relative to the music folder or absolute paths.");
            playlistGrid = new DataGrid { Height = 220, ItemsSource = playlists, IsReadOnly = false, SelectionMode = DataGridSelectionMode.Single };
            Help(playlistGrid, "Tick Sync for playlists to process. Select a row to edit its name, link, destination and genre in the playlist editor. Row selection and the Sync checkbox have separate effects.");
            var syncDisplay = new Style(typeof(CheckBox), (Style)window.FindResource(typeof(CheckBox)));
            syncDisplay.Setters.Add(new Setter(FrameworkElement.MarginProperty, new Thickness(0)));
            syncDisplay.Setters.Add(new Setter(UIElement.IsHitTestVisibleProperty, false));
            syncDisplay.Setters.Add(new Setter(UIElement.FocusableProperty, false));
            var syncEdit = new Style(typeof(CheckBox), (Style)window.FindResource(typeof(CheckBox)));
            syncEdit.Setters.Add(new Setter(FrameworkElement.MarginProperty, new Thickness(0)));
            syncEdit.Setters.Add(new Setter(FrameworkElement.ToolTipProperty, "Include this playlist in the next sync."));
            playlistGrid.Columns.Add(new DataGridCheckBoxColumn { Header = "Sync", Binding = new Binding("Selected") { UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged }, Width = new DataGridLength(48), ElementStyle = syncDisplay, EditingElementStyle = syncEdit });
            foreach (var pair in new[] { new[] { "Name", "Name" }, new[] { "Folder", "Folder" }, new[] { "Genre", "Genre" } })
                playlistGrid.Columns.Add(new DataGridTextColumn { Header = pair[0], Binding = new Binding(pair[1]), IsReadOnly = true, Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
            ResponsivePanel.SetFullWidth(playlistGrid, true);
            card.Children.Add(playlistGrid);
            var actions = Actions(card);
            Button(actions, "Select all", () => { foreach (var item in playlists) item.Selected = true; playlistGrid.Items.Refresh(); });
            Button(actions, "Select none", () => { foreach (var item in playlists) item.Selected = false; playlistGrid.Items.Refresh(); });
            Button(actions, "New", ClearPlaylistEditor);
            Button(actions, "Remove selected", RemovePlaylist);
            Button(actions, "Save playlists", SavePlaylists, true);
            playlistGrid.SelectionChanged += (s, e) => {
                var item = playlistGrid.SelectedItem as PlaylistRow;
                if (item == null) return;
                Set("p.name", item.Name); Set("p.link", item.Link); Set("p.folder", item.Folder); Set("p.genre", item.Genre);
            };
            var editor = Card(page, "Playlist editor");
            Text(editor, "p.name", "Name");
            Text(editor, "p.link", "Spotify playlist link");
            PathField(editor, "p.folder", "Destination folder", "", "folder", "A relative path, such as Electronic/House, is resolved under the music folder.");
            Text(editor, "p.genre", "Genre for new MP3s (optional)");
            Button(Actions(editor), "Add / update playlist", UpdatePlaylist);
            var output = Card(page, "Sync output", fullWidth: true);
            LibraryField(output, "s.output", "Music folder");
            DownloadOptions(page, output, "s.", false);
        }

        private void BuildDiscography() {
            var page = pages["Discography"];
            var card = Card(page, "Artist releases", "Single-track releases become track links; multi-track releases become album links.");
            Text(card, "c.artist", "Spotify artist link, URI or ID");
            Text(card, "c.market", "Market / country code", "DE");
            var output = Card(page, "Queue or text file");
            Select(output, "c.mode", "Save links", new[] { "Append to a queue file", "Write a new discography file" });
            PathField(output, "c.output", "Output text file", Path.Combine(root, "download-queue.txt"), "save");
            Button(Actions(output), "Open this file in download queue", () => ImportQueue(Get("c.output")));
            Note(output, "After collecting, open the file in the download queue to review the releases and start downloading.");
        }

        private void BuildAudit() {
            var page = pages["Library care"];
            var source = Card(page, "Choose an operation");
            Select(source, "a.mode", "Operation", new[] { "Audit folder (report only)", "Audit and repair folder", "Replace tracks from NML export" });
            LibraryField(source, "a.directory", "Music folder to scan");
            PathField(source, "a.nml", "Traktor NML export of broken tracks", "", "nml", "Every supported track listed in this export will be replaced at its original location.");
            Toggle(source, "a.full_verification", "Verify audio fingerprints as well as duration");
            Toggle(source, "a.rescan", "Recheck previously verified / repaired files");
            var limits = Card(page, "Duration checks");
            Text(limits, "a.duration_tolerance", "Allowed difference from expected duration (seconds)", "8");
            Text(limits, "a.min_duration", "Minimum duration (optional)", "", "Seconds or MM:SS / HH:MM:SS. Out-of-range files are reported and, in repair mode, replaced.");
            Text(limits, "a.max_duration", "Maximum duration (optional)");
            Compact("a.min_duration", "a.max_duration");
            Text(limits, "a.limit", "Process at most this many files (optional)", "", "Leave blank to process the entire selection. Use a small number for a trial.");
            Select(limits, "a.spotify", "Spotify catalog lookups", new[] { "Auto (use saved credentials)", "Enabled", "Disabled" });
            var repair = Card(page, "Replacement options", "Original audio is backed up before replacement. Close Traktor and tag editors before repairing.");
            rows["a.repair_card"] = (FrameworkElement)repair.Parent;
            Text(repair, "a.max_candidates", "Maximum replacement candidates per file", "5");
            Toggle(repair, "a.repair_unknown", "Attempt to repair unrecognized or ambiguous recordings");
            Toggle(repair, "a.no_reference_check", "Do not download temporary reference recordings");
            PathField(repair, "a.match_overrides", "Manual match overrides (optional JSON)", "", "json");
            PathField(repair, "a.cookies", "Cookies file (optional)", "", "file");
            var storage = Card(page, "Reports & tools");
            rows["a.storage_card"] = (FrameworkElement)storage.Parent;
            PathField(storage, "a.state_dir", "Reports and backups folder (optional)", "", "folder", "Blank uses _savify_audit under the source folder. Reuse it to resume a folder audit.");
            PathField(storage, "a.ffmpeg", "FFmpeg folder", Path.Combine(root, "ffmpeg"));
            PathField(storage, "a.fpcalc", "Chromaprint fpcalc executable (optional)", "", "exe", "Needed for fingerprint checks if FFmpeg was built without Chromaprint. Repair and fingerprint verification also require an AcoustID application key in Settings.");
        }

        private void BuildTraktor() {
            var page = pages["Traktor tools"];
            var card = Card(page, "Collection maintenance", "Cleanup removes only successfully repaired paths from the collection and playlists. It creates a clean re-import NML and a manifest to restore playlist positions.");
            Select(card, "t.mode", "Operation", new[] { "Remove repaired tracks for re-import", "Restore playlist positions after re-import" });
            PathField(card, "t.collection", "Active Traktor collection.nml", "", "nml");
            Text(card, "t.repair_logs", "Repair logs (one path per line)", "", "Choose replacements.jsonl from a GUI repair run, events.jsonl, report.csv, audit.log or a console log.", 3);
            var logs = Actions(card);
            rows["t.log_buttons"] = logs;
            Button(logs, "Add repair logs", () => {
                var dialog = new OpenFileDialog { Multiselect = true, Filter = "Repair logs|*.jsonl;*.csv;*.log;*.txt|All files|*.*" };
                if (dialog.ShowDialog(window) == true) Set("t.repair_logs", (Get("t.repair_logs") + "\n" + String.Join("\n", dialog.FileNames)).Trim());
            });
            Button(logs, "Use latest GUI repair", () => {
                if (lastRepairLog == null || !File.Exists(lastRepairLog)) throw new InvalidOperationException("Complete a library repair first, or choose a log from Run history.");
                Set("t.repair_logs", lastRepairLog);
            });
            PathField(card, "t.manifest", "Manifest from an applied cleanup", "", "json");
            var options = Card(page, "Options & review");
            Text(options, "t.log_encoding", "Legacy console-log encoding (optional)", "", "Usually blank. For an old Windows console log, for example: cp1252.");
            Text(options, "t.volume_maps", "Volume mappings (optional, one NAME=ROOT per line)", "", "Example: DJ Disk=J:  — drive-letter volumes do not need a mapping.", 2);
            Toggle(options, "t.apply", "Apply changes to the collection (back up the original first)");
            Note(options, "With Apply unchecked, this task creates a preview and leaves the collection unchanged. Close Traktor before applying changes. After cleanup, import reimport-repaired.nml using File tags, analyze the tracks, close Traktor, then restore playlist positions using the cleanup manifest.");
        }

        private void BuildHistory() {
            var card = Card(pages["Run history"], "Recent saved runs", "Enable Save run files before starting a task to keep its settings, logs and reports here. The latest 200 saved runs are listed.", fullWidth: true);
            historyGrid = new DataGrid { Height = 300, IsReadOnly = true, SelectionMode = DataGridSelectionMode.Single };
            Help(historyGrid, "Select a run to view its log, open its reports, repeat it or reuse its output in another task. Running tasks keep their live output in the console.");
            foreach (string name in new[] { "Started", "Task", "Status" })
                historyGrid.Columns.Add(new DataGridTextColumn { Header = name, Binding = new Binding(name), Width = new DataGridLength(1, DataGridLengthUnitType.Star) });
            ResponsivePanel.SetFullWidth(historyGrid, true);
            card.Children.Add(historyGrid);
            var actions = Actions(card);
            Button(actions, "Refresh", RefreshHistory);
            Button(actions, "View log", () => ViewLog(SelectedHistory()));
            Button(actions, "Open files", () => OpenPath(SelectedHistory().Folder));
            Button(actions, "Open report", () => {
                var item = SelectedHistory();
                if (String.IsNullOrEmpty(item.Artifact)) throw new InvalidOperationException("This run has no report. Open its files to inspect the console log.");
                OpenPath(item.Artifact);
            });
            Button(actions, "Load retry queue", () => ImportQueue(Path.Combine(SelectedHistory().Folder, "retry-queue.txt")));
            Button(actions, "Run again", () => Start(ReadObject(Path.Combine(SelectedHistory().Folder, "job.json"))), true);
            Button(actions, "Use repair log", () => {
                string log = Path.Combine(SelectedHistory().Folder, "replacements.jsonl");
                if (!File.Exists(log)) throw new FileNotFoundException("This run did not produce a replacement log.");
                Set("t.repair_logs", log); ((ComboBox)fields["t.mode"]).SelectedIndex = 0; navigation.SelectedItem = "Traktor tools";
            });
            Button(actions, "Use cleanup manifest", () => {
                string manifest = Path.Combine(SelectedHistory().Folder, "traktor", "manifest.json");
                if (!File.Exists(manifest)) throw new FileNotFoundException("This run did not produce a Traktor manifest.");
                var data = ReadObject(manifest);
                if (!data.ContainsKey("applied") || !(bool)data["applied"]) throw new InvalidOperationException("Use the manifest from an applied cleanup, after re-importing the repaired tracks.");
                Set("t.collection", Value(data, "collection")); Set("t.manifest", manifest);
                ((ComboBox)fields["t.mode"]).SelectedIndex = 1; navigation.SelectedItem = "Traktor tools";
            });
        }

        private void BuildSettings() {
            var page = pages["Settings & setup"];
            var libraryAndTools = new StackPanel();
            page.Children.Add(libraryAndTools);
            var appearance = Card(libraryAndTools, "Appearance", "Choose a palette for your workspace. Changes apply and save immediately.");
            var theme = Select(appearance, "ui.theme", "Theme", ThemeCatalog.Names);
            var darkMode = Toggle(appearance, "ui.dark_mode", "Dark mode", true);
            Action appearanceChanged = () => {
                if (building) return;
                ThemeCatalog.Apply(window, Get("ui.theme"), On("ui.dark_mode"));
                Safe(() => {
                    SavePreferences();
                    Named<TextBlock>("Status").Text = "Appearance saved: " + Get("ui.theme") + (On("ui.dark_mode") ? " (dark)" : " (light)");
                });
            };
            theme.SelectionChanged += (s, e) => appearanceChanged();
            darkMode.Checked += (s, e) => appearanceChanged();
            darkMode.Unchecked += (s, e) => appearanceChanged();
            var storage = Card(libraryAndTools, "Your music library", "The library path is shared with the existing Convenify launchers.");
            LibraryField(storage, "cfg.library", "Default music folder");
            var credentials = Card(page, "Service credentials", "Saved as Windows user environment variables, as in Convenify setup. They are not written to task snapshots or GUI preferences. Leave a field blank to keep its current value.");
            foreach (var definition in defaults.credentials) {
                var input = new PasswordBox();
                string saved = credentialEnvironment.Get(definition.environment, EnvironmentVariableTarget.User) ?? credentialEnvironment.Get(definition.environment, EnvironmentVariableTarget.Process);
                Field(credentials, definition.key, definition.label, input, String.IsNullOrWhiteSpace(saved) ? "Not configured yet." : "A saved value is available. Enter a new value to replace it.");
            }
            Button(Actions(credentials), "Save settings", SaveSettings, true);
            var tools = Card(libraryAndTools, "Tools & maintenance", "Use the environment check to inspect dependencies without making network requests.");
            var actions = Actions(tools);
            Button(actions, "Check environment", () => Start(new Dictionary<string, object> { { "action", "diagnostics" } }), true);
            Button(actions, "Install Deno", () => {
                if (MessageBox.Show(window, "Install Deno through winget for YouTube support? This downloads software and accepts the package and source agreements.", "Install Deno", MessageBoxButton.OKCancel, MessageBoxImage.Information) == MessageBoxResult.OK)
                    Start(new Dictionary<string, object> { { "action", "install_deno" } });
            }, true);
            Button(actions, "Update downloader", () => {
                if (MessageBox.Show(window, "Update yt-dlp and ytmusicapi in Convenify's bundled Python? This requires an internet connection.", "Update downloader", MessageBoxButton.OKCancel, MessageBoxImage.Information) == MessageBoxResult.OK)
                    Start(new Dictionary<string, object> { { "action", "update_downloader" } });
            }, true);
            Note(tools, "Downloads use the bundled FFmpeg. Library repair can use another FFmpeg folder and an optional fpcalc executable on the Library care page.");
            var files = Card(libraryAndTools, "Workspace files");
            Button(Actions(files), "Open Convenify folder", () => OpenPath(root));
            Note(files, "GUI preferences and queue drafts are kept in gui-data. Enable Save run files to also keep task history and logs. Playlist definitions are saved to playlists.json. Existing command-line launchers and per-type config.json arguments remain available.");
            var reset = Card(page, "Reset configuration", fullWidth: true);
            Note(reset, "Reset settings restores this tab, console preferences and shared CLI configuration, and removes saved credentials. Reset everything also restores every operation tab and clears saved playlists, local queues and drafts. Both keep music, run history, reports and recovery files. You will review a confirmation before any reset.");
            var resetActions = Actions(reset);
            Button(resetActions, "Reset settings", () => ConfirmReset(SettingsScope, false), true);
            Button(resetActions, "Reset everything", () => ConfirmReset(null, true), true);
            Note(reset, "For a clean release, use GUI/export_release.py instead of copying this working folder. To clear visible run history while preserving its files, close Convenify and move gui-data/runs outside this installation. The desktop guide has release instructions.");
        }

        private void BuildHelp() {
            var page = pages["Help"];
            var start = Card(page, "Start here");
            Note(start, "1. Open Settings & setup, select a music folder, enter your Spotify developer credentials and save.\n2. Run Check environment. Install Deno if it is missing.\n3. Paste a link on Download music or import a text queue. Choose your audio options and start.\n4. Watch the process output below. You can navigate the app while a task runs. Only one task runs at a time.");
            var organize = Card(page, "Queues & playlists");
            Note(organize, "The queue editor understands the same genre blocks as download.py. Queue text is saved as a draft between sessions; use Save queue as to write a text file. Discography collects links without downloading audio.\n\nPlaylist sync uses the selected saved entries and their destination folders. Add / update changes the list in the editor; Save playlists writes it for both the GUI and update_library.bat. Only newly created MP3 files receive genre tags.");
            var care = Card(page, "Audit, repair & Traktor");
            Note(care, "Audit folder reports issues without replacing music. Audit and repair also installs validated replacements and keeps backups. Fingerprint checks and repairs need an AcoustID application key and Chromaprint support. Matching duration alone does not prove audio identity; enable fingerprint verification for a deeper check.\n\nNML replacement replaces all supported tracks in an exported NML. It does not skip tracks on later runs. Folder audits use checkpoints and can resume with the same reports folder.\n\nUse the repair run's replacements.jsonl in Traktor tools. Preview first, then Apply with Traktor closed. Import the generated reimport-repaired.nml using File tags, analyze the tracks, close Traktor and restore playlist positions with the applied cleanup manifest.");
            var runs = Card(page, "Output, stopping & reports");
            Note(runs, "The log shows both stdout and stderr. Show DEBUG includes Savify's detailed diagnostics. Turn off Follow output to read earlier lines. Drag the divider to resize the log.\n\nSave run files is off by default. Turn it on before a task to keep full logs, settings and reports in Run history. Show run files opens the last saved run in this session and is disabled after an unsaved run. Save log exports the full saved log, or only the recent console buffer if automatic saving was off. Clear view clears buffered messages, including hidden DEBUG messages.\n\nStop task terminates the worker and its child processes. Completed music, requested outputs, repair records and recovery backups are always kept. Folder repairs can resume from their checkpoints. Traktor outputs are saved beside the collection when run-file saving is off; inspect the manifest and original backup before repeating an interrupted apply.\n\nFinished means the operation exited successfully. Needs attention means some downloads failed, the audit found issues, or setup is incomplete. Inspect the output/report for details. Saved runs also support retry queues and Run again.");
            Button(Actions(runs), "Open desktop guide", () => OpenPath(Path.Combine(root, "GUI", "README.md")));
        }

        private void RefreshVisibility() {
            if (building) return;
            bool queue = Index("d.source") == 1;
            string query = Get("d.query");
            bool link = query.StartsWith("http", StringComparison.OrdinalIgnoreCase) || query.StartsWith("spotify:", StringComparison.OrdinalIgnoreCase);
            Show("d.query", !queue); Show("d.search_type", !queue && !link);
            Show("d.queue", queue); Show("d.queue_buttons", queue);
            bool artist = queue ? Get("d.queue").Contains("/artist/") || Get("d.queue").Contains("spotify:artist:") : query.Contains("/artist/") || query.StartsWith("spotify:artist:") || (!link && Get("d.search_type") == "artist");
            Show("d.artist_albums", artist);
            foreach (string prefix in new[] { "d.", "s." }) {
                string format = Get(prefix + "format");
                Show(prefix + "quality", format != "flac" && format != "wav");
                Show(prefix + "skip_cover_art", format == "mp3");
                Show(prefix + "group", Index(prefix + "group_mode") == 2);
                Show(prefix + "verify_existing", !On(prefix + "redownload"));
                if (prefix == "d.") Show(prefix + "genre", format == "mp3");
                else Show("p.genre", format == "mp3");
            }
            int mode = Index("a.mode");
            bool repair = mode != 0, nml = mode == 2, full = !nml && On("a.full_verification");
            Show("a.directory", !nml); Show("a.nml", nml); Show("a.full_verification", !nml); Show("a.rescan", !nml);
            Show("a.repair_card", repair); Show("a.repair_unknown", repair && full); Show("a.no_reference_check", repair && full);
            ResponsivePanel.SetFullWidth(rows["a.storage_card"], !repair);
            Show("a.fpcalc", repair || full);
            bool restore = Index("t.mode") == 1;
            Show("t.manifest", restore); Show("t.repair_logs", !restore); Show("t.log_buttons", !restore); Show("t.log_encoding", !restore);
            RefreshRunButton();
        }

        private void Navigate(string page) {
            currentPage = page;
            var host = Named<StackPanel>("PageHost");
            host.Children.Clear(); host.Children.Add(pages[page]);
            Named<TextBlock>("PageTitle").Text = page;
            Named<TextBlock>("PageDescription").Text = descriptions[Array.IndexOf(pageNames, page)];
            Named<ScrollViewer>("FormScroll").ScrollToTop();
            if (page == "Run history") RefreshHistory();
            RefreshRunButton();
        }

        private void RefreshRunButton() {
            var reset = Named<Button>("ResetTab");
            reset.Visibility = currentPage != SettingsScope && defaultsRegistry.HasScope(currentPage) ? Visibility.Visible : Visibility.Collapsed;
            reset.IsEnabled = !busy;
            string label = null, hint = "";
            switch (currentPage) {
                case "Download music": label = Index("d.source") == 1 ? "Download queue" : "Start download"; hint = "Existing files are skipped unless you choose to replace or verify them."; break;
                case "Playlist sync": label = "Sync selected playlists"; hint = playlistDirty ? "Playlist edits are not saved. Sync uses the list currently shown." : "Only checked playlists will be processed."; break;
                case "Discography": label = "Collect releases"; hint = "Collect links now; download them from the queue when you are ready."; break;
                case "Library care": label = new[] { "Start audit", "Start repair", "Replace NML tracks" }[Index("a.mode")]; hint = Index("a.mode") == 0 ? "Report only: your music files are not replaced." : "Replacements are validated and original audio is backed up."; break;
                case "Traktor tools": label = On("t.apply") ? "Apply collection changes" : "Create preview"; hint = On("t.apply") ? "Close Traktor before applying changes. An original backup will be kept." : "Preview creates reports without changing the active collection."; break;
            }
            runButton.Visibility = label == null ? Visibility.Collapsed : Visibility.Visible;
            runButton.Content = label ?? "Run";
            if (label != null) Help(runButton, OptionHelp.ForAction(label) + (busy ? "\n\nWait for the running task to finish, or stop it first." : ""));
            runButton.IsEnabled = !busy;
            Named<TextBlock>("PageHint").Text = busy ? "A task is running. You can browse pages and follow its output below." : hint;
        }

        private Dictionary<string, object> BuildJob() {
            var job = new Dictionary<string, object>();
            if (currentPage == "Download music" || currentPage == "Playlist sync") {
                bool sync = currentPage == "Playlist sync";
                string p = sync ? "s." : "d.";
                job["action"] = sync ? "sync" : "download";
                Require(p + "output", "Choose a music folder.");
                foreach (string key in new[] { "output", "format", "workers", "search_results", "match_threshold", "match_overrides", "cookies", "temp_path", "proxy", "request_delay" }) job[key] = Get(p + key);
                if (Visible(p + "quality")) job["quality"] = Get(p + "quality");
                foreach (string key in new[] { "m3u", "no_youtube_music", "no_match_cache", "redownload" }) job[key] = On(p + key);
                job["verify_existing"] = Visible(p + "verify_existing") && On(p + "verify_existing");
                job["skip_cover_art"] = Visible(p + "skip_cover_art") && On(p + "skip_cover_art");
                job["use_default_group"] = Index(p + "group_mode") == 0;
                job["group"] = Index(p + "group_mode") == 2 ? Get(p + "group") : (sync && Index(p + "group_mode") == 0 ? "%playlist%" : "");
                if (sync) {
                    playlistGrid.CommitEdit(DataGridEditingUnit.Cell, true); playlistGrid.CommitEdit(DataGridEditingUnit.Row, true);
                    var selected = playlists.Where(item => item.Selected).Select(item => new Dictionary<string, object> { { "name", item.Name }, { "path", item.Folder }, { "link", item.Link }, { "genre", Get(p + "format") == "mp3" ? item.Genre : "" } }).ToArray();
                    if (selected.Length == 0) throw new InvalidOperationException("Tick at least one playlist to sync.");
                    job["playlists"] = selected;
                } else {
                    bool queue = Index("d.source") == 1;
                    Require(queue ? "d.queue" : "d.query", queue ? "Add Spotify links to the queue." : "Enter a link or search query.");
                    job["source"] = queue ? "queue" : "single";
                    job["queue"] = queue ? ((TextBox)fields["d.queue"]).Text : "";
                    job["query"] = queue ? "" : Get("d.query");
                    job["search_type"] = Get("d.search_type");
                    job["genre"] = Visible("d.genre") ? Get("d.genre") : "";
                    job["organize"] = On("d.organize");
                    job["artist_albums"] = Visible("d.artist_albums") && On("d.artist_albums");
                }
            } else if (currentPage == "Discography") {
                Require("c.artist", "Enter a Spotify artist link or ID."); Require("c.output", "Choose an output text file.");
                job["action"] = "discography";
                foreach (string key in new[] { "artist", "market", "output" }) job[key] = Get("c." + key);
                job["append_queue"] = Index("c.mode") == 0;
            } else if (currentPage == "Library care") {
                int mode = Index("a.mode");
                Require(mode == 2 ? "a.nml" : "a.directory", mode == 2 ? "Choose an exported NML file." : "Choose the music folder to scan.");
                job["action"] = "audit"; job["mode"] = new[] { "audit", "repair", "nml" }[mode];
                foreach (string key in new[] { "directory", "nml", "duration_tolerance", "min_duration", "max_duration", "limit", "state_dir", "ffmpeg", "fpcalc" })
                    if (Visible("a." + key)) job[key] = Get("a." + key);
                foreach (string key in new[] { "full_verification", "rescan", "repair_unknown", "no_reference_check" })
                    job[key] = Visible("a." + key) && On("a." + key);
                job["spotify"] = new[] { "auto", "enabled", "disabled" }[Index("a.spotify")];
                if (mode != 0) foreach (string key in new[] { "max_candidates", "cookies", "match_overrides" }) job[key] = Get("a." + key);
            } else if (currentPage == "Traktor tools") {
                Require("t.collection", "Choose the active Traktor collection.nml.");
                job["action"] = "traktor"; job["mode"] = Index("t.mode") == 1 ? "restore" : "cleanup";
                foreach (string key in new[] { "collection", "manifest", "repair_logs", "log_encoding", "volume_maps" }) if (Visible("t." + key)) job[key] = Get("t." + key);
                job["apply"] = On("t.apply");
            } else throw new InvalidOperationException("Choose an operation from the sidebar.");
            return job;
        }

        private void Require(string key, string message) {
            if (String.IsNullOrWhiteSpace(Get(key))) { fields[key].Focus(); throw new InvalidOperationException(message); }
        }

        private void Start(Dictionary<string, object> job) {
            if (busy) throw new InvalidOperationException("Wait for the current task, or stop it first.");
            string action = Value(job, "action");
            bool repair = action == "audit" && Value(job, "mode") != "audit";
            bool apply = action == "traktor" && job.ContainsKey("apply") && (bool)job["apply"];
            bool overwrite = action == "discography" && !(bool)job["append_queue"] && File.Exists(Value(job, "output"));
            if (repair || apply || overwrite) {
                string message = repair ? "Start replacing audio in the selected library or NML export? Validated replacements are installed at the original paths and originals are backed up. Close Traktor and tag editors first."
                    : apply ? "Apply these collection changes? Close Traktor first. The tool keeps an original backup and manifest."
                    : "Replace the existing discography text file?";
                if (MessageBox.Show(window, message, "Confirm task", MessageBoxButton.OKCancel, MessageBoxImage.Question) != MessageBoxResult.OK) return;
            }
            string python = Path.Combine(root, "python", "python.exe");
            if (!File.Exists(python)) throw new FileNotFoundException("The bundled Python is missing: " + python);
            SavePreferences();
            try {
                PrepareRun(job);
                runner.Start(root, Path.Combine(root, "GUI", "convenify_worker.py"), Path.Combine(runFolder, "job.json"), viewedLog);
                SetBusy(true);
                Named<TextBlock>("Status").Text = "Running: " + action;
                var progress = Named<ProgressBar>("Progress");
                progress.Value = 0; progress.Maximum = 100; progress.IsIndeterminate = true;
            } catch {
                if (activeMetadata != null) {
                    activeMetadata["status"] = "Could not start";
                    try { if (saveRunFiles && Directory.Exists(runFolder)) WriteObject(Path.Combine(runFolder, "run.json"), activeMetadata); }
                    catch (Exception ex) { Append("Could not save run status: " + ex.Message + "\n"); }
                }
                CleanupTemporaryRun();
                UpdateRunFilesButton();
                throw;
            }
        }

        private void PrepareRun(Dictionary<string, object> job) {
            saveRunFiles = On("ui.save_run_files");
            job["save_run_files"] = saveRunFiles;
            started = DateTime.Now; artifact = null; activeAction = Value(job, "action");
            string id = started.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N");
            runFolder = saveRunFiles ? Path.Combine(dataRoot, "runs", id)
                : Path.Combine(Path.GetTempPath(), "Convenify", id);
            viewedLog = saveRunFiles ? Path.Combine(runFolder, "console.log") : null;
            activeMetadata = new Dictionary<string, object> { { "started", started.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture) }, { "task", activeAction }, { "status", "Running" } };
            Directory.CreateDirectory(runFolder);
            WriteObject(Path.Combine(runFolder, "job.json"), job);
            if (saveRunFiles) WriteObject(Path.Combine(runFolder, "run.json"), activeMetadata);
            ClearConsole();
            Append("Starting " + activeAction + "...\n" + (saveRunFiles ? "Run files: " + runFolder : "Run files and logs will not be saved automatically.") + "\n\n");
            UpdateRunFilesButton();
        }

        private void CleanupTemporaryRun() {
            if (saveRunFiles || runFolder == null || !Directory.Exists(runFolder)) return;
            try {
                // Only remove our own job transport file; never recursively delete
                // task outputs or a folder supplied by a job/history entry.
                string parent = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "Convenify"));
                string folder = Path.GetFullPath(runFolder);
                if (!String.Equals(Path.GetDirectoryName(folder), parent, StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Unexpected temporary run location.");
                File.Delete(Path.Combine(folder, "job.json"));
                Directory.Delete(folder, false);
            } catch (Exception ex) { Append("Could not remove temporary task settings: " + ex.Message + "\n"); }
        }

        private void UpdateRunFilesButton() {
            var button = Named<Button>("OpenRun");
            button.IsEnabled = saveRunFiles && runFolder != null && Directory.Exists(runFolder);
            Help(button, button.IsEnabled ? "Open the files and logs saved for the current or last run. Earlier saved runs are available in Run history."
                : runFolder == null ? "No saved run is available in this session. Enable Save run files before starting a task, or open an earlier saved run from Run history."
                : saveRunFiles ? "The saved folder for this run is no longer available. It may have been moved or removed."
                : "This run did not save run files. Enable Save run files before starting the next task to keep its logs and run history.");
            Help(Named<Button>("SaveLog"), viewedLog != null
                ? "Export the full saved console log, including DEBUG messages and output no longer held in the on-screen view."
                : "Export the output still held in the console buffer, including hidden DEBUG messages. Older or cleared messages are unavailable because automatic log saving is off.");
        }

        private void SetBusy(bool value) {
            busy = value;
            foreach (var page in pages) if (page.Key != "Run history" && page.Key != "Help") page.Value.IsEnabled = !value;
            foreach (var button in mutationButtons) button.IsEnabled = !value;
            cancelButton.IsEnabled = value;
            Named<CheckBox>("SaveRunFiles").IsEnabled = !value;
            RefreshRunButton();
        }
        private void Stop() {
            runner.Cancel();
            cancelButton.IsEnabled = false;
            Named<TextBlock>("Status").Text = "Stopping task and child processes...";
        }
        private void DrainOutput() {
            var batch = new StringBuilder();
            RunnerMessage message;
            int lines = 0;
            while (lines++ < 150 && batch.Length < 40000 && runner.TryRead(out message)) {
                if (message.ExitCode.HasValue) {
                    if (batch.Length > 0) { Append(batch.ToString()); batch.Clear(); }
                    Finish(message.ExitCode.Value);
                    break;
                }
                if (message.Text.StartsWith("@@CONVENIFY@@", StringComparison.Ordinal)) HandleEvent(message.Text.Substring(13));
                else batch.AppendLine(message.Text);
            }
            if (batch.Length > 0) Append(batch.ToString());
            if (busy) Named<TextBlock>("Elapsed").Text = (DateTime.Now - started).ToString(@"hh\:mm\:ss");
        }
        private void HandleEvent(string source) {
            try {
                var data = json.Deserialize<Dictionary<string, object>>(source);
                string kind = Value(data, "event");
                if (kind == "progress") {
                    double total = Convert.ToDouble(data["total"], CultureInfo.InvariantCulture);
                    var progress = Named<ProgressBar>("Progress"); progress.IsIndeterminate = false;
                    progress.Maximum = Math.Max(1, total); progress.Value = Convert.ToDouble(data["current"], CultureInfo.InvariantCulture);
                    Named<TextBlock>("Status").Text = Value(data, "message");
                } else if (kind == "artifact") {
                    artifact = Value(data, "path");
                    Append(Value(data, "label") + ": " + artifact + "\n");
                } else if (kind == "repair_log") lastRepairLog = Value(data, "path");
            } catch { Append(source + "\n"); }
        }
        private void Finish(int code) {
            string status = code == 0 ? "Finished" : code == 130 ? "Stopped" : code == 1 ? "Needs attention" : "Failed";
            Append("\n" + status + " (exit code " + code + "). " + (saveRunFiles
                ? "Process output was written to this run's console.log."
                : "Run files and logs were not saved. Save log can export the remaining console buffer.") + "\n");
            Named<TextBlock>("Status").Text = status + " - " + activeAction;
            var progress = Named<ProgressBar>("Progress"); progress.IsIndeterminate = false;
            if (code == 0) progress.Value = progress.Maximum;
            Named<TextBlock>("Elapsed").Text = (DateTime.Now - started).ToString(@"hh\:mm\:ss");
            activeMetadata["status"] = status;
            activeMetadata["exit_code"] = code;
            activeMetadata["artifact"] = artifact ?? "";
            activeMetadata["elapsed_seconds"] = (int)(DateTime.Now - started).TotalSeconds;
            try { if (saveRunFiles) WriteObject(Path.Combine(runFolder, "run.json"), activeMetadata); }
            catch (Exception ex) { Append("Could not save run status: " + ex.Message + "\n"); }
            CleanupTemporaryRun();
            UpdateRunFilesButton();
            SetBusy(false);
            if (currentPage == "Run history") RefreshHistory();
            if (closingAfterCancel) window.Close();
        }
        private void Append(string text) {
            bool replace;
            string visible = logBuffer.Append(text, On("ui.show_debug"), out replace);
            double offset = console.VerticalOffset;
            if (replace) console.Text = visible;
            else if (visible.Length > 0) console.AppendText(visible);
            if (Named<CheckBox>("FollowLog").IsChecked == true) console.ScrollToEnd();
            else if (replace) console.ScrollToVerticalOffset(offset);
        }
        private void RebuildConsole() {
            double offset = console.VerticalOffset;
            console.Text = logBuffer.Render(On("ui.show_debug"));
            if (Named<CheckBox>("FollowLog").IsChecked == true) console.ScrollToEnd();
            else console.ScrollToVerticalOffset(offset);
        }
        private void ClearConsole() { logBuffer.Clear(); console.Clear(); }

        private Dictionary<string, object> ReadObject(string path) {
            if (!File.Exists(path)) return new Dictionary<string, object>();
            var result = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8));
            if (result == null) throw new InvalidDataException(Path.GetFileName(path) + " must contain a JSON object.");
            return result;
        }
        private static string Value(Dictionary<string, object> data, string key) {
            object value;
            return data.TryGetValue(key, out value) && value != null ? Convert.ToString(value, CultureInfo.InvariantCulture) : "";
        }
        private void WriteObject(string path, object value, bool keepBackup = true) {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try {
                File.WriteAllText(temporary, json.Serialize(value), new UTF8Encoding(false));
                if (File.Exists(path)) File.Replace(temporary, path, keepBackup ? path + ".bak" : null, true);
                else File.Move(temporary, path);
                if (!keepBackup) AppDefaults.RemoveOldCopies(path);
            } finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        private void SavePreferences() { SavePreferencesWithBackup(true); }
        private void SavePreferencesWithBackup(bool keepBackup) {
            var values = new Dictionary<string, object>();
            foreach (var field in fields) {
                if (field.Value is PasswordBox || field.Key.StartsWith("cfg.") || field.Key == "t.apply") continue;
                if (field.Value is CheckBox) values[field.Key] = On(field.Key);
                else if (field.Key == "ui.theme") values[field.Key] = Get(field.Key);
                else if (field.Value is ComboBox) values[field.Key] = Index(field.Key);
                else if (field.Value is TextBox) values[field.Key] = ((TextBox)field.Value).Text;
            }
            values["last_repair_log"] = lastRepairLog ?? "";
            values["playlist_dirty"] = playlistDirty;
            if (playlistDirty) values["playlist_draft"] = playlists.ToArray();
            WriteObject(Path.Combine(dataRoot, "preferences.json"), values, keepBackup);
        }
        private void LoadPreferences() {
            try {
                var values = ReadObject(Path.Combine(dataRoot, "preferences.json"));
                foreach (var value in values) {
                    Control control;
                    if (!fields.TryGetValue(value.Key, out control) || control is PasswordBox || value.Key.StartsWith("cfg.") || value.Key == "t.apply") continue;
                    if (value.Key == "ui.theme") {
                        ((ComboBox)control).SelectedItem = ThemeCatalog.Normalize(value.Value as string);
                    }
                    else if (control is CheckBox && value.Value is bool) ((CheckBox)control).IsChecked = (bool)value.Value;
                    else if (control is ComboBox && value.Value is int) {
                        int index = (int)value.Value;
                        if (index >= 0 && index < ((ComboBox)control).Items.Count) ((ComboBox)control).SelectedIndex = index;
                    } else if (control is TextBox && value.Value is string) ((TextBox)control).Text = (string)value.Value;
                }
                lastRepairLog = Value(values, "last_repair_log");
                if (values.ContainsKey("playlist_dirty") && values["playlist_dirty"] is bool && (bool)values["playlist_dirty"] && values.ContainsKey("playlist_draft")) {
                    var draft = json.ConvertToType<PlaylistRow[]>(values["playlist_draft"]);
                    playlists.Clear(); foreach (var item in draft) playlists.Add(item);
                    playlistDirty = true;
                }
            } catch (Exception ex) { Append("Could not load GUI preferences: " + ex.Message + "\n"); }
        }
        private void SaveSettings() {
            Require("cfg.library", "Choose a default music folder.");
            string folder = Path.GetFullPath(Environment.ExpandEnvironmentVariables(Get("cfg.library")));
            var config = ReadObject(Path.Combine(root, "config.json"));
            // Preserve every existing CLI option and unknown config key.
            Directory.CreateDirectory(folder);
            config["library_path"] = folder;
            foreach (var entry in defaults.Config) if (!config.ContainsKey(entry.Key)) config[entry.Key] = entry.Value;
            WriteObject(Path.Combine(root, "config.json"), config);
            foreach (var definition in defaults.credentials) {
                string value = Get(definition.key);
                if (String.IsNullOrWhiteSpace(value)) continue;
                credentialEnvironment.Set(definition.environment, value, EnvironmentVariableTarget.User);
                credentialEnvironment.Set(definition.environment, value, EnvironmentVariableTarget.Process);
                ((PasswordBox)fields[definition.key]).Clear();
                var hint = ((StackPanel)rows[definition.key]).Children.OfType<TextBlock>().LastOrDefault();
                if (hint != null) hint.Text = "A saved value is available. Enter a new value to replace it.";
            }
            foreach (var entry in defaultsRegistry.Entries.Where(e => e.InitialConfigKey == "library_path")) Set(entry.Key, folder);
            SavePreferences();
            Named<TextBlock>("Status").Text = "Settings saved";
            Append("Library settings and supplied credentials saved. New tasks will use them immediately.\n");
        }

        private void ConfirmReset(string scope, bool everything) {
            if (busy) throw new InvalidOperationException("Wait for the running task to finish before resetting configuration.");
            string title = everything ? "Reset everything" : scope == SettingsScope ? "Reset settings" : "Reset " + scope;
            string message = everything
                ? "Restore defaults for every operation, appearance and console option, shared CLI settings and saved credentials? This clears saved playlist definitions, installation-local queue files, all drafts and remembered paths."
                : scope == SettingsScope
                    ? "Restore appearance and console defaults, clear the shared library path, restore default CLI options and remove saved Spotify/AcoustID credentials? Operation-tab values and saved playlists are kept."
                    : "Restore every option on " + scope + " to its original default, including hidden options and entered text?" + (scope == "Playlist sync" ? " Unsaved playlist edits will be discarded and saved definitions reloaded." : "");
            if (everything || scope == SettingsScope) message += "\n\nCredentials are removed from this process, your Windows user environment and the installation's legacy credential files. This also affects the command-line tools. Machine-level environment settings are kept.";
            message += "\n\nMusic, run history, reports, repair records and recovery backups are kept. Reset applies immediately and is saved.";
            if (MessageBox.Show(window, message, title, MessageBoxButton.OKCancel, MessageBoxImage.Warning, MessageBoxResult.Cancel) == MessageBoxResult.OK)
                ResetConfiguration(scope, everything);
        }

        private void ResetConfiguration(string scope, bool everything) {
            if (busy) throw new InvalidOperationException("Wait for the running task to finish before resetting configuration.");
            if (!everything && !defaultsRegistry.HasScope(scope)) throw new ArgumentException("This page has no settings to reset.");
            bool shared = everything || scope == SettingsScope;
            // Validate all targets before the first mutation. No recursive deletes.
            var files = shared ? defaults.files.Where(f => everything || f.scope == "settings").ToArray() : new DefaultFile[0];
            foreach (var file in files) AppDefaults.LocalPath(root, file.path);
            AppDefaults.LocalPath(root, "gui-data/preferences.json");
            building = true;
            try {
                foreach (var file in files) {
                    string path = AppDefaults.LocalPath(root, file.path);
                    if (file.value == null) { File.Delete(path); AppDefaults.RemoveOldCopies(path); }
                    else WriteObject(path, file.value, false);
                }
                if (shared) foreach (var definition in defaults.credentials) {
                    credentialEnvironment.Set(definition.environment, null, EnvironmentVariableTarget.User);
                    credentialEnvironment.Set(definition.environment, null, EnvironmentVariableTarget.Process);
                    var hint = ((StackPanel)rows[definition.key]).Children.OfType<TextBlock>().LastOrDefault();
                    if (hint != null) hint.Text = "Not configured yet.";
                }
                if (everything || scope == "Playlist sync") {
                    playlistGrid.CancelEdit();
                    playlistGrid.SelectedItem = null;
                    playlists.Clear(); playlistExtras.Clear(); playlistDirty = false; playlistsReadable = true;
                    if (!everything) LoadPlaylists();
                }
                defaultsRegistry.Reset(everything ? null : scope);
                if (everything) {
                    lastRepairLog = runFolder = viewedLog = artifact = activeAction = null;
                    activeMetadata = null; saveRunFiles = false;
                    ClearConsole(); UpdateRunFilesButton();
                    Named<ProgressBar>("Progress").Value = 0;
                    Named<TextBlock>("Elapsed").Text = "";
                }
                SavePreferencesWithBackup(false);
            } finally {
                building = false;
                ThemeCatalog.Apply(window, Get("ui.theme"), On("ui.dark_mode"));
                RebuildConsole(); RefreshVisibility(); RefreshRunButton();
            }
            Named<TextBlock>("Status").Text = everything ? "All configuration reset; run files kept" : "Defaults restored: " + scope;
        }

        private void LoadPlaylists() {
            try {
                var source = ReadObject(Path.Combine(root, "playlists.json"));
                foreach (var entry in source) {
                    var data = entry.Value as Dictionary<string, object>;
                    if (entry.Key == "library-path" || entry.Key == "playlist-name" || data == null) { playlistExtras[entry.Key] = entry.Value; continue; }
                    playlists.Add(new PlaylistRow { Selected = true, Name = entry.Key, Folder = Value(data, "path"), Link = Value(data, "link"), Genre = Value(data, "genre") });
                }
            } catch (Exception ex) {
                playlistsReadable = false;
                Append("Could not read playlists.json. It will not be overwritten: " + ex.Message + "\n");
            }
        }
        private void ClearPlaylistEditor() {
            playlistGrid.SelectedItem = null;
            foreach (string key in new[] { "p.name", "p.link", "p.folder", "p.genre" }) Set(key, "");
            fields["p.name"].Focus();
        }
        private void UpdatePlaylist() {
            Require("p.name", "Enter a playlist name."); Require("p.link", "Enter a Spotify playlist link."); Require("p.folder", "Enter a destination folder.");
            string name = Get("p.name");
            if (name == "library-path" || name == "playlist-name") throw new InvalidOperationException("That name is reserved by the legacy playlist format. Choose another name.");
            if (!System.Text.RegularExpressions.Regex.IsMatch(Get("p.link"), @"^(https?://open\.spotify\.com/(intl-[A-Za-z-]+/)?playlist/[A-Za-z0-9]+([/?].*)?|spotify:playlist:[A-Za-z0-9]+)$"))
                throw new InvalidOperationException("Use a Spotify playlist link or URI.");
            var selected = playlistGrid.SelectedItem as PlaylistRow;
            if (playlists.Any(item => item.Name == name && item != selected)) throw new InvalidOperationException("A playlist with that name already exists. Select it to edit, or use another name.");
            if (selected == null) { selected = new PlaylistRow { Selected = true }; playlists.Add(selected); }
            selected.Name = name; selected.Link = Get("p.link"); selected.Folder = Get("p.folder");
            if (Visible("p.genre")) selected.Genre = Get("p.genre");
            playlistGrid.Items.Refresh(); playlistGrid.SelectedItem = selected;
            playlistDirty = true; RefreshRunButton();
        }
        private void RemovePlaylist() {
            var selected = playlistGrid.SelectedItem as PlaylistRow;
            if (selected == null) throw new InvalidOperationException("Select a playlist row first.");
            playlists.Remove(selected); ClearPlaylistEditor(); playlistDirty = true; RefreshRunButton();
        }
        private void SavePlaylists() {
            if (!playlistsReadable) throw new InvalidOperationException("The original playlists.json could not be read. Correct it or restore a backup before saving from the GUI.");
            var previous = ReadObject(Path.Combine(root, "playlists.json"));
            var data = new Dictionary<string, object>(playlistExtras);
            foreach (var item in playlists) {
                object saved;
                var entry = previous.TryGetValue(item.Name, out saved) && saved is Dictionary<string, object> ? new Dictionary<string, object>((Dictionary<string, object>)saved) : new Dictionary<string, object>();
                entry["path"] = item.Folder; entry["link"] = NormalizePlaylistLink(item.Link); entry["genre"] = item.Genre ?? "";
                data[item.Name] = entry;
            }
            WriteObject(Path.Combine(root, "playlists.json"), data);
            playlistDirty = false; SavePreferences(); RefreshRunButton();
            Named<TextBlock>("Status").Text = "Playlists saved";
        }
        private static string NormalizePlaylistLink(string link) {
            if (link.StartsWith("spotify:playlist:", StringComparison.Ordinal)) return "https://open.spotify.com/playlist/" + link.Substring("spotify:playlist:".Length);
            return System.Text.RegularExpressions.Regex.Replace(link, @"/intl-[A-Za-z-]+/", "/");
        }

        private void ImportQueue(string path) {
            if (busy) throw new InvalidOperationException("Wait for the running task before editing the queue.");
            if (path == null) {
                var dialog = new OpenFileDialog { Filter = "Text queues|*.txt|All files|*.*" };
                if (dialog.ShowDialog(window) != true) return;
                path = dialog.FileName;
            }
            if (!File.Exists(path)) throw new FileNotFoundException("Queue file not found: " + path);
            if (new FileInfo(path).Length > 4 * 1024 * 1024) throw new InvalidOperationException("This queue exceeds the editor's 4 MB limit. Split it into smaller queues.");
            if (!String.IsNullOrWhiteSpace(Get("d.queue")) && MessageBox.Show(window, "Replace the queue editor's current draft with this file?", "Load queue", MessageBoxButton.OKCancel, MessageBoxImage.Question) != MessageBoxResult.OK) return;
            Set("d.queue", File.ReadAllText(path, Encoding.UTF8));
            ((ComboBox)fields["d.source"]).SelectedIndex = 1;
            navigation.SelectedItem = "Download music";
        }
        private void SaveQueue() {
            var dialog = new SaveFileDialog { Filter = "Text queues|*.txt", FileName = "download-queue.txt", InitialDirectory = root, OverwritePrompt = true };
            if (dialog.ShowDialog(window) == true) {
                File.WriteAllText(dialog.FileName, ((TextBox)fields["d.queue"]).Text, new UTF8Encoding(false));
                Named<TextBlock>("Status").Text = "Queue saved";
            }
        }
        private HistoryRow SelectedHistory() {
            var row = historyGrid.SelectedItem as HistoryRow;
            if (row == null) throw new InvalidOperationException("Select a run first.");
            return row;
        }
        private void RefreshHistory() {
            string current = busy ? runFolder : null;
            Task.Run(() => {
                var result = new List<HistoryRow>();
                try {
                    string runs = Path.Combine(dataRoot, "runs");
                    if (Directory.Exists(runs)) foreach (string folder in Directory.EnumerateDirectories(runs).OrderByDescending(p => p).Take(200)) {
                        try {
                            var serializer = new JavaScriptSerializer();
                            string meta = Path.Combine(folder, "run.json");
                            if (!File.Exists(meta)) continue;
                            var data = serializer.Deserialize<Dictionary<string, object>>(File.ReadAllText(meta, Encoding.UTF8));
                            string status = Value(data, "status");
                            if (status == "Running" && folder != current) status = "Interrupted";
                            result.Add(new HistoryRow { Started = Value(data, "started"), Task = Value(data, "task"), Status = status, Folder = folder, Artifact = Value(data, "artifact") });
                        } catch { /* A damaged history entry must not hide all other runs. */ }
                    }
                } catch { }
                window.Dispatcher.BeginInvoke(new Action(() => historyGrid.ItemsSource = result));
            });
        }
        private void ViewLog(HistoryRow row) {
            if (busy) throw new InvalidOperationException("The console is following the running task. Open the old run's files to read its log separately.");
            string path = Path.Combine(row.Folder, "console.log");
            Task.Run(() => {
                try {
                    string text;
                    using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite)) {
                        bool tail = stream.Length > 240000;
                        if (tail) stream.Seek(-240000, SeekOrigin.End);
                        using (var reader = new StreamReader(stream, Encoding.UTF8)) {
                            if (tail) reader.ReadLine();
                            text = (tail ? "[Showing the end of this log. Save log exports the full file.]\n" : "") + reader.ReadToEnd();
                        }
                    }
                    window.Dispatcher.BeginInvoke(new Action(() => {
                        if (busy) return;
                        viewedLog = path; ClearConsole(); Append(text); UpdateRunFilesButton();
                        Named<TextBlock>("Status").Text = row.Status + " - " + row.Task + " - " + row.Started;
                    }));
                } catch (Exception ex) { window.Dispatcher.BeginInvoke(new Action(() => Append("Could not open log: " + ex.Message + "\n"))); }
            });
        }
        private void ExportLog() {
            var dialog = new SaveFileDialog { Filter = "Log files|*.log|Text files|*.txt", FileName = "Convenify-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log", OverwritePrompt = true };
            if (dialog.ShowDialog(window) != true) return;
            string source = viewedLog, destination = dialog.FileName, text = logBuffer.Render(true);
            if (source != null && String.Equals(Path.GetFullPath(source), Path.GetFullPath(destination), StringComparison.OrdinalIgnoreCase)) return;
            Task.Run(() => {
                try {
                    if (source != null && File.Exists(source)) File.Copy(source, destination, true);
                    else File.WriteAllText(destination, text, new UTF8Encoding(false));
                } catch (Exception ex) { window.Dispatcher.BeginInvoke(new Action(() => MessageBox.Show(window, ex.Message, "Could not save log"))); }
            });
        }
        private void OpenPath(string path) {
            if (!File.Exists(path) && !Directory.Exists(path)) throw new FileNotFoundException("This file or folder does not exist yet: " + path);
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
        }

        public static void Run(string root) {
            var app = new DesktopApp(root);
            var application = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose, MainWindow = app.window };
            application.Run(app.window);
        }
        public static void SmokeTest(string root) {
            var app = new DesktopApp(root);
            app.timer.Stop();
            // Exercise real WPF controls and the same job builder used by Run.
            Action<bool, string> check = (condition, label) => { if (!condition) throw new InvalidOperationException("Smoke test failed: " + label); Console.WriteLine("PASS " + label); };
            check(app.window.WindowState == WindowState.Maximized, "Window opens maximized");
            check(app.window.Width <= SystemParameters.WorkArea.Width && app.window.Height <= SystemParameters.WorkArea.Height,
                "Restored window fits the usable screen area");
            check(app.Named<Image>("BrandBanner").Source != null, "Sidebar loads GithubBanner.png");
            check(app.fields.All(item => !String.IsNullOrWhiteSpace(item.Value.ToolTip as string)
                && AutomationProperties.GetHelpText(item.Value) == (string)item.Value.ToolTip
                && app.rows[item.Key].ToolTip != null), "Every option has hover help on its control and label, with accessible help text");
            app.Set("d.output", root);
            app.Set("d.query", "https://open.spotify.com/track/test");
            app.Navigate("Download music");
            check(!app.Visible("d.search_type"), "Search type hidden for a link");
            check(!app.Visible("d.artist_albums"), "Artist option hidden for a track");
            ((ComboBox)app.fields["d.format"]).SelectedItem = "flac";
            check(!app.Visible("d.quality") && !app.Visible("d.genre") && !app.Visible("d.skip_cover_art"), "MP3 and bitrate controls hidden for FLAC");
            var download = app.BuildJob();
            check(!download.ContainsKey("quality") && !download.ContainsKey("repair_unknown"), "Download job excludes irrelevant options");
            ((CheckBox)app.fields["d.redownload"]).IsChecked = true;
            check(!app.Visible("d.verify_existing"), "Verification option hidden for replacement downloads");
            ((ComboBox)app.fields["a.mode"]).SelectedIndex = 0;
            check(!app.Visible("a.repair_card") && !app.Visible("a.repair_unknown"), "Repair options hidden in audit mode");
            ((ComboBox)app.fields["a.mode"]).SelectedIndex = 1;
            ((CheckBox)app.fields["a.full_verification"]).IsChecked = true;
            check(app.Visible("a.repair_unknown") && app.Visible("a.fpcalc"), "Fingerprint repair dependencies shown");
            ((ComboBox)app.fields["a.mode"]).SelectedIndex = 2;
            check(!app.Visible("a.directory") && !app.Visible("a.full_verification") && !app.Visible("a.repair_unknown") && app.Visible("a.nml"), "NML has only applicable controls");
            ((ComboBox)app.fields["t.mode"]).SelectedIndex = 1;
            check(app.Visible("t.manifest") && !app.Visible("t.repair_logs") && !app.Visible("t.log_encoding"), "Traktor restore hides cleanup-only inputs");
            app.SetBusy(true);
            check(!app.runButton.IsEnabled && app.navigation.IsEnabled && app.cancelButton.IsEnabled, "Navigation remains available while running");
            app.SetBusy(false);
            var layoutRoot = (FrameworkElement)app.window.Content;
            Action<double, double> layout = (width, height) => {
                layoutRoot.Measure(new Size(width, height));
                layoutRoot.Arrange(new Rect(0, 0, width, height));
                layoutRoot.UpdateLayout();
            };
            app.Navigate("Download music");
            layout(1920, 1040);
            var firstCard = (FrameworkElement)app.pages["Download music"].Children[0];
            var secondCard = (FrameworkElement)app.pages["Download music"].Children[1];
            Point first = firstCard.TranslatePoint(new Point(), layoutRoot);
            Point second = secondCard.TranslatePoint(new Point(), layoutRoot);
            check(second.X > first.X + firstCard.ActualWidth && Math.Abs(first.Y - second.Y) < 1,
                "Wide layouts place source and output cards side by side");
            layout(900, 640);
            first = firstCard.TranslatePoint(new Point(), layoutRoot);
            second = secondCard.TranslatePoint(new Point(), layoutRoot);
            check(Math.Abs(first.X - second.X) < 1 && second.Y >= first.Y + firstCard.ActualHeight,
                "Narrow layouts stack cards without overlap");
            foreach (string page in app.pageNames) {
                app.Navigate(page);
                foreach (Size size in new[] { new Size(760, 448), new Size(1366, 720), new Size(1920, 1040) }) {
                    layout(size.Width, size.Height);
                    var footer = app.Named<Button>("CancelButton");
                    Point bottom = footer.TranslatePoint(new Point(0, footer.ActualHeight), layoutRoot);
                    if (bottom.Y > size.Height + 1) throw new InvalidOperationException("Footer extends beyond the viewport: " + page + " at " + size + " (bottom " + bottom.Y + ", root " + layoutRoot.ActualHeight + ", rows " + String.Join(", ", ((Grid)layoutRoot).Children.OfType<Grid>().First().RowDefinitions.Select(row => row.ActualHeight)) + ")");
                }
            }
            check(app.pages.Count == 8, "All eight pages keep the footer visible across small and large screens");
            app.Navigate("Download music");
            var workspace = app.Named<Grid>("WorkspaceGrid");
            var actionBar = app.Named<Border>("ActionBar");
            var divider = app.Named<GridSplitter>("LogDivider");
            var logPane = app.Named<Border>("LogPane");
            layout(1366, 720);
            double actionHeight = actionBar.ActualHeight;
            foreach (Size size in new[] { new Size(760, 448), new Size(1366, 720) }) {
                foreach (double proportion in new[] { 0.0, 0.2, 3.0, 50.0 }) {
                    workspace.RowDefinitions[1].Height = new GridLength(proportion, GridUnitType.Star);
                    workspace.RowDefinitions[3].Height = new GridLength(1, GridUnitType.Star);
                    layout(size.Width, size.Height);
                    double barBottom = actionBar.TranslatePoint(new Point(0, actionBar.ActualHeight), layoutRoot).Y;
                    double dividerTop = divider.TranslatePoint(new Point(), layoutRoot).Y;
                    double logTop = logPane.TranslatePoint(new Point(), layoutRoot).Y;
                    Point buttonTop = app.runButton.TranslatePoint(new Point(), actionBar);
                    if (Math.Abs(actionBar.ActualHeight - actionHeight) > 1 || Math.Abs(barBottom - dividerTop) > 1
                        || Math.Abs(dividerTop + divider.ActualHeight - logTop) > 1
                        || buttonTop.Y < 0 || buttonTop.Y + app.runButton.ActualHeight > actionBar.ActualHeight + 1)
                        throw new InvalidOperationException("Console resizing displaced the action bar or clipped its button at " + size + " with proportion " + proportion);
                }
            }
            workspace.RowDefinitions[1].Height = new GridLength(3, GridUnitType.Star);
            workspace.RowDefinitions[3].Height = new GridLength(1, GridUnitType.Star);
            check(true, "Action bar stays fixed above the console with its button visible across resize extremes");
            foreach (string theme in ThemeCatalog.Names) foreach (bool darkMode in new[] { false, true }) {
                ThemeCatalog.Apply(app.window, theme, darkMode);
                app.Navigate("Download music");
                layout(1366, 720);
                var card = (Border)app.pages["Download music"].Children[0];
                var surface = ((SolidColorBrush)app.window.Resources["SurfaceBrush"]).Color;
                var input = ((SolidColorBrush)app.window.Resources["InputBrush"]).Color;
                var foreground = ((SolidColorBrush)app.window.Resources["TextBrush"]).Color;
                check(((SolidColorBrush)card.Background).Color == surface
                    && ((SolidColorBrush)app.fields["d.query"].Background).Color == input
                    && ((SolidColorBrush)app.fields["d.format"].Foreground).Color == foreground
                    && ((SolidColorBrush)app.fields["d.organize"].Foreground).Color == foreground
                    && ((SolidColorBrush)((Expander)app.pages["Download music"].Children[2]).Foreground).Color == foreground
                    && ((SolidColorBrush)app.console.Background).Color == ((SolidColorBrush)app.window.Resources["LogBrush"]).Color,
                    theme + (darkMode ? " dark" : " light") + " updates cards, inputs, dropdowns, checkbox/expander labels and log");
                app.Navigate("Settings & setup");
                layout(1366, 720);
                check(((SolidColorBrush)app.fields["cfg.client_secret"].Background).Color == input,
                    theme + (darkMode ? " dark" : " light") + " reaches pages added after the theme changes");
            }
            Console.WriteLine("Desktop smoke tests passed.");
        }
    }
}
