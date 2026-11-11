// Transparent setup bootstrap. The reviewed PowerShell files remain unchanged
// inside the embedded ZIP; no generated or obfuscated script is executed.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

[DataContract]
internal sealed class PayloadFile {
    [DataMember] public string name;
    [DataMember] public long size;
    [DataMember] public string sha256;
}
[DataContract]
internal sealed class PayloadManifest {
    [DataMember] public int schema;
    [DataMember] public string product;
    [DataMember] public string channel;
    [DataMember] public string setup_version;
    [DataMember] public string source_commit;
    [DataMember] public string application_resolution;
    [DataMember] public string archive_sha256;
    [DataMember] public PayloadFile[] files;
}

internal static class SetupBootstrap {
    private const long MaximumBytes = 256L * 1024 * 1024;
    private static string Hash(byte[] bytes) {
        using (var hash = SHA256.Create()) {
            return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }
    }
    private static byte[] Resource(string name) {
        using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream(name)) {
            if (source == null || source.Length > MaximumBytes) throw new InvalidDataException("The embedded setup resource is missing or too large.");
            using (var memory = new MemoryStream()) { source.CopyTo(memory); return memory.ToArray(); }
        }
    }
    private static PayloadManifest ReadManifest(byte[] bytes) {
        if (Hash(bytes) != BuildInfo.ManifestSha256) throw new InvalidDataException("The setup manifest integrity check failed. Download setup again from the official release.");
        PayloadManifest manifest;
        using (var stream = new MemoryStream(bytes)) {
            manifest = (PayloadManifest)new DataContractJsonSerializer(typeof(PayloadManifest)).ReadObject(stream);
        }
        if (manifest == null || manifest.schema != 1 || manifest.product != BuildInfo.Product
                || manifest.channel != BuildInfo.Channel || manifest.setup_version != BuildInfo.SetupVersion
                || manifest.source_commit != BuildInfo.SourceCommit || manifest.files == null
                || manifest.files.Length < 8 || manifest.files.Length > 32)
            throw new InvalidDataException("The setup manifest does not match this executable.");
        string resolution = BuildInfo.Channel == "dev" ? "embedded-development-wheel" : "verified-current-release";
        if (manifest.application_resolution != resolution) throw new InvalidDataException("The setup application channel does not match.");
        return manifest;
    }
    private static void CheckAncestors(string path) {
        for (DirectoryInfo directory = new DirectoryInfo(Path.GetFullPath(path)); directory != null; directory = directory.Parent) {
            if (directory.Exists && (directory.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Setup cannot use a redirected extraction folder. Choose a regular local folder.");
        }
    }
    private static void CreatePrivateDirectory(string path) {
        path = Path.GetFullPath(path);
        if (File.Exists(path) || Directory.Exists(path)) throw new IOException("The extraction destination already exists; setup will not overwrite it.");
        string parent = Path.GetDirectoryName(path);
        if (String.IsNullOrEmpty(parent) || !Directory.Exists(parent)) throw new IOException("The extraction parent folder does not exist.");
        CheckAncestors(parent);
        SecurityIdentifier user = WindowsIdentity.GetCurrent().User;
        var security = new DirectorySecurity();
        security.SetAccessRuleProtection(true, false);
        security.SetOwner(user);
        security.AddAccessRule(new FileSystemAccessRule(user, FileSystemRights.FullControl,
            InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
        Directory.CreateDirectory(path, security);
        CheckAncestors(path);
    }
    private static void Extract(byte[] payload, PayloadManifest manifest, string destination) {
        if (Hash(payload) != manifest.archive_sha256) throw new InvalidDataException("The embedded setup payload integrity check failed. Download setup again from the official release.");
        var expected = new Dictionary<string, PayloadFile>(StringComparer.OrdinalIgnoreCase);
        long total = 0;
        foreach (PayloadFile item in manifest.files) {
            if (item == null || !Regex.IsMatch(item.name ?? "", @"\A[A-Za-z0-9][A-Za-z0-9._-]{0,180}\z")
                    || item.name.EndsWith(".", StringComparison.Ordinal) || Regex.IsMatch(item.name, @"\A(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", RegexOptions.IgnoreCase)
                    || !Regex.IsMatch(item.sha256 ?? "", @"\A[0-9a-f]{64}\z")
                    || item.size < 0 || item.size > MaximumBytes || expected.ContainsKey(item.name))
                throw new InvalidDataException("The setup file manifest is invalid.");
            total += item.size;
            if (total > MaximumBytes) throw new InvalidDataException("The extracted setup exceeds the supported size.");
            expected.Add(item.name, item);
        }
        foreach (string required in new string[] { BuildInfo.Product + ".ps1", BuildInfo.Product + ".vbs", BuildInfo.Product + ".cmd", BuildInfo.Product + "-Setup.cmd", "brand.ico", "brand.png", "install_bactologix.py", "README.txt" }) {
            if (!expected.ContainsKey(required)) throw new InvalidDataException("The setup is missing a required file.");
        }
        // Validate every name, count and size before writing any payload file.
        using (var memory = new MemoryStream(payload))
        using (var archive = new ZipArchive(memory, ZipArchiveMode.Read)) {
            if (archive.Entries.Count != expected.Count) throw new InvalidDataException("Unexpected setup archive contents.");
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string prefix = BuildInfo.Product + "/";
            foreach (ZipArchiveEntry entry in archive.Entries) {
                string name = entry.FullName.StartsWith(prefix, StringComparison.Ordinal) ? entry.FullName.Substring(prefix.Length) : "";
                PayloadFile item;
                if (!expected.TryGetValue(name, out item) || !seen.Add(name) || entry.Length != item.size)
                    throw new InvalidDataException("The archive contains an unexpected, duplicate or changed file.");
            }
            CreatePrivateDirectory(destination);
            foreach (ZipArchiveEntry entry in archive.Entries) {
                string name = entry.FullName.Substring(prefix.Length);
                PayloadFile item = expected[name];
                byte[] bytes;
                using (Stream input = entry.Open())
                using (var buffer = new MemoryStream()) { input.CopyTo(buffer); bytes = buffer.ToArray(); }
                if (bytes.LongLength != item.size || Hash(bytes) != item.sha256)
                    throw new InvalidDataException("The setup file integrity check failed: " + name);
                CheckAncestors(destination);
                string target = Path.Combine(destination, name);
                using (var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { output.Write(bytes, 0, bytes.Length); }
            }
        }
    }
    private static string QuoteArgument(string value) {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value) {
            if (character == '\\') { slashes++; continue; }
            if (character == '"') result.Append('\\', 2 * slashes + 1).Append('"');
            else result.Append('\\', slashes).Append(character);
            slashes = 0;
        }
        return result.Append('\\', 2 * slashes).Append('"').ToString();
    }
    private static ProcessStartInfo SetupCommand(string folder) {
        string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        string system = Environment.Is64BitOperatingSystem && !Environment.Is64BitProcess ? "Sysnative" : "System32";
        string shell = Path.Combine(windows, system, @"WindowsPowerShell\v1.0\powershell.exe");
        if (!File.Exists(shell)) throw new FileNotFoundException("Windows PowerShell is unavailable. Repair the Windows component before continuing.");
        var info = new ProcessStartInfo(shell);
        info.Arguments = "-NoLogo -NoProfile -NonInteractive -STA -ExecutionPolicy Bypass -File "
            + QuoteArgument(Path.Combine(folder, BuildInfo.Product + ".ps1")) + " -Setup";
        info.WorkingDirectory = folder;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        return info;
    }
    private static void CleanupOwnedFiles(string folder, PayloadManifest manifest) {
        // Remove only unchanged files we extracted. Never recursively remove a
        // directory, follow a redirect or touch copied application/user data.
        try {
            CheckAncestors(folder);
            foreach (PayloadFile item in manifest.files) {
                string path = Path.Combine(folder, item.name);
                if (File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0
                        && Hash(File.ReadAllBytes(path)) == item.sha256) File.Delete(path);
            }
            Directory.Delete(folder, false);
        } catch { /* Retain a partial staging folder if ownership is uncertain. */ }
    }
    [STAThread]
    private static int Main(string[] args) {
        bool verify = args.Length > 0 && args[0] == "--verify-payload";
        string staging = null;
        PayloadManifest manifest = null;
        try {
            if (args.Length != 0 && !(verify && args.Length == 2))
                throw new ArgumentException("Open setup normally to install. Diagnostic usage: --verify-payload <new-folder> extracts and verifies only; it does not install or start Bactologix.");
            byte[] manifestBytes = Resource("Bactologix.Setup.Manifest");
            manifest = ReadManifest(manifestBytes);
            byte[] payload = Resource("Bactologix.Setup.Payload");
            if (verify) {
                string destination = Path.GetFullPath(args[1]);
                Extract(payload, manifest, destination);
                File.WriteAllBytes(Path.Combine(destination, "verified-payload.json"), manifestBytes);
                Console.WriteLine("Verified " + BuildInfo.Product + " setup " + BuildInfo.SetupVersion + "; no installation started.");
                return 0;
            }
            Application.EnableVisualStyles();
            if (!Environment.Is64BitOperatingSystem) throw new PlatformNotSupportedException("Bactologix setup requires 64-bit Windows with WSL 2 support.");
            var identity = WindowsIdentity.GetCurrent();
            if (new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator))
                throw new InvalidOperationException("Close this window and open setup normally by double-clicking it. Do not use Run as administrator. Ubuntu and Bactologix must be installed for your own Windows account. Setup requests administrator permission separately when Windows components need it.");
            using (var setupLock = new Mutex(false, @"Local\" + BuildInfo.Product + ".Setup." + identity.User.Value)) {
                bool acquired;
                try { acquired = setupLock.WaitOne(0, false); } catch (AbandonedMutexException) { acquired = true; }
                if (!acquired) {
                    MessageBox.Show("Setup is already open for this Windows account. Return to that window or finish it before opening another installer.", BuildInfo.Product + " Setup", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 1618;
                }
                try {
                    string cache = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), BuildInfo.Product, "InstallerCache");
                    CheckAncestors(cache);
                    Directory.CreateDirectory(cache);
                    CheckAncestors(cache);
                    staging = Path.Combine(cache, Guid.NewGuid().ToString("N"));
                    Extract(payload, manifest, staging);
                    using (Process child = Process.Start(SetupCommand(staging))) {
                        child.WaitForExit();
                        return child.ExitCode;
                    }
                } finally { setupLock.ReleaseMutex(); }
            }
        } catch (Exception error) {
            if (verify) Console.Error.WriteLine(error.Message);
            else {
                Application.EnableVisualStyles();
                MessageBox.Show(error.Message, BuildInfo.Product + " Setup", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
            return 1;
        } finally {
            if (!verify && staging != null && manifest != null && Directory.Exists(staging)) CleanupOwnedFiles(staging, manifest);
        }
    }
}
