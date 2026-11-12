pragma Singleton
import QtQuick

QtObject {
    readonly property string mode: typeof uiAppearance !== "undefined" ? uiAppearance.mode : "dark"
    readonly property bool dark: mode === "dark"
    // The dark surfaces stay distinct without a near-black collection area.
    readonly property color chrome: dark ? "#30313a" : "#e8edf3"
    readonly property color sidebar: dark ? "#2a2b32" : "#f1f4f8"
    readonly property color content: dark ? "#24252b" : "#f9fbfd"
    readonly property color window: content
    readonly property color pane: sidebar
    readonly property color paneAlt: dark ? "#303139" : "#eaf0f6"
    readonly property color header: chrome
    readonly property color control: dark ? "#34353e" : "#ffffff"
    readonly property color hover: dark ? "#3b3c46" : "#e7edf4"
    readonly property color selected: dark ? "#3b4053" : "#dfe7f4"
    readonly property color border: dark ? "#42434d" : "#d9e0e9"
    readonly property color text: dark ? "#ececee" : "#273247"
    readonly property color muted: dark ? "#b4b5be" : "#68788c"
    readonly property color blue: dark ? "#8c9dfc" : "#5b75c8"
    readonly property color primaryFill: dark ? "#5c70bd" : "#536bb8"
    readonly property color green: dark ? "#79ce9c" : "#3b8b6b"
    readonly property color red: dark ? "#ed8991" : "#b95e67"
    readonly property color chartBackground: dark ? "#292c34" : "#ffffff"
    readonly property color chartGrid: dark ? "#373b45" : "#edf1f6"
    readonly property color chartAxis: dark ? "#777985" : "#b6c1cf"
    readonly property color chartDivider: dark ? "#36373f" : "#e3e9f0"
    readonly property color chartOverlay: dark ? "#f0303139" : "#f0ffffff"
    readonly property color chartWarning: dark ? "#e4b77d" : "#996014"
    readonly property string fontFamily: "Microsoft YaHei UI"
    readonly property string numberFont: "Consolas"

    // Presentation colors only; collected values and report colors stay exact.
    function seriesColor(key, original) {
        var colors = dark ? {
            fps: "#94a8e6", fps_1_low: "#adbbd9", fps_0_1_low: "#b8b2d6",
            frame_time: "#7fbfc6", app_cpu: "#b29ad7", total_cpu: "#91add0",
            app_user_cpu: "#c0a1cd", app_kernel_cpu: "#8eacc0",
            memory: "#92c1a5", ios_physical_footprint: "#92c1a5",
            private_memory: "#bbca8e", ios_private_memory: "#bbca8e",
            app_gpu_usage: "#d2b98c", gpu: "#d2b98c", gpu_memory: "#c2a693",
            network_send: "#90b6ca", network_recv: "#abc4ac"
        } : {
            fps: "#647fc4", fps_1_low: "#8095bc", fps_0_1_low: "#9285b6",
            frame_time: "#3f959f", app_cpu: "#8b74b4", total_cpu: "#668caa",
            app_user_cpu: "#a27daa", app_kernel_cpu: "#6c91a7",
            memory: "#579978", ios_physical_footprint: "#579978",
            private_memory: "#8a9f5f", ios_private_memory: "#8a9f5f",
            app_gpu_usage: "#b18c52", gpu: "#b18c52", gpu_memory: "#a2806b",
            network_send: "#5c91ab", network_recv: "#759e7c"
        }
        if (colors[key]) return colors[key]
        var hex = String(original || "")
        if (!/^#[0-9a-f]{6}$/i.test(hex)) return blue
        var r = parseInt(hex.slice(1, 3), 16) / 255
        var g = parseInt(hex.slice(3, 5), 16) / 255
        var b = parseInt(hex.slice(5, 7), 16) / 255
        var gray = (r + g + b) / 3
        r = r * .7 + gray * .3; g = g * .7 + gray * .3; b = b * .7 + gray * .3
        if (dark) { r = r * .8 + .18; g = g * .8 + .18; b = b * .8 + .18 }
        else { r *= .82; g *= .82; b *= .82 }
        return Qt.rgba(r, g, b, 1)
    }

    function setMode(value) {
        if (typeof uiAppearance !== "undefined") uiAppearance.setMode(value)
    }

    // Compatibility colors for existing panels; new UI uses semantic roles.
    function color(original) {
        var key = String(original).toLowerCase()
        if (["#151928", "#151929", "#171b2b", "#182032", "#1b2232", "#1c2232", "#1f2330"].indexOf(key) >= 0) return window
        if (["#222633", "#222838", "#24293a", "#242b3b", "#262d40", "#293248"].indexOf(key) >= 0) return header
        if (["#292d3a", "#2d313e", "#2c3447", "#263249", "#2b3348"].indexOf(key) >= 0) return pane
        if (["#32384c", "#303544", "#272c3a", "#202534", "#202839"].indexOf(key) >= 0) return control
        if (["#373d50", "#34394a", "#394154", "#343b50", "#35415a", "#353b4c", "#373c4b"].indexOf(key) >= 0) return hover
        if (["#2a2f40", "#263c66", "#284676"].indexOf(key) >= 0) return selected
        if (["#4169e1", "#4371fd", "#4593fd", "#3274e8", "#4779e6", "#5a7df0", "#7895ff", "#6b8fff"].indexOf(key) >= 0) return blue
        if (["#42485a", "#41485f", "#4a5368", "#52617d", "#596178", "#3a4051", "#4d6fcf", "#61718f", "#454d62", "#4d566d", "#555d70", "#444b5d"].indexOf(key) >= 0) return border
        if (key.length !== 7) return original
        var r = parseInt(key.slice(1, 3), 16), g = parseInt(key.slice(3, 5), 16), b = parseInt(key.slice(5, 7), 16)
        var spread = Math.max(r, g, b) - Math.min(r, g, b)
        if (spread > 75) return original
        var luminance = (r * .2126 + g * .7152 + b * .0722) / 255
        if (luminance > .73) return text
        if (luminance > .40) return muted
        if (luminance > .25) return border
        return paneAlt
    }
}
