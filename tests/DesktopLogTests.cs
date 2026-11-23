using System;
using System.Text;
using Convenify.Desktop;

public static class DesktopLogTests
{
    private static void Check(bool condition, string label) {
        if (!condition) throw new Exception(label);
        Console.WriteLine("PASS " + label);
    }
    public static void Run() {
        var buffer = new ConsoleLogBuffer();
        bool replace;
        string raw = "[INFO]\tReady\r\n[DEBUG]\tmatching details\r\n    extra candidate detail\r\n"
            + "DEBUG:savify.audit:lookup\n2026-11-20 12:30:45,123 DEBUG: timed details\n"
            + "[debug] yt-dlp details\n[WARNING] review needed\n[ERROR] DEBUG lookup failed\n"
            + "DEBUGGER is a normal word\nA track called DEBUG\n";
        string visible = buffer.Append(raw, false, out replace);
        Check(!replace && visible.Contains("Ready") && visible.Contains("review needed") && visible.Contains("[ERROR] DEBUG")
            && visible.Contains("DEBUGGER") && visible.Contains("A track called DEBUG")
            && !visible.Contains("matching details") && !visible.Contains("extra candidate") && !visible.Contains("lookup\n")
            && !visible.Contains("timed details") && !visible.Contains("yt-dlp details"), "Filters DEBUG prefixes and their indented continuation lines without hiding warnings, errors or ordinary text");
        Check(buffer.Render(true) == raw && buffer.Render(false) == visible, "Toggling visibility retains the original received text and line endings");
        buffer.Clear();
        buffer.Append("[DE", false, out replace);
        visible = buffer.Append("BUG] split message\n[INFO] next\n", false, out replace);
        Check(replace && visible == "[INFO] next\n" && buffer.Render(true).Contains("[DEBUG] split message"), "A DEBUG prefix split across batches is reclassified correctly");
        buffer.Clear();
        var flood = new StringBuilder();
        for (int i = 0; i < 15000; i++) flood.AppendLine((i % 2 == 0 ? "[DEBUG] " : "[INFO] ") + "message " + i);
        visible = buffer.Append(flood.ToString(), false, out replace);
        Check(replace && buffer.Length <= 300000 && visible.Contains("message 14999") && !visible.Contains("[DEBUG]")
            && buffer.Render(true).Contains("message 14998"), "Heavy output stays bounded while keeping recent visible and hidden messages");
        buffer.Clear();
        buffer.Append("[DEBUG] " + new string('x', 400000), false, out replace);
        visible = buffer.Append("continued\n[INFO] normal output\n", false, out replace);
        Check(replace && buffer.Length <= 300000 && visible == "[INFO] normal output\n", "Oversized partial lines stay bounded and preserve their log level after trimming");
        buffer.Clear();
        Check(buffer.Length == 0 && buffer.Render(true).Length == 0 && buffer.Render(false).Length == 0, "Clear removes both visible and hidden buffered output");
    }
}
