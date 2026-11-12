"""Narrow device CoreAnimation kdebug frame stream; never derived from FPS."""
import asyncio
from contextlib import suppress
import math
import struct

from ..domain.validation import MAX_FRAME_TIME_MS
from ..domain.jank import JANK_METRICS

FRAME_EVENT_ID = 0x31800318
FRAME_FILTERS = {0x25990000, 0x31B00000, 0x31800108}
FRAME_SOURCE = 'iOS CoreProfileSessionTap'
RECORD = struct.Struct('<Q32sQIIQ')
FPS_CONTRACT = 'ios-coreprofile-interval-fps-v1'
FRAME_RATE_METRICS = frozenset({'fps', 'frame_time'})
TRACE_METRICS = JANK_METRICS | FRAME_RATE_METRICS


def fps_from_intervals(frames):
    """Reciprocal mean of actual intervals, never an integer count per poll."""
    costs = [frame.get('frame_ms') for frame in frames]
    if not costs or any(isinstance(cost, bool) or not isinstance(cost, (float, int))
                        or not math.isfinite(cost) or not 0 < cost <= MAX_FRAME_TIME_MS for cost in costs):
        return None
    return 1000.0 * len(costs) / math.fsum(costs)


class IOSFrameDecoder:
    def __init__(self, numer, denom):
        self.factor = float(numer) / float(denom)
        if not math.isfinite(self.factor) or self.factor <= 0:
            raise ValueError('iOS Mach 时钟比例无效')
        self.last_tick = None
        self.duplicates = self.out_of_order = 0

    def decode(self, data):
        if not isinstance(data, bytes) or len(data) % RECORD.size or len(data) > 4 * 1024 * 1024:
            raise ValueError(f'iOS 逐帧数据格式不受支持（{len(data)} 字节，头部 {data[:8].hex()}）；未从平均 FPS 推算卡顿')
        rows = []
        for tick, _, tid, debugid, _, _ in RECORD.iter_unpack(data):
            if debugid != FRAME_EVENT_ID or tick <= 0:
                continue
            if self.last_tick is None:
                self.last_tick = tick
                continue
            if tick <= self.last_tick:
                self.duplicates += int(tick == self.last_tick)
                self.out_of_order += int(tick < self.last_tick)
                continue
            cost = (tick - self.last_tick) * self.factor / 1e6
            self.last_tick = tick
            if 0 < cost <= MAX_FRAME_TIME_MS:
                rows.append(dict(frame_ms=cost, source_time_s=tick * self.factor / 1e9,
                                 source=FRAME_SOURCE, tid=tid))
        return rows


async def stream_ios_frames(dvt, emit):
    """Optional stream: an unavailable/busy kperf source does not stop CPU/FPS."""
    tap = None
    entered = False
    foreign_owner = False
    try:
        from pymobiledevice3.services.dvt.instruments.core_profile_session_tap import CoreProfileSessionTap, STACKSHOT_HEADER
        config = await asyncio.wait_for(CoreProfileSessionTap.get_time_config(dvt), 10)
        decoder = IOSFrameDecoder(config['numer'], config['denom'])
        tap = CoreProfileSessionTap(dvt, config, FRAME_FILTERS)
        # Restrict the trace to the 64-byte frame events used by the decoder.
        tap._config = {'rp': 10, 'tc': [{'kdf2': FRAME_FILTERS, 'tk': 3, 'uuid': tap.uuid.upper()}], 'ur': 500}
        await asyncio.wait_for(tap.__aenter__(), 12)
        entered = True
        emit({'type': 'frame_status', 'available': True, 'message': 'iOS 逐帧通道已启动，等待 CoreAnimation 事件'})
        while True:
            data = await tap._next_message()
            if not data:
                continue
            if data.startswith(b'bplist'):
                tap._raise_if_tap_start_failed(data)
                continue
            if data.startswith(STACKSHOT_HEADER):
                continue
            frames = decoder.decode(data)
            if frames:
                for row in frames:
                    row['stream_epoch'] = tap.uuid
                emit({'type': 'frames', 'frames': frames})
    except asyncio.CancelledError:
        raise
    except Exception as error:
        message = str(error).casefold()
        foreign_owner = 'kperf' in message and any(word in message for word in ('owned', 'busy', 'another'))
        emit({'type': 'frame_status', 'available': False,
              'message': 'iOS 逐帧通道不可用，FPS 回退计数器，Jank / Big Jank 保持缺失：' + str(error)[:350]})
    finally:
        if tap is not None and entered and not foreign_owner:
            with suppress(Exception):
                await asyncio.wait_for(tap.__aexit__(None, None, None), 3)
        channel = getattr(tap, '_channel', None)
        if channel is not None:
            with suppress(Exception):
                await asyncio.wait_for(channel.close(), 3)
