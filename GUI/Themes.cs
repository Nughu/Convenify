using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Media;

namespace Convenify.Desktop
{
    internal static class ThemeCatalog
    {
        public const string Default = "Default";
        public static readonly string[] Names = { Default, "Ocean", "Forest", "Plum" };
        // Each palette supplies all semantic brushes so controls, popups and
        // pages that are not currently visible change together.
        private static readonly string[] Keys = {
            "CanvasBrush", "SurfaceBrush", "InputBrush", "TextBrush", "MutedBrush", "BorderBrush",
            "SoftBrush", "SoftHoverBrush", "AccentBrush", "OnAccentBrush", "FocusBrush",
            "SelectionBrush", "SelectionTextBrush", "SidebarBrush", "SidebarTextBrush",
            "NavSelectedBrush", "NavSelectedTextBrush", "NavHoverBrush",
            "LogBrush", "LogTextBrush", "LogMutedBrush", "FooterBrush", "StripeBrush",
            "TooltipBrush", "TooltipTextBrush"
        };
        private sealed class Palette { public string[] Light, Dark; }
        private static readonly Dictionary<string, Palette> Palettes = new Dictionary<string, Palette> {
            { Default, new Palette { Light = new[] {
                "#FCF1FF", "#FFFFFF", "#FFFBFF", "#3C1647", "#785580", "#E4BFEA",
                "#F3D9FA", "#EAC1F5", "#AD14C7", "#FFFFFF", "#D91AA5",
                "#F4CDF9", "#6D147E", "#260C31", "#F1C7F9",
                "#742384", "#FFE0F9", "#491654",
                "#210B2A", "#F9DFFF", "#DAA6E4", "#F3DCFA", "#FDF6FF",
                "#4B1659", "#FFF1FC"
            }, Dark = new[] {
                "#1D1025", "#2C1637", "#21112B", "#FBE9FF", "#D6B1DF", "#684174",
                "#4A2459", "#613075", "#E84DFF", "#280531", "#FF70D7",
                "#702B82", "#FFF0FD", "#18091F", "#E7BCEE",
                "#742384", "#FFE0F9", "#361343",
                "#170C1E", "#F6DCFF", "#D3A4DF", "#281331", "#341C41",
                "#F9DEFF", "#350D41"
            } } },
            { "Ocean", new Palette { Light = new[] {
                "#F1F6FA", "#FFFFFF", "#FFFFFF", "#203346", "#526C80", "#C9D9E5",
                "#E0EDF6", "#CEDFED", "#176A9C", "#FFFFFF", "#007CA7",
                "#CFEAF8", "#164B6D", "#112C40", "#C5DDEB",
                "#1C4D68", "#A6E9FF", "#193C52",
                "#102737", "#D8EDF8", "#A8CADF", "#E3EDF5", "#F5F9FC",
                "#17394F", "#EFFAFF"
            }, Dark = new[] {
                "#101D29", "#192B3B", "#132330", "#E5F3FF", "#AAC5DC", "#3A5C76",
                "#26465D", "#305772", "#64C9FF", "#062538", "#7BE7F5",
                "#275B80", "#EBF8FF", "#0D1721", "#BBD5E8",
                "#234D68", "#AFEFFF", "#173347",
                "#0B1620", "#D6EDFF", "#9BBED9", "#172938", "#203346",
                "#DCEFFF", "#112B40"
            } } },
            { "Forest", new Palette { Light = new[] {
                "#F3F7F3", "#FFFFFF", "#FFFFFF", "#23382D", "#586F60", "#CEDCD2",
                "#E2EDE4", "#CEE1D4", "#28754D", "#FFFFFF", "#528032",
                "#D4EBDA", "#245437", "#182F25", "#D1E1D5",
                "#31563F", "#CEF0AB", "#274334",
                "#14281F", "#E0F0E1", "#B5D0BC", "#E4EEE5", "#F7FAF7",
                "#284936", "#F1FFF0"
            }, Dark = new[] {
                "#14221B", "#1F3026", "#17271E", "#E8F5E9", "#B1CCB7", "#42624C",
                "#304D3A", "#3F6049", "#8CD4A0", "#112F1C", "#C3E88D",
                "#365F42", "#EDFFEE", "#0D1B13", "#C6DDCD",
                "#2C5037", "#D2F5B1", "#203A2A",
                "#0D1912", "#DCF2DF", "#A9CCB1", "#1B2D22", "#263A2D",
                "#E1F1E0", "#163421"
            } } },
            { "Plum", new Palette { Light = new[] {
                "#F9F3F8", "#FFFCFF", "#FFFFFF", "#412A43", "#795F7C", "#DCCBDD",
                "#EEDFEF", "#E1CBE2", "#884C88", "#FFFFFF", "#A74780",
                "#E8CDE6", "#5E2F60", "#302033", "#E5D0E3",
                "#62405F", "#FFD5EF", "#48304A",
                "#261C2A", "#F2E0F1", "#CAB0CD", "#EEDFEF", "#FAF5FA",
                "#4F3352", "#FFF1FC"
            }, Dark = new[] {
                "#211924", "#302535", "#261D2B", "#F5E9F4", "#CDB7CF", "#67506B",
                "#4C3750", "#614465", "#D3A1CD", "#301631", "#F0A3C7",
                "#674869", "#FFF0FA", "#19131D", "#D8C0DA",
                "#573653", "#FFD2EA", "#38283B",
                "#18131D", "#EEDEEF", "#C6ADCA", "#2B2030", "#392B3E",
                "#EEDDED", "#3A223D"
            } } }
        };

        public static string Normalize(string name)
        {
            if (name == "Violet / Pink") return Default;
            if (name == "Midnight") return "Plum";
            return name != null && Palettes.ContainsKey(name) ? name : Default;
        }

        public static void Apply(Window window, string name, bool darkMode)
        {
            var palette = Palettes[Normalize(name)];
            string[] colors = darkMode ? palette.Dark : palette.Light;
            for (int i = 0; i < Keys.Length; i++) {
                var brush = (SolidColorBrush)new BrushConverter().ConvertFromString(colors[i]);
                brush.Freeze();
                window.Resources[Keys[i]] = brush;
            }
        }
    }
}
