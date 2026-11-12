import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: plot
    property var curveData: ({curves: []})
    property var colors: ["#5ba6ff", "#f2a65a", "#72dbaf", "#db82e8", "#ed7581", "#a5cb53"]
    onCurveDataChanged: graph.requestPaint()
    Canvas {
        id: graph; objectName: "studioPlotCanvas"
        Layout.fillWidth: true; Layout.fillHeight: true
        Layout.minimumHeight: 100
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var c=getContext("2d"), curves=plot.curveData.curves || []
            c.reset(); c.fillStyle="#131c2c";c.fillRect(0,0,width,height)
            var xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity
            curves.forEach(s=>(s.points||[]).forEach(p=>{if(isFinite(p.x)&&isFinite(p.y)){xmin=Math.min(xmin,p.x);xmax=Math.max(xmax,p.x);ymin=Math.min(ymin,p.y);ymax=Math.max(ymax,p.y)}}))
            if(!isFinite(ymin)){c.fillStyle="#91a3c2";c.fillText("没有实际数据，不能生成曲线",45,50);return}
            if(ymax===ymin){var padding=Math.max(1,Math.abs(ymin)*.02)/2;ymin-=padding;ymax+=padding}
            var span=Math.max(1e-9,ymax-ymin), xspan=Math.max(.001,xmax-xmin),w=Math.max(1,width-84),h=Math.max(1,height-42)
            c.strokeStyle="#4c5d79";c.beginPath();c.moveTo(62,12);c.lineTo(62,12+h);c.lineTo(62+w,12+h);c.stroke()
            c.fillStyle="#a9bad4";c.font="10px sans-serif"
            c.fillText(ymax.toFixed(1),2,20);c.fillText(ymin.toFixed(1),2,12+h)
            c.fillText(xmin.toFixed(2)+" s",62,height-5);c.fillText(xmax.toFixed(2)+" s",width-64,height-5)
            curves.forEach((s,i)=>{
                c.strokeStyle=plot.colors[i%plot.colors.length];c.lineWidth=1.4;c.beginPath()
                var last=null, segment=null
                ;(s.points||[]).forEach(p=>{
                    if(!isFinite(p.x)||!isFinite(p.y)){last=null;return}
                    var x=62+(p.x-xmin)/xspan*w,y=12+h-(p.y-ymin)/span*h
                    if(last===null || p.segment!==segment || (!s.reduced&&s.gap_threshold&&p.x-last>s.gap_threshold))c.moveTo(x,y);else c.lineTo(x,y)
                    last=p.x;segment=p.segment
                });c.stroke()
                if((s.points||[]).length===1){var p=s.points[0];c.fillStyle=plot.colors[i%plot.colors.length];c.beginPath();c.arc(62+(p.x-xmin)/xspan*w,12+h-(p.y-ymin)/span*h,3,0,Math.PI*2);c.fill()}
            })
        }
    }
    Flow {
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: plot.curveData.curves || []
            Text { required property var modelData; required property int index; text: (modelData.name||modelData.id||"逐帧")+" · "+(modelData.original_count||modelData.points.length)+" 点"; textFormat: Text.PlainText; color: plot.colors[index%plot.colors.length]; font.pixelSize: 10 }
        }
    }
}
