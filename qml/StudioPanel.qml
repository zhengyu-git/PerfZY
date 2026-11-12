import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

AppDialog {
    id: panel
    objectName: "studioPanel"
    property var studio: appController.studio
    property string logPath: ""
    property var planMetrics: null
    readonly property var guides: [
        {title:"1. 异常取证", prepare:"设置对下一次采集生效，也可在采集中调整；查看结果需选中一条已保存记录。", steps:"① 填阈值并点「应用取证设置」。\n② 返回主界面采集，结束后停止并保存。\n③ 再打开此页，勾选记录，点事件旁的「证据包」。", result:"自动保存阈值命中时间和附近已有数据；没有命中就没有事件，旧记录不会按新阈值补算。"},
        {title:"2. 内存专项", prepare:"左侧勾选一条采到内存的记录；秒数从该次采集开始计。", steps:"① 填进入玩法、退出玩法的时间，例如 10 秒、30 秒。\n② 前后观察填 10；记录需覆盖进入前和退出后。\n③ 点「分析内存」，查看前/内/后均值、峰值和退出残留。", result:"识别增长和是否回落；采样不足会说明原因，不直接认定泄漏。"},
        {title:"3. 卡顿详情", prepare:"选中一条有真实逐帧数据的新记录；只有每秒平均值不够。", steps:"① 卡顿阈值可填 50 毫秒，点「列出卡顿帧」。\n② 在结果某一行点「详情」，展开前后 2 秒。\n③ 查看逐帧耗时、附近采样和已有截图。", result:"没有帧源会显示缺失原因；列表为空也可能是没有超过阈值的帧。"},
        {title:"4. 任务编排", prepare:"先在主界面选设备、应用、截图目标和指标；不必勾历史记录。", steps:"① 填方案名、应用版本、每轮秒数和循环轮数。\n② 首次可用每轮 20 秒、1 轮、间隔 1 秒；点「开始执行」。\n③ 结束自动保存，点「刷新记录」查看；中途可停止并保存当前轮。", result:"保存多轮记录和可选 HTML，不操作、不启动或重启游戏；保存方案本身不会执行。"},
        {title:"5. 对比工作台", prepare:"左侧勾选两条或多条同设备、同应用且采到同一指标的记录。", steps:"① 选内存或 CPU 等已有指标；场景名可先留空。\n② 区间填 0 到 20 秒，点「叠加曲线」。\n③ 可填备注后保存，或点「导出区间证据包」。", result:"ZIP 内的 comparison.html 可直接打开；点底部「导出目录」找到文件。"}
    ]
    StudioHelp { id: help; parent: Overlay.overlay; guides: panel.guides }
    width: Math.min(1160,parent ? parent.width-28 : 1160)
    height: Math.min(740,parent ? parent.height-28 : 740)
    anchors.centerIn: parent; modal: true; focus: true; padding: 14
    title: "性能测试工作台"
    titleActions: Component { CaptureButton { objectName: "studioHelpButton"; text: "完整使用教程"; onClicked: help.open() } }
    onOpened: studio.refresh()
    palette.window: Theme.color("#222838"); palette.base: Theme.color("#182032"); palette.text: Theme.color("#dce6fa")
    palette.buttonText: Theme.color("#dce6fa"); palette.button: Theme.color("#35415a"); palette.highlight: Theme.color("#4779e6")
    palette.windowText: Theme.color("#dce6fa");palette.placeholderText: Theme.color("#8094b7")
    
    
    function n(v) { return v===undefined||v===null||!isFinite(Number(v)) ? "—" : Number(v).toFixed(2) }
    function planValue() { return {name:planName.text,version:planVersion.text,duration:Number(planDuration.text),rounds:Number(planRounds.text),interval:Number(planInterval.text),rest:Number(planRest.text),screenshots:planShots.checked,report:planReport.checked,metrics:panel.planMetrics||studio.currentMetrics} }
    contentItem: ColumnLayout {
        spacing: 8
        Label { Layout.fillWidth: true; text: "本地保存，不上传数据。Windows FPS 使用应用呈现帧间隔，不等于屏幕实际显示帧率；没有真实帧源时，卡顿详情会明确显示缺失。"; color: Theme.chartWarning; font.pixelSize: 12; wrapMode: Text.Wrap }
        Label {Layout.fillWidth:true;text:"已选 "+studio.selectedIds.length+" 条 · 取证 / 内存 / 卡顿对象："+studio.primaryRecordName;elide:Text.ElideRight;color:Theme.color("#a9c6ee");font.pixelSize: 12;textFormat:Text.PlainText}
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 10
            ColumnLayout {
                Layout.preferredWidth: 210; Layout.maximumWidth: 210; Layout.fillHeight: true
                Label { text: "本地记录（最多选12条）"; color: Theme.color("#d8e4fa") }
                CaptureButton { text: "刷新记录"; enabled: !studio.busy; onClicked: {appController.refreshSessionRecords();studio.refresh()} }
                CaptureButton {text:"清空选择";enabled:!studio.busy;onClicked:studio.clearRecords()}
                CaptureButton {text:"在主界面打开";enabled:!appController.collecting&&!appController.busy;onClicked:studio.openPrimary()}
                ListView {
                    objectName: "studioRecords"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
                    model: studio.records
                    ScrollBar.vertical: ScrollBar {}
                    Label { anchors.centerIn: parent; width: parent.width-16; visible: parent.count===0; text: "还没有已保存记录。\n关闭工作台，在主界面采集并停止保存，再回来点「刷新记录」。\n也可用「任务编排」发起新采集。"; color: Theme.color("#a7bad8"); wrapMode: Text.Wrap; font.pixelSize: 12 }
                    delegate: CheckDelegate {
                        required property var modelData
                        width: ListView.view.width-10; height: 66
                        checked: modelData.selected; enabled: !studio.busy
                        contentItem: Text { text: modelData.name+"\n版本："+(modelData.version||"未填写")+" · "+modelData.duration; textFormat: Text.PlainText; color: Theme.color("#bfd0eb"); wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight; font.pixelSize: 12; leftPadding: 0; rightPadding: 28 }
                        onToggled: studio.selectRecord(modelData.id,checked)
                    }
                }
                Label { Layout.fillWidth: true; text: "取证/内存/卡顿针对第一条选中记录；多选用于对比。"; wrapMode: Text.Wrap; color: Theme.color("#93a7c7"); font.pixelSize: 12 }
                CaptureTextField { id:versionTag; Layout.fillWidth: true; placeholderText: "给选中记录填写版本"; selectByMouse: true }
                CaptureButton { text: "保存版本标签"; enabled: !studio.busy; onClicked: studio.tagSelectedVersion(versionTag.text) }
            }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true
                TabBar {
                    id: tabs; objectName: "studioTabs"; Layout.fillWidth: true
                    DialogTabButton { text: "异常取证"; font.pixelSize: 12 }
                    DialogTabButton { text: "内存专项"; font.pixelSize: 12 }
                    DialogTabButton { text: "卡顿详情"; font.pixelSize: 12 }
                    DialogTabButton { text: "任务编排"; font.pixelSize: 12 }
                    DialogTabButton { text: "对比工作台"; font.pixelSize: 12 }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: quickGuide.implicitHeight+16
                    color: Theme.color("#29374d"); radius: 5
                    Label { id: quickGuide; objectName: "studioQuickGuide"; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8; text: panel.guides[tabs.currentIndex].steps; color: Theme.color("#d5e5ff"); font.pixelSize: 12; wrapMode: Text.Wrap; textFormat: Text.PlainText }
                }
                ScrollView {
                    id: pageScroll; objectName: "studioPageScroll"
                    Layout.fillWidth: true; Layout.fillHeight: true
                    contentWidth: availableWidth
                    contentHeight: Math.max(availableHeight,tabs.currentIndex===2 ? 540 : 465)
                    clip: true
                StackLayout {
                    currentIndex: tabs.currentIndex; width: pageScroll.availableWidth; height: pageScroll.contentHeight
                    ColumnLayout {
                        CheckBox { id:evidenceEnabled; text: "开启自动阈值取证（复用已有指标和截图）"; checked: studio.options.enabled }
                        GridLayout {
                            Layout.fillWidth: true; columns: 4
                            Label { text: "FPS <"; color: Theme.color("#bdcce5") }
                            CaptureTextField { id:efps; Layout.fillWidth: true; text: studio.options.fps_below }
                            Label { text: "帧耗时 > ms"; color: Theme.color("#bdcce5") }
                            CaptureTextField { id:eft; Layout.fillWidth: true; text: studio.options.frame_above }
                            Label { text: "CPU > %"; color: Theme.color("#bdcce5") }
                            CaptureTextField { id:ecpu; Layout.fillWidth: true; text: studio.options.cpu_above }
                            Label { text: "内存增量 MB"; color: Theme.color("#bdcce5") }
                            CaptureTextField { id:emem; Layout.fillWidth: true; text: studio.options.memory_jump }
                            Label { text: "持续秒数"; color: Theme.color("#bdcce5") }
                            CaptureTextField { id:ehold; Layout.fillWidth: true; text: studio.options.hold }
                            Label { text: "冷却秒数"; color: Theme.color("#bdcce5") }
                            CaptureTextField { id:ecool; Layout.fillWidth: true; text: studio.options.cooldown }
                            Label { text: "数量上限"; color: Theme.color("#bdcce5") }
                            CaptureTextField { id:ecap; Layout.fillWidth: true; text: studio.options.max_events }
                            Label { text: "前后窗口 s"; color: Theme.color("#bdcce5") }
                            CaptureTextField { id:ectx; Layout.fillWidth: true; text: studio.options.context }
                        }
                        CaptureButton { text: "应用取证设置"; onClicked: studio.setEvidenceOptions({enabled:evidenceEnabled.checked,fps_below:Number(efps.text),frame_above:Number(eft.text),cpu_above:Number(ecpu.text),memory_jump:Number(emem.text),hold:Number(ehold.text),cooldown:Number(ecool.text),max_events:Number(ecap.text),context:Number(ectx.text)}) }
                        Label { Layout.fillWidth: true; text: "采集中显示实时取证标记，停止保存后可导出附近数据、截图和可选日志；阈值命中不是根因。"; wrapMode: Text.Wrap; color: Theme.color("#93a7c7"); font.pixelSize: 12 }
                        StudioRows {
                            objectName: "studioEvidenceRows"; Layout.fillWidth: true; Layout.fillHeight: true
                            rows: studio.events; actionText: "证据包";secondaryActionText:"定位"
                            emptyText: studio.selectedIds.length===0 ? "先应用取证设置，再返回主界面采集并保存，最后勾选左侧记录。" : "所选记录没有取证事件：可能未命中阈值、取证未开启，或这是加入取证功能之前的旧记录。新设置不会追溯补算。"
                            formatRow: function(r){return "#"+r.id+" · "+panel.n(r.time)+" s  ["+panel.n(r.start)+"–"+panel.n(r.end)+"]\n"+(r.reasons||[]).map(x=>x.reason+" "+x.metric+"="+panel.n(x.value)).join("；")}
                            onActivated: function(i,r){studio.exportEvidence(i,panel.logPath)}
                            onSecondaryActivated:function(i,r){if(studio.focusEvidence(i))panel.close()}
                        }
                    }
                    ColumnLayout {
                        GridLayout {
                            Layout.fillWidth:true;columns:4
                            Label {text:"进入时间 s";color:Theme.color("#bdcce5")} CaptureTextField {id:enter;Layout.fillWidth:true;text:"10"}
                            Label {text:"退出时间 s";color:Theme.color("#bdcce5")} CaptureTextField {id:exitTime;Layout.fillWidth:true;text:"30"}
                            Label {text:"前后观察 s";color:Theme.color("#bdcce5")} CaptureTextField {id:memoryWindow;Layout.fillWidth:true;text:"10"}
                            CaptureButton {text:"分析内存";enabled:!studio.busy;onClicked:studio.memory(Number(enter.text),Number(exitTime.text),Number(memoryWindow.text))}
                        }
                        Label {Layout.fillWidth:true;text:"进入/退出时间可填写已标记场景的起止时间；展示进入前、玩法内、退出后，绝不直接认定泄漏。";wrapMode:Text.Wrap;color:Theme.color("#93a7c7");font.pixelSize: 12}
                        StudioRows {
                            Layout.fillWidth:true;Layout.fillHeight:true;rows:studio.result.memory ? studio.result.memory.rows : []
                            emptyText: "勾选一条采到内存的记录 → 填进入/退出时间（相对采集开始的秒数）→ 点击「分析内存」。"
                            formatRow:function(r){return r.metric+" · "+r.verdict+"\n前/内/后 MB："+panel.n(r.before)+" / "+panel.n(r.during)+" / "+panel.n(r.after)+"；峰值 "+panel.n(r.peak)+"\n增长 "+panel.n(r.growth_mb_min)+" MB/min；退出残留 "+panel.n(r.residual_mb)+" MB；样本 "+r.counts.join("/")}
                        }
                        Label {Layout.fillWidth:true;text:studio.result.memory ? studio.result.memory.notes.join("\n") : "没有采到的 Java / Native / Graphics 分类不会补造。";wrapMode:Text.Wrap;color:Theme.color("#93a7c7");font.pixelSize: 12}
                    }
                    ColumnLayout {
                        RowLayout {
                            Label{text:"卡顿阈值 ms";color:Theme.color("#bdcce5")} CaptureTextField {id:hitchThreshold;Layout.preferredWidth:60;text:"50"}
                            CaptureButton {text:"列出卡顿帧";enabled:!studio.busy;onClicked:studio.listHitches(Number(hitchThreshold.text))}
                            Label{text:"附近±s";color:Theme.color("#bdcce5")} CaptureTextField {id:radius;Layout.preferredWidth:45;text:"2"}
                        }
                        StudioRows {
                            Layout.fillWidth:true;Layout.preferredHeight:120
                            rows:studio.result.hitches ? studio.result.hitches.rows : [];actionText:"详情"
                            emptyText: studio.result.hitches ? studio.result.hitches.reason : "勾选有真实逐帧的新记录，点「列出卡顿帧」。旧记录无法补帧；有帧数据也不保证超过卡顿阈值。"
                            formatRow:function(r){return panel.n(r.x)+" s · "+panel.n(r.frame_ms)+" ms · "+r.source+"\nCPU/GPU Busy："+panel.n(r.cpu_busy)+" / "+panel.n(r.gpu_busy)+" ms · "+r.clock}
                            onActivated:function(i,r){studio.frameDetail(r.x,Number(radius.text))}
                        }
                        StudioPlot {
                            Layout.fillWidth:true;Layout.fillHeight:true
                            curveData: ({curves:studio.result.detail && studio.result.detail.rows.length ? [{name:"真实逐帧耗时 ms",points:studio.result.detail.rows.map(r=>({x:r.x,y:r.frame_ms})),original_count:studio.result.detail.total}] : []})
                        }
                        ListView {
                            Layout.fillWidth:true;Layout.preferredHeight:80;orientation:ListView.Horizontal;clip:true;spacing:5
                            model:studio.result.detail ? studio.result.detail.screenshots : []
                            delegate:Image{required property var modelData;width:132;height:76;source:modelData.image;fillMode:Image.PreserveAspectFit}
                        }
                        Label {Layout.fillWidth:true;text:studio.result.detail ? "CPU采样%："+studio.result.detail.cpu.slice(-6).map(p=>panel.n(p.x)+"s="+panel.n(p.y)).join("；")+"\nGPU采样%："+studio.result.detail.gpu.slice(-6).map(p=>panel.n(p.x)+"s="+panel.n(p.y)).join("；") : "";wrapMode:Text.Wrap;color:Theme.color("#b2c6e4");font.pixelSize: 12}
                        Label {Layout.fillWidth:true;text:studio.result.detail ? (studio.result.detail.reason||studio.result.detail.note)+(studio.result.detail.truncated ? "（仅展示最靠近所选时间的2000帧）" : "") : "逐帧记录以独立文件留存；旧记录和缺失帧源无法补出真实帧详情，也没有调用栈/火焰图。";wrapMode:Text.Wrap;color:Theme.color("#93a7c7");font.pixelSize: 12}
                    }
                    ColumnLayout {
                        ComboBox {
                            Layout.fillWidth:true;model:studio.plans;textRole:"name";displayText:currentIndex>=0 ? currentText : "选择已保存方案"
                            onActivated:function(i){var p=studio.plans[i];planName.text=p.name;planVersion.text=p.version;planDuration.text=p.duration;planRounds.text=p.rounds;planInterval.text=p.interval;planRest.text=p.rest;planShots.checked=p.screenshots;planReport.checked=p.report;panel.planMetrics=p.metrics}
                        }
                        GridLayout {
                            Layout.fillWidth:true;columns:4
                            Label{text:"方案名称";color:Theme.color("#bdcce5")}CaptureTextField {id:planName;Layout.fillWidth:true;text:"性能回归"}
                            Label{text:"应用版本";color:Theme.color("#bdcce5")}CaptureTextField {id:planVersion;Layout.fillWidth:true;placeholderText:"例如 1.0.0"}
                            Label{text:"每轮秒数";color:Theme.color("#bdcce5")}CaptureTextField {id:planDuration;Layout.fillWidth:true;text:"60"}
                            Label{text:"循环轮次";color:Theme.color("#bdcce5")}CaptureTextField {id:planRounds;Layout.fillWidth:true;text:"3"}
                            Label{text:"采样间隔 s";color:Theme.color("#bdcce5")}CaptureTextField {id:planInterval;Layout.fillWidth:true;text:"1"}
                            Label{text:"轮间等待 s";color:Theme.color("#bdcce5")}CaptureTextField {id:planRest;Layout.fillWidth:true;text:"5"}
                            CheckBox{id:planShots;text:"自动截图";checked:true}CheckBox{id:planReport;text:"每轮生成HTML";checked:true}
                        }
                        Label {Layout.fillWidth:true;text:"方案指标："+(panel.planMetrics||studio.currentMetrics).join(", ");wrapMode:Text.Wrap;maximumLineCount:2;elide:Text.ElideRight;color:Theme.color("#93a7c7");font.pixelSize: 12}
                        CaptureButton {text:"使用主界面当前指标";enabled:!studio.taskRunning;onClicked:panel.planMetrics=studio.currentMetrics}
                        RowLayout {
                            CaptureButton {text:"保存方案";enabled:!studio.taskRunning;onClicked:studio.savePlan(panel.planValue())}
                            CaptureButton {text:"开始执行";enabled:!studio.taskRunning&&!studio.busy;onClicked:studio.startPlan(panel.planValue())}
                            CaptureButton {text:"停止并保存当前轮";enabled:studio.taskRunning;onClicked:studio.cancelPlan()}
                        }
                        Label {Layout.fillWidth:true;text:"任务不启动/重启或操作游戏；启动时固定当前设备、应用和截图目标。执行期间禁止手动再开一轮；停止会保存当前轮。";wrapMode:Text.Wrap;color:Theme.color("#e3b97e");font.pixelSize: 12}
                        Label {Layout.fillWidth:true;text:(studio.task.state||"未开始")+" · 轮次 "+(studio.task.round||0)+"/"+(studio.task.total||0)+"\n"+(studio.task.error||"");wrapMode:Text.Wrap;color:Theme.color("#d4e3fb")}
                        StudioRows {Layout.fillWidth:true;Layout.fillHeight:true;rows:studio.task.results||[];emptyText:"先在主界面选择设备和应用，再填方案参数并点「开始执行」。保存方案不会自动开始采集。";formatRow:function(r){return r.name+"\n"+(r.error||r.report_error||r.path)}}
                    }
                    ColumnLayout {
                        RowLayout {
                            Layout.fillWidth:true
                            ComboBox{id:compareMetric;Layout.fillWidth:true;model:studio.metrics;textRole:"title"}
                            CaptureTextField {id:alignScene;Layout.fillWidth:true;placeholderText:"同名场景，留空从记录起点对齐"}
                        }
                        RowLayout {
                            Label{text:"区间 s";color:Theme.color("#bdcce5")}CaptureTextField {id:rangeStart;Layout.preferredWidth:65;text:"0"}
                            Label{text:"至";color:Theme.color("#bdcce5")}CaptureTextField {id:rangeEnd;Layout.preferredWidth:65;text:"60"}
                            CaptureButton {text:"叠加曲线";enabled:!studio.busy;onClicked:studio.compare(studio.metrics[compareMetric.currentIndex].key,alignScene.text,Number(rangeStart.text),Number(rangeEnd.text))}
                            CaptureButton {text:"导出区间证据包";enabled:!studio.busy;onClicked:studio.exportRange(Number(rangeStart.text),Number(rangeEnd.text),studio.metrics[compareMetric.currentIndex].key,alignScene.text,panel.logPath)}
                        }
                        StudioPlot{Layout.fillWidth:true;Layout.fillHeight:true;curveData:studio.result.overlay||({curves:[]})}
                        Label{Layout.fillWidth:true;text:studio.result.overlay ? studio.result.overlay.notes.join("\n") : "多选记录后叠加同一指标；不同设备/应用不强行对比。";wrapMode:Text.Wrap;maximumLineCount:3;elide:Text.ElideRight;color:Theme.color("#93a7c7");font.pixelSize: 12}
                        RowLayout {
                            Layout.fillWidth:true
                            CaptureTextField {id:note;Layout.fillWidth:true;placeholderText:"为当前对齐区间添加备注（保存到第一条记录）"}
                            CaptureButton {text:"保存备注";enabled:!studio.busy;onClicked:studio.addNote(Number(rangeStart.text),Number(rangeEnd.text),note.text)}
                        }
                    }
                }
                }
            }
        }
        CaptureTextField {Layout.fillWidth:true;placeholderText:"可选本地 .log/.txt 日志路径；留空不读取，导出时仅附最后64KB，分享前请检查脱敏";selectByMouse:true;onTextChanged:panel.logPath=text}
        RowLayout {
            Layout.fillWidth:true
            Label {objectName:"studioStatus";Layout.fillWidth:true;text:studio.message;wrapMode:Text.Wrap;maximumLineCount:2;elide:Text.ElideRight;color:Theme.color("#c0d5f7");font.pixelSize: 12;textFormat:Text.PlainText}
            BusyIndicator {running:studio.busy;visible:running;Layout.preferredWidth:24;Layout.preferredHeight:24}
            CaptureButton {text:"导出目录";onClicked:studio.openExportFolder()}
            CaptureButton {text:"分析JSON";enabled:!studio.busy;onClicked:studio.exportResults()}
            CaptureButton {text:"关闭";onClicked:panel.close()}
        }
    }
}
