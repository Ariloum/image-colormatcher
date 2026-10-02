// Image ColorMatcher — постоянная exe-обёртка (T-0042).
// Ничего не знает о коде программы: запускает python-embed\pythonw.exe -m colormatcher.gui
// из своей папки и передаёт аргументы (например, путь к .cmws). Пересобирать не нужно —
// обновления кода подхватываются сами. Сборка: tools\build_launcher.bat
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("Image ColorMatcher")]
[assembly: AssemblyProduct("Image ColorMatcher")]
[assembly: AssemblyDescription("Launcher: python-embed\\pythonw.exe -m colormatcher.gui")]
[assembly: AssemblyVersion("1.0.0.0")]

static class Launcher
{
    const string Title = "Image ColorMatcher";

    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "python-embed", "pythonw.exe");
        if (!File.Exists(python))
        {
            MessageBox.Show("Не найден встроенный Python:\n" + python +
                            "\n\nColorMatcher.exe должен лежать в корне проекта рядом с папкой python-embed.",
                            Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var arguments = new StringBuilder("-m colormatcher.gui");
        foreach (string a in args)
            arguments.Append(" \"").Append(a.Replace("\"", "\\\"")).Append('"');
        var psi = new ProcessStartInfo(python, arguments.ToString())
        {
            WorkingDirectory = root,
            UseShellExecute = false,
        };
        try
        {
            Process.Start(psi);
            return 0;
        }
        catch (Exception e)
        {
            MessageBox.Show("Не удалось запустить:\n" + e.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 2;
        }
    }
}
