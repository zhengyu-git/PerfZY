import QtQuick

ListView {
    id: view
    property var records: []
    property string scopeKey: ""
    property string identityRole: "id"
    property var shownRecords: []
    property string shownScope: ""
    property var pendingAnchor: null
    property bool resetPending: false
    property int updateSerial: 0
    model: []

    function rowIndex(identifier) {
        if (!identifier) return -1
        for (var i = 0; i < shownRecords.length; ++i)
            if (String(shownRecords[i][identityRole]) === identifier) return i
        return -1
    }
    function captureViewport() {
        if (!shownRecords.length || height <= 0) return null
        cancelFlick()
        var average = contentHeight / shownRecords.length
        var index = indexAt(Math.max(1, width / 2), contentY + 1)
        if (index < 0) index = Math.floor((contentY - originY) / Math.max(1, average))
        index = Math.max(0, Math.min(shownRecords.length - 1, index))
        var row = itemAtIndex(index)
        return { id: String(shownRecords[index][identityRole]), index: index,
            nextId: index + 1 < shownRecords.length ? String(shownRecords[index + 1][identityRole]) : "",
            previousId: index > 0 ? String(shownRecords[index - 1][identityRole]) : "",
            offset: row ? contentY - row.y : contentY - originY - index * average }
    }
    function restoreViewport(anchor) {
        forceLayout()
        if (!shownRecords.length || !anchor) { positionViewAtBeginning(); return }
        var index = rowIndex(anchor.id)
        if (index < 0) index = rowIndex(anchor.nextId)
        if (index < 0) index = rowIndex(anchor.previousId)
        if (index < 0) index = Math.min(anchor.index, shownRecords.length - 1)
        positionViewAtIndex(index, ListView.Beginning)
        forceLayout()
        var row = itemAtIndex(index)
        var desired = (row ? row.y : contentY) + anchor.offset
        contentY = Math.max(originY, Math.min(originY + Math.max(0, contentHeight - height), desired))
    }
    function scheduleViewportRestore(anchor) {
        var serial = ++updateSerial
        Qt.callLater(function() {
            if (serial !== view.updateSerial) return
            view.restoreViewport(anchor)
            view.shownScope = view.scopeKey
            view.pendingAnchor = null
            view.resetPending = false
        })
    }

    function scrollToTop() {
        cancelFlick()
        // Keep the reset through a model update in the same event turn.
        resetPending = true
        pendingAnchor = null
        scheduleViewportRestore(null)
    }

    onRecordsChanged: {
        var anchor = !resetPending && shownScope === scopeKey ? pendingAnchor || captureViewport() : null
        pendingAnchor = anchor
        shownScope = scopeKey
        shownRecords = records || []
        // Capture against the old model before ListView resets its viewport.
        model = shownRecords
        scheduleViewportRestore(anchor)
    }
    onScopeKeyChanged: scrollToTop()
}
