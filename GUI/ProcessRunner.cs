using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace Convenify.Desktop
{
    // A Windows Job Object closes the whole worker / yt-dlp / FFmpeg tree, even
    // if the desktop crashes. The worker waits on stdin until it is assigned.
    internal sealed class ProcessJob : IDisposable
    {
        private IntPtr handle;
        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimits
        {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass, SchedulingClass;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters { public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount; }
        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedLimits
        {
            public BasicLimits BasicLimitInformation;
            public IoCounters IoInfo;
            public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, uint length);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateJobObject(IntPtr job, uint exitCode);
        [DllImport("kernel32.dll")]
        private static extern bool CloseHandle(IntPtr handle);

        public ProcessJob()
        {
            handle = CreateJobObject(IntPtr.Zero, null);
            if (handle == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
            var limits = new ExtendedLimits();
            limits.BasicLimitInformation.LimitFlags = 0x2000; // KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(handle, 9, ref limits, (uint)Marshal.SizeOf(limits)))
            {
                int error = Marshal.GetLastWin32Error();
                Dispose();
                throw new System.ComponentModel.Win32Exception(error);
            }
        }
        public void Assign(Process process)
        {
            if (!AssignProcessToJobObject(handle, process.Handle)) throw new System.ComponentModel.Win32Exception();
        }
        public void Stop()
        {
            if (handle != IntPtr.Zero && !TerminateJobObject(handle, 130))
                throw new System.ComponentModel.Win32Exception();
        }
        public void Dispose()
        {
            if (handle != IntPtr.Zero) { CloseHandle(handle); handle = IntPtr.Zero; }
        }
    }

    public sealed class RunnerMessage
    {
        public string Text;
        public int? ExitCode;
    }

    public sealed class ProcessRunner : IDisposable
    {
        private readonly ConcurrentQueue<RunnerMessage> messages = new ConcurrentQueue<RunnerMessage>();
        private readonly object gate = new object();
        private Process process;
        private ProcessJob job;
        private StreamWriter log;
        private string[] secrets = new string[0];
        private int pending;
        private int dropped;
        private bool logFailed;
        private static readonly Regex Ansi = new Regex(@"\x1B\[[0-?]*[ -/]*[@-~]", RegexOptions.Compiled);
        public bool Running { get; private set; }
        public bool Cancelled { get; private set; }
        public string LogPath { get; private set; }

        public static string Quote(string value)
        {
            // CommandLineToArgvW / Python CRT quoting, including trailing slashes.
            var result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\0') throw new ArgumentException("Paths cannot contain a null character.");
                if (c == '\\') { slashes++; continue; }
                result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
                result.Append(c);
                slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }

        public void Start(string root, string worker, string jobFile, string logPath)
        {
            lock (gate)
            {
                if (Running) throw new InvalidOperationException("Another task is already running.");
                var credentialDefinitions = AppDefaults.Load(root).credentials;
                RunnerMessage old;
                while (messages.TryDequeue(out old)) { }
                pending = dropped = 0;
                Cancelled = false;
                logFailed = false;
                LogPath = logPath;
                log = String.IsNullOrEmpty(logPath) ? null : new StreamWriter(logPath, false, new UTF8Encoding(false)) { AutoFlush = true };
                var info = new ProcessStartInfo(Path.Combine(root, "python", "python.exe"));
                info.Arguments = "-u " + Quote(worker) + " --handshake --job " + Quote(jobFile);
                info.WorkingDirectory = root;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = info.RedirectStandardError = info.RedirectStandardInput = true;
                info.StandardOutputEncoding = info.StandardErrorEncoding = Encoding.UTF8;
                info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                info.EnvironmentVariables["NO_COLOR"] = "1";
                info.EnvironmentVariables["CONVENIFY_GUI"] = "1";
                info.EnvironmentVariables["CONVENIFY_SAVE_RUN_FILES"] = log == null ? "0" : "1";
                info.EnvironmentVariables.Remove("CONVENIFY_RUN_DIR");
                if (log != null) info.EnvironmentVariables["CONVENIFY_RUN_DIR"] = Path.GetDirectoryName(logPath);
                string userPath = Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.User) ?? "";
                info.EnvironmentVariables["PATH"] = Path.Combine(root, "ffmpeg") + ";" + Environment.ExpandEnvironmentVariables(userPath) + ";" + Environment.GetEnvironmentVariable("PATH");
                var hidden = new List<string>();
                foreach (var definition in credentialDefinitions)
                {
                    string name = definition.environment;
                    string value = Environment.GetEnvironmentVariable(name, EnvironmentVariableTarget.User);
                    if (String.IsNullOrWhiteSpace(value)) value = Environment.GetEnvironmentVariable(name);
                    if (!String.IsNullOrWhiteSpace(value)) { info.EnvironmentVariables[name] = value; hidden.Add(value); }
                }
                secrets = hidden.ToArray();
                process = new Process { StartInfo = info };
                process.OutputDataReceived += OnOutput;
                process.ErrorDataReceived += OnOutput;
                try
                {
                    job = new ProcessJob();
                    if (!process.Start()) throw new IOException("Could not start the bundled Python.");
                    job.Assign(process);
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    Running = true;
                    process.StandardInput.WriteLine("START");
                    process.StandardInput.Flush();
                    process.StandardInput.Close();
                    var running = process;
                    Task.Run(() =>
                    {
                        int code = 2;
                        try { running.WaitForExit(); code = running.ExitCode; }
                        catch (Exception ex) { Publish("Runner error: " + ex.Message); }
                        lock (gate)
                        {
                            if (job != null) { job.Dispose(); job = null; }
                            if (log != null) {
                                try { log.Dispose(); }
                                catch (IOException) { messages.Enqueue(new RunnerMessage { Text = "WARNING: Could not finalize the session log. Check disk space." }); Interlocked.Increment(ref pending); }
                                log = null;
                            }
                            running.Dispose();
                            process = null;
                            Running = false;
                            messages.Enqueue(new RunnerMessage { ExitCode = Cancelled ? 130 : code });
                        }
                    });
                }
                catch
                {
                    try { if (process != null && !process.HasExited) process.Kill(); } catch { }
                    if (job != null) { job.Dispose(); job = null; }
                    if (process != null) { process.Dispose(); process = null; }
                    if (log != null) { log.Dispose(); log = null; }
                    Running = false;
                    throw;
                }
            }
        }

        private void OnOutput(object sender, DataReceivedEventArgs args)
        {
            if (args.Data != null) Publish(args.Data);
        }
        private void Publish(string text)
        {
            text = Ansi.Replace(text, "");
            foreach (string secret in secrets) text = text.Replace(secret, "[redacted]");
            lock (gate)
            {
                if (log != null && !logFailed)
                {
                    try { log.WriteLine(text); }
                    catch (IOException) { logFailed = true; text += "\nWARNING: The session log could not be written. Check disk space."; }
                }
            }
            // Preserve structured events. Bound UI memory even with very noisy tools.
            if (Volatile.Read(ref pending) < 2500 || text.StartsWith("@@CONVENIFY@@", StringComparison.Ordinal))
            {
                messages.Enqueue(new RunnerMessage { Text = text.Length > 24000 ? text.Substring(0, 24000) + " [display truncated]" : text });
                Interlocked.Increment(ref pending);
            }
            else Interlocked.Increment(ref dropped);
        }
        public bool TryRead(out RunnerMessage message)
        {
            int count = Interlocked.Exchange(ref dropped, 0);
            if (count > 0)
            {
                string detail = !String.IsNullOrEmpty(LogPath) && !logFailed
                    ? "the full output is in the session log"
                    : "these lines were not saved; enable Save run files before the next task to retain full output";
                message = new RunnerMessage { Text = String.Format("[Display skipped {0} lines to stay responsive; {1}.]", count, detail) };
                return true;
            }
            if (!messages.TryDequeue(out message)) return false;
            if (!message.ExitCode.HasValue) Interlocked.Decrement(ref pending);
            return true;
        }
        public void Cancel()
        {
            lock (gate)
            {
                if (!Running || job == null) return;
                job.Stop();
                Cancelled = true;
            }
        }
        public void Dispose()
        {
            Cancel();
            // Never wait for process teardown on the dispatcher thread.
        }
    }
}
