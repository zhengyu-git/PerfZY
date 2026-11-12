import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property var controller: appController
    objectName: "chart:" + chartKey
    required property string chartTitle
    property string chartKey: ""
    property bool canMoveUp: false
    property bool canMoveDown: false
    property bool renderActive: true
    property bool selectionMenuEnabled: false
    signal selectionMenuRequested(real x, real y)
    readonly property var excludedIntervals: root.controller.excludedIntervals || []
    readonly property var timelineCuts: root.controller.timelineCuts || []
    function crossesExcluded(first, last) {
        for (var i=0;i<excludedIntervals.length;i++)
            if (first < excludedIntervals[i].end && last >= excludedIntervals[i].start) return true
        for (var j=0;j<timelineCuts.length;j++)
            if (first < timelineCuts[j].time && last >= timelineCuts[j].time) return true
        return false
    }
    onTimelineCutsChanged: if (renderActive) plot.requestPaint()
    required property var seriesNames
    required property var seriesColors
    readonly property var displayColors: metricKeys.map(function(key, index) { return Theme.seriesColor(key, seriesColors[index]) })
    function colorForTitle(title, original) {
        var index = seriesNames.indexOf(title)
        return index >= 0 ? displayColors[index] : Theme.seriesColor("", original)
    }
    property var seriesEnabled: []
    property var seriesSelectable: []
    property var seriesStatus: []
    property var seriesReasons: []
    property var metricKeys: []
    property var dataSource: ({})
    property var renderedData: ({})
    property var latestValues: ({})
    property bool activeMarking: false
    property real markingStart: 0
    property real dragStartX: -1
    property real dragCurrentX: -1
    property bool dragging: false
    property var stats: []
    property var lockedStats: []
    property bool hoverPreviewVisible: false
    property real hoverPreviewX: 0
    property real hoverPreviewTime: 0
    property var hoverPreviewRows: []
    property real axisMaximum: 10
    // Read the active slider directly so its chart follows the pointer in the
    // same frame. The controller remains the shared source used to synchronize
    // every other chart.
    readonly property real viewStartRatio: slider.first.value
    readonly property real viewEndRatio: slider.second.value
    property bool synchronizingRange: false
    property real fullDuration: timelineDuration()
    property real viewStartTime: viewStartRatio * fullDuration
    property real viewEndTime: viewEndRatio * fullDuration
    property real viewDuration: Math.max(.001, viewEndTime - viewStartTime)
    property string unitOverride: ""
    property string axisUnit: unitOverride ? unitOverride : chartKey === "fps" ? "FPS"
                              : chartKey === "frame" ? "ms"
                              : chartKey === "frame_latency" ? "ms"
                              : chartKey === "frame_quality" ? "% / count"
                              : chartKey === "frame_pipeline" ? "ms"
                              : chartKey === "cpu" ? "%"
                              : chartKey === "cpu_core_usage" ? "%"
                              : chartKey === "cpu_core_clock" ? "MHz"
                              : chartKey === "cpu_health" ? "% / count"
                              : chartKey === "memory" ? "MB"
                              : chartKey === "memory_pressure" ? "faults/s"
                              : chartKey === "gpu" ? "%"
                              : chartKey === "gpu_frame" ? "ms"
                              : chartKey === "gpu_memory" ? "MB"
                              : chartKey === "gpu_clock" ? "MHz"
                              : chartKey === "gpu_busy" ? "ms"
                              : chartKey === "thermal" ? "°C"
                              : chartKey === "power" ? "W"
                              : chartKey === "network" ? "KB/s"
                              : chartKey === "io" ? "KB/s"
                              : chartKey === "io_ops" ? "ops/s"
                              : chartKey === "process" ? "count" : ""
    property bool chartHovered: chartHover.hovered
    signal pageScrollRequested(real delta)
    Connections {
        target: Theme
        function onModeChanged() {
            if (root.renderActive) plot.requestPaint()
        }
    }
    Timer {
        id: rangeRefreshTimer
        interval: 60
        repeat: false
        onTriggered: {
            if (!root.renderActive) return
            root.refreshAxisMaximum()
            if (root.controller.hasSelection || root.controller.hasTimeLock)
                root.refreshStats(false)
        }
    }

    function resetRenderedData() {
        var next = ({}), latest = ({})
        for (var i = 0; i < metricKeys.length; i++) {
            var key = metricKeys[i]
            var sourceRows = dataSource[key] || []
            // Live charts own mutable arrays for incremental append/trim.
            // Saved sessions are immutable, so sharing their converted rows
            // avoids another full copy of every long historical curve.
            next[key] = root.controller.collecting ? sourceRows.slice() : sourceRows
            if (next[key].length)
                latest[key] = Number(next[key][next[key].length - 1].y)
        }
        renderedData = next
        latestValues = latest
        if (renderActive) plot.requestPaint()
    }
    onDataSourceChanged: resetRenderedData()
    onMetricKeysChanged: resetRenderedData()

    function enabledMetricKeys() {
        var keys = []
        for (var i = 0; i < metricKeys.length; i++)
            if (seriesEnabled.length <= i || seriesEnabled[i]) keys.push(metricKeys[i])
        return keys
    }
    function lowerBound(rows, target) {
        var low = 0, high = rows.length
        while (low < high) {
            var middle = (low + high) >> 1
            if (Number(rows[middle].x) < target) low = middle + 1
            else high = middle
        }
        return low
    }
    function nearestIndex(rows, target) {
        if (root.controller.isExcludedTime(target)) return -1
        if (!rows.length) return -1
        var right = lowerBound(rows, target)
        var best=-1, distance=Infinity
        for (var index=Math.max(0,right-1);index<=Math.min(rows.length-1,right);index++) {
            var stamp=Number(rows[index].x), delta=Math.abs(stamp-target)
            if (!crossesExcluded(Math.min(stamp,target),Math.max(stamp,target)) && delta<distance) {best=index;distance=delta}
        }
        return best
    }
    function refreshStats(updateAxis) {
        var keys = enabledMetricKeys()
        var nextStats = root.controller.selectionStats(keys)
        var nextLocked = root.controller.lockedValues(keys)
        // QVariantList conversion can occasionally arrive empty while a live
        // Canvas is processing input. Calculate from the chart's own immutable
        // snapshot as a fallback so the two information panels never vanish.
        stats = nextStats && nextStats.length ? nextStats : localSelectionStats(keys)
        lockedStats = nextLocked && nextLocked.length ? nextLocked : localLockedValues(keys)
        if (hoverPreviewVisible) updateHoverPreview(hoverPreviewX)
        if (updateAxis === false) plot.requestPaint()
        else refreshAxisMaximum()
    }
    function localSelectionStats(keys) {
        if (!root.controller.hasSelection) return []
        var duration = Math.max(.001, Number(root.controller.duration))
        var start = Number(root.controller.selectionStartRatio) * duration
        var end = Number(root.controller.selectionEndRatio) * duration
        var result = []
        for (var i = 0; i < keys.length; i++) {
            var metricIndex = metricKeys.indexOf(keys[i])
            var rows = renderedData[keys[i]] || []
            var first = lowerBound(rows, start), last = lowerBound(rows, end)
            var count = 0, total = 0, minimum = Infinity, maximum = -Infinity
            for (var r = first; r < rows.length && r <= last; r++) {
                var x = Number(rows[r].x), value = Number(rows[r].y)
                if (x < start || x > end || !isFinite(value)) continue
                count++; total += value; minimum = Math.min(minimum, value); maximum = Math.max(maximum, value)
            }
            if (count) result.push({
                "title": seriesNames[metricIndex] || keys[i],
                "color": displayColors[metricIndex] || Theme.text,
                "avg": (total / count).toFixed(2),
                "min": minimum.toFixed(2), "max": maximum.toFixed(2)
            })
        }
        return result
    }
    function localLockedValues(keys) {
        if (!root.controller.hasTimeLock) return []
        var result = [], time = Number(root.controller.lockedTime)
        for (var i = 0; i < keys.length; i++) {
            var metricIndex = metricKeys.indexOf(keys[i])
            var rows = renderedData[keys[i]] || []
            var index = nearestIndex(rows, time)
            if (index < 0) continue
            result.push({
                "title": seriesNames[metricIndex] || keys[i],
                "color": displayColors[metricIndex] || Theme.text,
                "value": Number(rows[index].y).toFixed(2), "unit": axisUnit
            })
        }
        return result
    }
    onSeriesEnabledChanged: refreshStats()
    function calculateAxisMaximum() {
        var largest = 0
        for (var i = 0; i < metricKeys.length; i++) {
            if (seriesEnabled.length > i && !seriesEnabled[i]) continue
            var rows = renderedData[metricKeys[i]] || []
            var first = lowerBound(rows, viewStartTime)
            var last = Math.min(rows.length, lowerBound(rows, viewEndTime) + 1)
            for (var r = first; r < last; r++) {
                var time = Number(rows[r].x)
                if (time < viewStartTime || time > viewEndTime) continue
                largest = Math.max(largest, Number(rows[r].y) || 0)
            }
        }
        return paddedAxisMaximum(largest)
    }
    function paddedAxisMaximum(largest) {
        if (largest <= 0) return 10
        var padded = largest * 1.10
        var roughStep = padded / 6
        var magnitude = Math.pow(10, Math.floor(Math.log(roughStep) / Math.LN10))
        var normalized = roughStep / magnitude
        var niceStep = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude
        return Math.max(niceStep, Math.ceil(padded / niceStep) * niceStep)
    }
    function acceptLiveBatch(batch) {
        var curveChanged = false, legendChanged = false, largest = 0, nextLatest = ({})
        for (var existingKey in latestValues) nextLatest[existingKey] = latestValues[existingKey]
        var missingKeys = batch["__missingKeys"] || []
        var missing = ({})
        for (var missingIndex = 0; missingIndex < missingKeys.length; missingIndex++)
            missing[String(missingKeys[missingIndex])] = true
        var trimBefore = Number(batch["__trimBefore"])
        var maxPoints = Number(batch["__maxPoints"])
        var shouldTrim = isFinite(trimBefore) && isFinite(maxPoints) && maxPoints > 0
        for (var i = 0; i < metricKeys.length; i++) {
            var key = metricKeys[i], point = batch[key]
            var rows = renderedData[key] || []
            if (point !== undefined && point !== null) {
                rows.push(point)
                nextLatest[key] = Number(point.y)
                if (seriesEnabled.length <= i || seriesEnabled[i])
                    largest = Math.max(largest, Number(point.y) || 0)
                curveChanged = true
                legendChanged = true
            } else if (missing[key] && nextLatest[key] !== null) {
                nextLatest[key] = null
                legendChanged = true
            }
            if (shouldTrim && rows.length) {
                var remove = Math.max(lowerBound(rows, trimBefore), rows.length - maxPoints)
                if (remove > 0) {
                    rows.splice(0, remove)
                    curveChanged = true
                    legendChanged = true
                    if (rows.length) nextLatest[key] = Number(rows[rows.length - 1].y)
                    else nextLatest[key] = null
                }
            }
        }
        if (!curveChanged && !legendChanged) return
        // Curves are explicitly repainted; only replace the tiny latest-value
        // map needed by Text bindings. Copying every historical array here was
        // O(points * metrics) on every sample and made long captures degrade.
        latestValues = nextLatest
        if (!curveChanged) return
        if (!renderActive) return
        if (shouldTrim) {
            refreshLiveOverlays(true)
        } else if (viewStartRatio <= .000001 && viewEndRatio >= .999999) {
            axisMaximum = Math.max(axisMaximum, paddedAxisMaximum(largest))
            refreshLiveOverlays(false)
        } else refreshLiveOverlays(true)
    }
    function refreshLiveOverlays(updateAxis) {
        // During ordinary collection there is no selected range or locked
        // cursor. Avoid two Python round-trips per chart per sample in that
        // common path; with ten charts that used to mean 20 needless calls/s.
        if (root.controller.hasSelection || root.controller.hasTimeLock) {
            refreshStats(updateAxis)
            return
        }
        if (stats.length) stats = []
        if (lockedStats.length) lockedStats = []
        if (hoverPreviewVisible) updateHoverPreview(hoverPreviewX)
        if (updateAxis) refreshAxisMaximum()
        else plot.requestPaint()
    }
    function refreshAxisMaximum() {
        axisMaximum = calculateAxisMaximum()
        plot.requestPaint()
    }
    function syncSharedRange() {
        synchronizingRange = true
        slider.first.value = root.controller.chartViewStartRatio
        slider.second.value = root.controller.chartViewEndRatio
        synchronizingRange = false
        // Range dragging is infrequent compared with live sampling and must
        // update every chart immediately. The viewport visibility heuristic
        // can lag one layout frame inside ScrollView, leaving visible charts
        // with stale curves and overlays if it is used here.
        if (renderActive) {
            // Move every curve in the same frame, then debounce the expensive
            // y-axis/statistics scans while a slider handle is moving.
            plot.requestPaint()
            rangeRefreshTimer.restart()
        }
    }
    function timeText(seconds) {
        var m = Math.floor(seconds / 60)
        var s = seconds - m * 60
        return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s.toFixed(2)
    }
    function timelineDuration() {
        var duration = Number(root.controller.chartDuration) || 0
        if (duration <= 0) return 5
        return Math.max(5, duration)
    }
    // The duration changes independently from nested chart data. Always
    // repaint so a chart never waits for mouse movement to reveal its curve.
    onFullDurationChanged: { if (renderActive) plot.requestPaint() }
    onRenderActiveChanged: {
        if (renderActive) {
            refreshAxisMaximum()
            refreshStats(false)
        }
    }
    function niceTimeStep(seconds) {
        var rough = Math.max(.001, seconds / 10)
        var magnitude = Math.pow(10, Math.floor(Math.log(rough) / Math.LN10))
        var normalized = rough / magnitude
        return (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude
    }
    function timeTicks() {
        var step = niceTimeStep(viewDuration)
        var first = Math.ceil(viewStartTime / step - 0.000001) * step
        var ticks = []
        for (var value = first; value <= viewEndTime + step * .001 && ticks.length < 20; value += step)
            ticks.push(value)
        return ticks
    }
    function axisTimeText(seconds) {
        seconds = Math.max(0, seconds)
        if (seconds < 60) {
            if (viewDuration <= 10 && Math.abs(seconds - Math.round(seconds)) > .001)
                return seconds.toFixed(1) + "s"
            return "0:" + (seconds < 10 ? "0" : "") + Math.round(seconds)
        }
        var minutes = Math.floor(seconds / 60)
        var remain = Math.round(seconds - minutes * 60)
        if (remain === 60) { minutes += 1; remain = 0 }
        return minutes + ":" + (remain < 10 ? "0" : "") + remain
    }
    function updateHoverPreview(mouseX) {
        hoverPreviewX = Math.max(0, Math.min(plot.width, mouseX))
        hoverPreviewTime = viewStartTime + hoverPreviewX / Math.max(1, plot.width) * viewDuration
        var result = []
        for (var cut=0;cut<timelineCuts.length;cut++) {
            if (Math.abs(hoverPreviewTime-timelineCuts[cut].time) <= 4*viewDuration/Math.max(1,plot.width)) {
                result.push({"name":"剪辑交界","value":"前后已拼接","color":Theme.muted})
                break
            }
        }
        for (var i = 0; i < metricKeys.length; i++) {
            if (seriesEnabled.length > i && !seriesEnabled[i]) continue
            var rows = renderedData[metricKeys[i]] || []
            if (!rows.length) continue
            var nearest = rows[nearestIndex(rows, hoverPreviewTime)]
            if (!nearest) continue
            result.push({"name": seriesNames[i] || metricKeys[i], "value": Number(nearest.y).toFixed(2), "color": displayColors[i]})
        }
        hoverPreviewRows = result
        hoverPreviewVisible = result.length > 0
    }
    height: 224
    radius: 12
    antialiasing: true
    color: Theme.chartBackground
    border.color: Theme.border
    HoverHandler { id: chartHover }
    Text {
        id: chartHeading
        anchors.left: parent.left; anchors.top: parent.top; anchors.leftMargin: 18; anchors.topMargin: 12
        width: Math.min(implicitWidth, parent.width - 100)
        text: root.chartTitle; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideRight
    }
    Item {
        id: yAxis
        anchors.left: parent.left; anchors.top: plot.top; anchors.bottom: plot.bottom
        anchors.leftMargin: 14; width: 40
        Repeater {
            model: 7
            Text { required property int index; width: yAxis.width; y: index * (yAxis.height - height) / 6; text: Number(root.axisMaximum * (6 - index) / 6).toFixed(0); color: Theme.muted; font.pixelSize: 10; font.weight: Font.Medium; horizontalAlignment: Text.AlignRight }
        }
    }
    Text {
        visible: root.axisUnit.length > 0 && root.axisUnit.toLowerCase() !== root.chartTitle.toLowerCase()
        anchors.left: chartHeading.right; anchors.leftMargin: 8; anchors.verticalCenter: chartHeading.verticalCenter
        text: root.axisUnit; color: Theme.muted; font.pixelSize: 10
    }
    Canvas {
        id: plot
        objectName: "plotCanvas:" + root.chartKey
        property int paintedPointCount: 0
        anchors.left: parent.left; anchors.right: legend.left; anchors.top: parent.top; anchors.bottom: slider.top
        anchors.leftMargin: 64; anchors.rightMargin: 12; anchors.topMargin: 40; anchors.bottomMargin: 18
        onPaint: {
            var drawnPoints=0
            var c=getContext("2d"); c.reset(); c.fillStyle=String(Theme.chartBackground); c.fillRect(0,0,width,height)
            c.strokeStyle=String(Theme.chartAxis);c.lineWidth=1;c.beginPath();c.moveTo(.5,0);c.lineTo(.5,height-.5);c.lineTo(width,height-.5);c.stroke()
            c.strokeStyle=String(Theme.chartGrid);c.lineWidth=1
            for(var grid=1;grid<6;grid++){
                var gy=height-grid/6*(height-8)-4
                c.beginPath();c.moveTo(0,gy);c.lineTo(width,gy);c.stroke()
            }
            c.strokeStyle=String(Theme.chartAxis)
            var axisSections=6
            for(var tick=0;tick<=axisSections;tick++){var ty=tick*height/axisSections;c.beginPath();c.moveTo(0,ty);c.lineTo(5,ty);c.stroke()}
            var maxTime=root.fullDuration, maxValue=root.axisMaximum
            var visibleStart=root.viewStartTime, visibleDuration=root.viewDuration
            var ticks=root.timeTicks()
            for(var tickIndex=0;tickIndex<ticks.length;tickIndex++){
                var tx=(ticks[tickIndex]-visibleStart)/visibleDuration*width
                c.beginPath();c.moveTo(tx,height);c.lineTo(tx,height-5);c.stroke()
            }
            for(var i=0;i<root.metricKeys.length;i++){
                if(root.seriesEnabled.length>i && !root.seriesEnabled[i])continue
                var rows=root.renderedData[root.metricKeys[i]] || []
                if(!rows.length)continue
                var firstRow=Math.max(0,root.lowerBound(rows,visibleStart)-1)
                var lastRow=Math.min(rows.length,root.lowerBound(rows,visibleStart+visibleDuration)+1)
                c.beginPath(); c.strokeStyle=String(root.displayColors[i]); c.lineWidth=1.6
                var stride=Math.max(1,Math.ceil((lastRow-firstRow)/Math.max(1,width)))
                var started=false, previousTime=-1
                for(var bucketStart=firstRow;bucketStart<lastRow;bucketStart+=stride){
                    var bucketEnd=Math.min(lastRow,bucketStart+stride),minIndex=bucketStart,maxIndex=bucketStart
                    for(var scan=bucketStart+1;scan<bucketEnd;scan++){
                        if(Number(rows[scan].y)<Number(rows[minIndex].y))minIndex=scan
                        if(Number(rows[scan].y)>Number(rows[maxIndex].y))maxIndex=scan
                    }
                    var indexes=minIndex===maxIndex?[minIndex]:(minIndex<maxIndex?[minIndex,maxIndex]:[maxIndex,minIndex])
                    for(var pointIndex=0;pointIndex<indexes.length;pointIndex++){
                        var rowIndex=indexes[pointIndex]
                        var px=(Number(rows[rowIndex].x)-visibleStart)/visibleDuration*width, py=height-Number(rows[rowIndex].y)/maxValue*(height-8)-4
                        if(!started || root.crossesExcluded(previousTime,Number(rows[rowIndex].x))){c.moveTo(px,py);started=true}else c.lineTo(px,py)
                        previousTime=Number(rows[rowIndex].x)
                        drawnPoints++
                    }
                }
                c.stroke()
                if(rows.length===1){
                    c.fillStyle=String(root.displayColors[i]);c.beginPath();c.arc((Number(rows[0].x)-visibleStart)/visibleDuration*width,height-Number(rows[0].y)/maxValue*(height-8)-4,2.5,0,Math.PI*2);c.fill()
                }
            }
            paintedPointCount=drawnPoints
            var sceneColors=["#4269d4","#a45ac7","#2e9d86","#c98535","#b74f66"]
            var labelEnds=[-Infinity,-Infinity]
            for(i=0;i<root.controller.scenes.length;i++){
                var scene=root.controller.scenes[i]
                var sceneStart=(Number(scene.start)-visibleStart)/visibleDuration*width
                var sceneEnd=(Number(scene.end)-visibleStart)/visibleDuration*width
                c.globalAlpha=.13;c.fillStyle=String(scene.color || sceneColors[i%sceneColors.length]);c.fillRect(sceneStart,0,Math.max(1,sceneEnd-sceneStart),height);c.globalAlpha=1
                var labelStart=Math.max(0,sceneStart)+4, labelEnd=Math.min(width,sceneEnd)-4
                var lane=labelStart>=labelEnds[0]?0:labelStart>=labelEnds[1]?1:-1
                if(lane>=0 && labelEnd-labelStart>=18){
                    c.fillStyle=String(Theme.text);c.font="10px sans-serif"
                    var label=String(scene.name), available=labelEnd-labelStart
                    if(c.measureText(label).width>available){
                        while(label.length && c.measureText(label+"…").width>available)label=label.slice(0,-1)
                        label+="…"
                    }
                    c.fillText(label,labelStart,11+lane*14)
                    labelEnds[lane]=labelStart+c.measureText(label).width+8
                }
            }
            c.save();c.strokeStyle=String(Theme.muted);c.globalAlpha=.55;c.lineWidth=1;c.setLineDash([3,4])
            for(var cutIndex=0;cutIndex<root.timelineCuts.length;cutIndex++){
                var cutX=(Number(root.timelineCuts[cutIndex].time)-visibleStart)/visibleDuration*width
                if(cutX<0 || cutX>width)continue
                c.beginPath();c.moveTo(cutX,0);c.lineTo(cutX,height);c.stroke()
            }
            c.restore()
            if(root.activeMarking){
                var activeStart=(root.markingStart-visibleStart)/visibleDuration*width
                var activeEnd=(root.controller.duration-visibleStart)/visibleDuration*width
                c.globalAlpha=.07;c.fillStyle=String(Theme.blue);c.fillRect(activeStart,0,Math.max(2,activeEnd-activeStart),height);c.globalAlpha=1
                c.strokeStyle=String(Theme.chartAxis);c.lineWidth=1;c.beginPath();c.moveTo(activeStart,0);c.lineTo(activeStart,height);c.moveTo(activeEnd,0);c.lineTo(activeEnd,height);c.stroke()
                c.fillStyle=String(Theme.muted);c.font="10px sans-serif";c.fillText("标记中",Math.min(width-30,activeStart+4),11)
            }
            if(root.dragging){
                var a=Math.min(root.dragStartX,root.dragCurrentX),b=Math.max(root.dragStartX,root.dragCurrentX)
                c.globalAlpha=.16;c.fillStyle=String(Theme.blue);c.fillRect(a,0,b-a,height);c.globalAlpha=1
            }
            if(root.hoverPreviewVisible){
                c.strokeStyle=String(Theme.chartAxis);c.lineWidth=1;c.setLineDash([2,3]);c.beginPath();c.moveTo(root.hoverPreviewX,0);c.lineTo(root.hoverPreviewX,height);c.stroke();c.setLineDash([])
            }
        }
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        Component.onCompleted: root.refreshAxisMaximum()
        Connections {
            target: root.controller
            function onSamplesChanged() {
                root.resetRenderedData()
                if (root.renderActive) root.refreshStats()
            }
            function onSampleBatchAppended(batch) { root.acceptLiveBatch(batch) }
            function onSelectionChanged() { if (root.renderActive) root.refreshStats(false) }
            function onTimeLockChanged() { if (root.renderActive) root.refreshStats(false) }
            function onScenesChanged() { if (root.renderActive) plot.requestPaint() }
            function onChartRangeChanged() { root.syncSharedRange() }
        }
        MouseArea {
            id: plotMouse
            objectName: "plotMouse:" + root.chartKey
            anchors.fill: parent; cursorShape: Qt.CrossCursor; hoverEnabled: true
            acceptedButtons: Qt.LeftButton | (root.selectionMenuEnabled ? Qt.RightButton : Qt.NoButton)
            preventStealing: true
            onWheel: function(wheel) { wheel.accepted = false }
            onPressed: function(mouse) {
                if (mouse.button === Qt.RightButton) {
                    var point = plotMouse.mapToItem(root,mouse.x,mouse.y)
                    root.selectionMenuRequested(point.x,point.y)
                    return
                }
                root.dragStartX=mouse.x;root.dragCurrentX=mouse.x;root.dragging=false;root.hoverPreviewVisible=false
            }
            onPositionChanged: function(mouse) {
                if (pressed && (pressedButtons & Qt.LeftButton)) {
                    root.hoverPreviewVisible = false
                    root.dragCurrentX = Math.max(0, Math.min(width, mouse.x))
                    if (Math.abs(mouse.x - root.dragStartX) > 5) root.dragging = true
                } else if (!pressed) root.updateHoverPreview(mouse.x)
                plot.requestPaint()
            }
            onExited: { root.hoverPreviewVisible=false; plot.requestPaint() }
            onCanceled: {
                root.dragging = false; root.dragStartX = -1; root.dragCurrentX = -1
                root.hoverPreviewVisible = false
                plot.requestPaint()
            }
            onReleased: function(mouse) {
                if (mouse.button === Qt.RightButton) return
                if(root.dragging){
                    var startTime=root.viewStartTime+root.dragStartX/width*root.viewDuration
                    var endX=Math.max(0,Math.min(width,mouse.x))
                    var endTime=root.viewStartTime+endX/width*root.viewDuration
                    var actualDuration=Math.max(.001,root.controller.duration)
                    root.controller.setSelectionRatios(startTime/actualDuration,endTime/actualDuration)
                }
                else {
                    if(root.controller.hasSelection)root.controller.clearSelection()
                    var lockTime=root.viewStartTime+mouse.x/width*root.viewDuration
                    root.controller.setTimeLockRatio(lockTime/Math.max(.001,root.controller.duration))
                }
                root.hoverPreviewVisible=false
                // Also refresh synchronously on the chart that handled the
                // pointer release; this avoids waiting for notify ordering.
                root.refreshStats(false)
                root.dragging=false;root.dragStartX=-1;root.dragCurrentX=-1;plot.requestPaint()
            }
        }
    }
    // Selection and lock markers deliberately live outside Canvas. Canvas can
    // retain an old backing-store frame when several delegates repaint at the
    // same time; ordinary QML bindings update these positions atomically in
    // every chart without relying on requestPaint scheduling.
    Item {
        id: sharedInteractionOverlay
        anchors.fill: plot
        z: 6
        clip: true
        enabled: false
        property real selectionX1: (Number(root.controller.selectionStartRatio) * Number(root.controller.duration)
                                    - root.viewStartTime) / root.viewDuration * width
        property real selectionX2: (Number(root.controller.selectionEndRatio) * Number(root.controller.duration)
                                    - root.viewStartTime) / root.viewDuration * width
        property real lockX: (Number(root.controller.lockedTime) - root.viewStartTime)
                             / root.viewDuration * width

        Rectangle {
            objectName: "selectionOverlay:" + root.chartKey
            visible: root.controller.hasSelection
            x: sharedInteractionOverlay.selectionX1
            y: 0
            width: Math.max(1, sharedInteractionOverlay.selectionX2
                               - sharedInteractionOverlay.selectionX1)
            height: parent.height
            color: Qt.rgba(Theme.blue.r, Theme.blue.g, Theme.blue.b, .15)
            border.color: Theme.muted
            border.width: 1
        }
        Item {
            objectName: "lockMarker:" + root.chartKey
            visible: root.controller.hasTimeLock
            x: Math.round(sharedInteractionOverlay.lockX)
            width: 1
            height: parent.height
            Repeater {
                model: Math.ceil(sharedInteractionOverlay.height / 7)
                Rectangle {
                    required property int index
                    x: 0; y: index * 7
                    width: 1; height: 4
                    color: Theme.text
                }
            }
            Rectangle {
                x: -3; y: 5
                width: 6; height: 6; radius: 3
                color: Theme.text
            }
        }
    }
    Rectangle {
        visible: root.hoverPreviewVisible
        x: Math.max(plot.x + 4, Math.min(legend.x - width - 6, plot.x + root.hoverPreviewX + 8))
        y: 28; width: 154; height: 25 + hoverRows.implicitHeight; z: 12
        color: Theme.chartOverlay; border.color: Theme.border; radius: 8
        Column {
            id: hoverRows; anchors.fill: parent; anchors.margins: 6; spacing: 2
            Text { text: root.timeText(root.hoverPreviewTime); color: Theme.text; font.pixelSize: 10; font.weight: Font.DemiBold }
            Repeater {
                model: root.hoverPreviewRows
                Row {
                    required property var modelData; spacing: 5
                    Rectangle { width: 7; height: 7; radius: 4; color: root.colorForTitle(modelData.title || modelData.name, modelData.color); anchors.verticalCenter: parent.verticalCenter }
                    Text { width: 92; text: modelData.name; color: Theme.text; font.pixelSize: 10; elide: Text.ElideRight }
                    Text { text: modelData.value; color: root.colorForTitle(modelData.title || modelData.name, modelData.color); font.pixelSize: 10 }
                }
            }
        }
    }
    Rectangle {
        id: lockedInfoPanel
        objectName: "lockedInfoPanel:" + root.chartKey
        visible: root.controller.hasTimeLock && root.lockedStats.length > 0
        x: Math.max(44, Math.min(selectionStatsPanel.visible ? selectionStatsPanel.x - width - 8 : legend.x - width - 8,
             plot.x + (root.controller.lockedTime - root.viewStartTime) / root.viewDuration * plot.width + 8))
        y: 28; width: 174; height: Math.min(132, 28 + lockRows.implicitHeight)
        color: Theme.chartOverlay; border.color: Theme.border; radius: 8; z: 14
        ChartInfoScroll {
            id: lockedInfoScroll
            objectName: "lockedInfoScroll:" + root.chartKey
            anchors.fill: parent; anchors.margins: 5; clip: true
            contentHeight: lockRows.implicitHeight
            onPageScrollRequested: function(delta) { root.pageScrollRequested(delta) }
            Column {
                id: lockRows; width: lockedInfoScroll.contentWidth; spacing: 3
                Text { text: root.timeText(root.controller.lockedTime); color: Theme.text; font.pixelSize: 10; font.weight: Font.DemiBold }
                Repeater { model: root.lockedStats
                    Row { required property var modelData; spacing: 5
                        Rectangle { width: 7; height: 7; radius: 4; color: root.colorForTitle(modelData.title || modelData.name, modelData.color); anchors.verticalCenter: parent.verticalCenter }
                        Text { width: 88; text: modelData.title; color: Theme.text; font.pixelSize: 10; elide: Text.ElideRight }
                        Text { text: modelData.value + " " + modelData.unit; color: root.colorForTitle(modelData.title || modelData.name, modelData.color); font.pixelSize: 10 }
                    }
                }
            }
        }
    }
    Item {
        anchors.left: plot.left; anchors.right: plot.right; anchors.bottom: slider.top; height: 18
        Repeater {
            model: root.timeTicks()
            Text {
                required property real modelData
                property real seconds: modelData
                x: Math.max(0, Math.min(parent.width - width, (seconds - root.viewStartTime) / root.viewDuration * parent.width - width / 2))
                text: root.axisTimeText(seconds)
                color: Theme.muted; font.pixelSize: 10; font.weight: Font.Medium
            }
        }
    }
    ScrollView {
        id: legend; anchors.right: parent.right; anchors.rightMargin: 14; anchors.top: plot.top; anchors.bottom: plot.bottom
        objectName: "chartLegend:" + root.chartKey
        width: 148; clip: true; contentWidth: availableWidth
        contentHeight: legendRows.implicitHeight
        wheelEnabled: false
        WheelHandler {
            target: null
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: function(event) {
                var delta = event.pixelDelta.y || event.angleDelta.y / 120 * 48
                if (!isFinite(delta) || delta === 0) { event.accepted = false; return }
                var view = legend.contentItem
                var start = Number(view.originY) || 0
                var end = start + Math.max(0, legend.contentHeight - legend.availableHeight)
                var before = view.contentY
                view.contentY = Math.max(start, Math.min(end, before - delta))
                var remainder = delta - (before - view.contentY)
                if (Math.abs(remainder) > .01) root.pageScrollRequested(remainder)
                event.accepted = true
            }
        }
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical: ScrollBar {
            id: legendScrollBar
            objectName: "legendScrollBar:" + root.chartKey
            parent: legend
            x: legend.width - width; y: 0; width: 6; height: legend.height
            policy: ScrollBar.AsNeeded
            visible: size < 1
            active: size < 1
            contentItem: Rectangle {
                implicitWidth: 6; implicitHeight: 18; radius: 3
                color: legendScrollBar.pressed ? Theme.blue : Theme.chartAxis
            }
            background: Rectangle { radius: 3; color: Theme.chartGrid }
        }
        Column {
        id: legendRows
        width: legend.availableWidth - (legendScrollBar.visible ? legendScrollBar.width + 3 : 0)
        spacing: 3
        Repeater { model: root.metricKeys.length
            Item {
                id: legendRow
                objectName: "legendRow:" + root.metricKeys[index]
                required property int index
                width: legendRows.width
                height: Math.max(20, 14 + (latestText.visible ? 16 : 0) + (statusText.visible ? 14 : 0))
                property bool metricEnabled: root.seriesEnabled.length <= index || root.seriesEnabled[index]
                property bool metricSelectable: root.seriesSelectable.length <= index || root.seriesSelectable[index]
                opacity: metricEnabled ? 1 : .34
                Rectangle { x: 0; y: 4; width: 6; height: 6; radius: 3; color: legendRow.metricEnabled ? root.displayColors[legendRow.index] || Theme.muted : Theme.muted }
                Text {
                    id: metricName
                    x: 14; y: 0; width: parent.width - 14; height: 14
                    text: root.seriesNames[legendRow.index] || root.metricKeys[legendRow.index]
                    color: legendRow.metricEnabled ? Theme.text : Theme.muted
                    font.pixelSize: 11; font.weight: Font.Medium
                    verticalAlignment: Text.AlignVCenter
                    wrapMode: Text.NoWrap; maximumLineCount: 1; elide: Text.ElideRight
                }
                Text {
                    id: latestText
                    property var latestValue: root.latestValues[root.metricKeys[legendRow.index]]
                    objectName: "legendValue:" + root.metricKeys[legendRow.index]
                    x: 14; y: 14; width: parent.width - 14; height: 16
                    visible: latestValue !== undefined && latestValue !== null && legendRow.metricEnabled
                    text: visible ? Number(latestValue).toFixed(root.metricKeys[legendRow.index] === "fps" ? 2 : 1) : ""
                    color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold; font.family: Theme.numberFont
                    horizontalAlignment: Text.AlignLeft; verticalAlignment: Text.AlignVCenter
                }
                Text {
                    id: statusText
                    objectName: "metricStatus:" + root.metricKeys[legendRow.index]
                    x: 14; y: latestText.visible ? 30 : 14; width: parent.width - 14; height: 14
                    visible: legendRow.metricEnabled && root.seriesStatus.length > legendRow.index
                             && root.seriesStatus[legendRow.index] !== "可用" && root.seriesStatus[legendRow.index] !== "已记录"
                             && root.seriesStatus[legendRow.index] !== ""
                    text: root.seriesStatus[legendRow.index] || ""
                    color: legendRow.metricSelectable ? Theme.muted : Theme.chartWarning; font.pixelSize: 10
                    elide: Text.ElideRight
                }
                MouseArea {
                    id: metricLegendMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onWheel: function(wheel) { wheel.accepted = false }
                    onClicked: {
                        if (legendRow.metricEnabled || legendRow.metricSelectable)
                            root.controller.setMetricEnabled(root.metricKeys[legendRow.index], !legendRow.metricEnabled)
                    }
                }
                ToolTip.visible: metricLegendMouse.containsMouse
                ToolTip.text: (root.seriesNames[legendRow.index] || root.metricKeys[legendRow.index])
                    + (root.seriesReasons[legendRow.index] ? "\n" + root.seriesReasons[legendRow.index] : "")
                ToolTip.delay: 300
            }
        }
    }
    }
    Rectangle {
        id: selectionStatsPanel
        objectName: "selectionStatsPanel:" + root.chartKey
        visible: root.controller.hasSelection && root.stats.length > 0
        anchors.right: legend.left; anchors.rightMargin: 8; anchors.top: parent.top
        anchors.topMargin: 28
        width: 300; height: Math.min(132, 28 + statsColumn.implicitHeight); color: Theme.chartOverlay; border.color: Theme.border; radius: 8; z: 13
        ChartInfoScroll {
            id: selectionStatsScroll
            objectName: "selectionStatsScroll:" + root.chartKey
            anchors.fill: parent; anchors.margins: 5; clip: true
            contentHeight: statsColumn.implicitHeight
            onPageScrollRequested: function(delta) { root.pageScrollRequested(delta) }
            Column {
                id: statsColumn; width: selectionStatsScroll.contentWidth; spacing: 3
                Row { spacing: 0
                    Text { width: 127; text: "框选区间统计"; color: Theme.text; font.pixelSize: 10; font.weight: Font.DemiBold }
                    Text { width: 49; text: "平均"; color: Theme.muted; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter }
                    Text { width: 49; text: "最小"; color: Theme.muted; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter }
                    Text { width: 49; text: "最大"; color: Theme.muted; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter }
                }
                Repeater { model: root.stats
                    Row { required property var modelData
                        Rectangle { width: 7; height: 7; radius: 4; color: root.colorForTitle(modelData.title || modelData.name, modelData.color); anchors.verticalCenter: parent.verticalCenter }
                        Text { width: 120; leftPadding: 6; text: modelData.title; color: Theme.text; font.pixelSize: 10; elide: Text.ElideRight }
                        Text { width: 49; text: modelData.avg; color: root.colorForTitle(modelData.title || modelData.name, modelData.color); font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter }
                        Text { width: 49; text: modelData.min; color: Theme.text; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter }
                        Text { width: 49; text: modelData.max; color: Theme.text; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter }
                    }
                }
            }
        }
    }
    OriginalRangeSlider {
        id: slider; anchors.left: parent.left; anchors.right: legend.left; anchors.bottom: parent.bottom
        anchors.leftMargin: 48; anchors.rightMargin: 12; anchors.bottomMargin: 8; height: 26
        first.onMoved: {
            if (!root.synchronizingRange)
                root.controller.setChartViewRange(slider.first.value, slider.second.value)
        }
        second.onMoved: {
            if (!root.synchronizingRange)
                root.controller.setChartViewRange(slider.first.value, slider.second.value)
        }
        Component.onCompleted: root.syncSharedRange()
    }
}
