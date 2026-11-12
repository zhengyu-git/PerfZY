"""Human-readable explanations of cached collector evidence, not device probes."""


def collection_quality_notes(details):
    notes = []
    if details.get('collection_revision') == 2:
        render = details.get('render_stream') or {}
        scope = details.get('process_scope') or {}
        tree = scope.get('resource_scope') == 'tree'
        notes.append('Windows：CPU、内存、I/O、线程、GPU 计数范围为' +
                     ('所选 PID 及经身份与父子链验证的子进程' if tree else '所选 PID') +
                     '；帧指标仍从该 PID 及子进程中选择一个主渲染流，不混合多个交换链。')
        if tree:
            notes.append('进程树资源：本轮验证 PID=' + ','.join(map(str, scope.get('resource_pids') or [])) +
                         '；工作集为各进程 RSS 相加，共享页可能重复计数，不是进程树唯一物理内存。')
            incomplete = [key for key, coverage in (scope.get('resource_metric_coverage') or {}).items()
                          if not coverage.get('complete')]
            if scope.get('resource_failed_pids') or scope.get('resource_discovery_failed') or incomplete:
                notes.append('进程树资源采样不完整或正在建立增量基线：相关聚合指标保持缺失，不把部分进程的和当完整值；失败 PID=' +
                             ','.join(map(str, scope.get('resource_failed_pids') or [])) +
                             '，受影响指标=' + ','.join(sorted(incomplete)) + '。')
            notes.append('进程树为动态快照，未报告失败不能证明捕获了全部短命子进程；GPU 仅汇总该范围内已返回的 PDH 实例，无实例不视为零，也不保证全部进程 GPU 数据完整。')
        if render.get('pid'):
            notes.append(f"当前渲染 PID={render['pid']}，所选 PID={scope.get('selected_pid')}，交换链={render.get('swapchain') or '来源未提供'}；主流按活动帧数选择，仍须核对是否为预期游戏窗口。")
            if render['pid'] != scope.get('selected_pid') and not tree:
                notes.append('渲染发生在子进程：当前 CPU/内存/GPU 资源曲线不包含该子进程；如需分析其资源，请选择对应渲染进程采集。')
            elif tree and render['pid'] not in (scope.get('resource_pids') or []):
                notes.append('当前渲染 PID 尚未进入本轮验证的资源进程集合，帧数据与资源统计人口不完全一致，请核对诊断。')
        integrity = details.get('integrity') or {}
        if integrity.get('queue_overflow_rows') or integrity.get('etw_loss_reported'):
            notes.append(f"已检测帧数据不完整：本地队列溢出丢弃 {integrity.get('queue_overflow_rows',0)} 行，ETW 报告丢失={bool(integrity.get('etw_loss_reported'))}；这些记录不适合直接用于严格帧尾部验收。")
        else:
            notes.append('尚未报告队列溢出或 ETW 丢失；这不是事件完整性证明。FPS 为应用呈现帧率，不是屏幕实际显示帧率。')
        window = integrity.get('last_window') or {}
        if window.get('unknown_display_status_frames'):
            notes.append(f"最近窗口有 {window['unknown_display_status_frames']} 帧显示状态未知，不能当成成功显示；显示丢帧比例只基于状态已知的帧。")
        notes.append('仅过滤来源明确标识的已知生成/重复帧；FrameType 缺失时无法证明没有插帧。')
    if 'frame_integrity' in details:
        quality = details['frame_integrity']
        notes.append(f"Android：帧源基线重建 {quality.get('baseline_resets',0)} 次，图层切换 {quality.get('layer_switches',0)} 次；重启/传输中断后先建立基线，不跨断档推算 FPS。")
    if 'observation_policy' in details:
        notes.append('iOS：按各字段在电脑端的单调时钟接收时间对齐；有效缓存可显示，但同一观测不重复写入曲线和统计，接收时间不等同设备精确事件时间。')
        if details.get('fps_method') == 'ios-coreprofile-interval-fps-v1':
            counts = (details.get('metric_source_counts') or {}).get('fps') or {}
            notes.append(f"iOS FPS 优先使用实际帧间隔；有效窗口中逐帧来源 {counts.get('coreprofile_frame_intervals', 0)} 次，计数器回退 {counts.get('coreanimation_counter_fallback', 0)} 次。次数不是帧数，也不证明采集覆盖完整。")
            if details.get('frame_trace_error'):
                notes.append('iOS FPS 已回退计数器，可能仍为整数：' + str(details['frame_trace_error'])[:350])
        elif details.get('raw_frame_events'):
            notes.append('本记录保留独立逐帧事件；FPS 仍为 CoreAnimation 计数器，Frame Time 曲线仍是 1000/FPS 估算。')
        else:
            notes.append('本记录未声明实际逐帧 FPS 来源，FPS 使用 CoreAnimation 计数器，帧时间为 1000/FPS 估算。')
        notes.append('iOS 帧源为经过目标活动状态筛选的设备数据，非 PID 隔离；深度 GPU 计数尚未接入。')
    return notes
