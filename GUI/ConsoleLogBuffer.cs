using System;
using System.Collections.Generic;
using System.Text;
using System.Text.RegularExpressions;

namespace Convenify.Desktop
{
    // Keep a bounded, unfiltered display buffer. Switching DEBUG visibility can
    // restore recent lines without reading a growing log file on the UI thread.
    // ProcessRunner separately writes full output when run-file saving is on.
    internal sealed class ConsoleLogBuffer
    {
        private sealed class Line { public string Text; public bool Debug; public bool PrefixTrimmed; }
        private readonly LinkedList<Line> lines = new LinkedList<Line>();
        private Line partial;
        private bool previousDebug;
        public int Length { get; private set; }
        private static readonly Regex Level = new Regex(
            @"^\s*(?:\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s*(?:[-|]\s*)?)?(?:\[(?<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\]|(?<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)(?=\s|:))",
            RegexOptions.Compiled | RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

        private static bool IsDebug(string text, bool continuation)
        {
            var match = Level.Match(text);
            if (match.Success) return String.Equals(match.Groups["level"].Value, "DEBUG", StringComparison.OrdinalIgnoreCase);
            // Indented lines following a DEBUG record belong to that record.
            // Explicit INFO/WARNING/ERROR prefixes above always take priority.
            return continuation && (String.IsNullOrWhiteSpace(text) || text.StartsWith("    ") || text.StartsWith("\t"));
        }

        // Returns either the new visible text or the whole current view when
        // trimming/a partial line requires replacement. Normal batches append.
        public string Append(string text, bool showDebug, out bool replace)
        {
            replace = false;
            var added = new StringBuilder();
            int start = 0;
            while (start < text.Length) {
                int newline = text.IndexOf('\n', start);
                int end = newline < 0 ? text.Length : newline + 1;
                string part = text.Substring(start, end - start);
                Line line;
                if (partial != null) {
                    line = partial;
                    line.Text += part;
                    if (!line.PrefixTrimmed) line.Debug = IsDebug(line.Text, previousDebug);
                    replace = true;
                } else {
                    line = new Line { Text = part, Debug = IsDebug(part, previousDebug) };
                    lines.AddLast(line);
                    if (showDebug || !line.Debug) added.Append(part);
                }
                Length += part.Length;
                if (newline < 0) partial = line;
                else { previousDebug = line.Debug; partial = null; }
                start = end;
            }
            if (Length > 300000 || lines.Count > 12000) {
                while (lines.Count > 1 && (Length > 240000 || lines.Count > 10000)) {
                    Length -= lines.First.Value.Text.Length;
                    lines.RemoveFirst();
                }
                if (Length > 240000) {
                    var first = lines.First.Value;
                    first.Text = first.Text.Substring(first.Text.Length - 240000);
                    first.PrefixTrimmed = true;
                    Length = first.Text.Length;
                }
                replace = true;
            }
            return replace ? Render(showDebug) : added.ToString();
        }

        public string Render(bool showDebug)
        {
            var text = new StringBuilder(Length);
            foreach (Line line in lines) if (showDebug || !line.Debug) text.Append(line.Text);
            return text.ToString();
        }
        public void Clear() { lines.Clear(); partial = null; previousDebug = false; Length = 0; }
    }
}
