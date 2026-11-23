using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows;

namespace Convenify.Desktop
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            try {
                string identity;
                using (var hash = SHA256.Create()) identity = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Replace("-", "");
                bool created;
                using (var instance = new Mutex(true, "Local\\Convenify-" + identity, out created)) {
                    if (!created) { MessageBox.Show("Convenify is already open for this folder.", "Convenify", MessageBoxButton.OK, MessageBoxImage.Information); return; }
                    try { DesktopApp.Run(root); }
                    finally { instance.ReleaseMutex(); }
                }
            }
            catch (Exception ex) {
                MessageBox.Show("Convenify could not start.\n\n" + ex.Message + "\n\nKeep Convenify.exe beside the GUI, python and savify-new folders.", "Convenify", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }
    }
}
