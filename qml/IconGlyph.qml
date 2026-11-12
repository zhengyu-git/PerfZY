import QtQuick

Image {
    id: glyph
    property string name: "chart"
    property color tint: Theme.text
    sourceSize.width: 24
    sourceSize.height: 24
    fillMode: Image.PreserveAspectFit
    smooth: true
    readonly property var paths: ({
        "sliders": '<path d="M3 6h5m4 0h9M3 12h11m4 0h3M3 18h3m4 0h11"/><circle cx="10" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="8" cy="18" r="2"/>',
        "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
        "refresh": '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
        "more": '<path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3"/>',
        "rename": '<path d="m14 5 5 5M4 20l5-1L20 8a2 2 0 0 0-5-5L4 14z"/>',
        "bookmark": '<path d="M6 4h12v17l-6-4-6 4z"/>',
        "compare": '<path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4"/>',
        "upload": '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
        "trash": '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
        "chevronUp": '<path d="m6 15 6-6 6 6"/>',
        "chevronDown": '<path d="m6 9 6 6 6-6"/>',
        "check": '<path d="m5 12 4 4L19 6"/>',
        "chart": '<path d="M3 13h4l3-8 4 14 3-8h4"/>',
        "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.2"/>',
        "history": '<path d="M3 7h7l2 2h9v11H3z"/><path d="M3 7V4h7l2 3"/>',
        "view": '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="2.5"/>',
        "log": '<path d="M4 6h16M4 12h16M4 18h10"/>',
        "settings": '<path d="m10 3-.6 2.3-2 .9-2.1-.7-2 3.5 1.7 1.7v2.4L3.3 15l2 3.5 2.1-.7 2 .9L10 21h4l.6-2.3 2-.9 2.1.7 2-3.5-1.7-1.7v-2.4L20.7 9l-2-3.5-2.1.7-2-.9L14 3z"/><circle cx="12" cy="12" r="3"/>',
        "collapse": '<path d="m14 6-6 6 6 6"/>',
        "expand": '<path d="m10 6 6 6-6 6"/>',
        "close": '<path d="m6 6 12 12M18 6 6 18"/>',
        "minimize": '<path d="M5 12h14"/>',
        "maximize": '<rect x="5" y="5" width="14" height="14" rx="1"/>',
        "restore": '<path d="M9 5h10v10M5 9h10v10H5z"/>',
        "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
        "moon": '<path d="M20 14a8 8 0 0 1-10-10 8 8 0 1 0 10 10z"/>',
        "capture": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="4"/>',
        "report": '<path d="M6 3h9l4 4v14H6zM9 11h7m-7 4h7"/>',
        "connection": '<path d="m8 5 11 11M5 8 16 19M7 3l14 14M3 7l14 14M9 13l-6 6m8-4-6 6"/>'
    })
    source: "data:image/svg+xml;utf8," + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="'
        + tint + '" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        + (paths[name] || paths.chart) + '</svg>')
}
