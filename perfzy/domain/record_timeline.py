"""One time mapping shared by samples, screenshots, scenes and raw evidence."""
from bisect import bisect_right
from copy import deepcopy
import math


def finite_time(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("时间必须是非负的有效数值")
    return value


class TimelineEdit:
    def __init__(self, duration, ranges):
        self.duration = finite_time(duration)
        merged = []
        spans = sorted((finite_time(row["start"]), finite_time(row["end"])) for row in ranges)
        for first, last in spans:
            if not 0 <= first < last <= self.duration + .001:
                raise ValueError("请选择采集记录范围内的有效时间段")
            last = min(last, self.duration)
            if last <= first:
                raise ValueError("请选择采集记录范围内的有效时间段")
            if merged and first <= merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], last)
            else:
                merged.append(dict(start=first, end=last))
        if not merged:
            raise ValueError("没有需要移除的时间段")
        self.ranges = merged
        self.starts = [row["start"] for row in merged]
        self.ends = [row["end"] for row in merged]
        self.prefix = [0.0]
        for row in merged:
            self.prefix.append(self.prefix[-1] + row["end"] - row["start"])
        self.removed_seconds = self.prefix[-1]
        self.edited_duration = max(0.0, self.duration - self.removed_seconds)
        if self.edited_duration <= 1e-9:
            raise ValueError("所选范围覆盖了整条记录，请至少保留一段数据")
        self.kept = []
        cursor = 0.0
        for row in merged:
            if row["start"] > cursor:
                self.kept.append(dict(start=cursor, end=row["start"]))
            cursor = row["end"]
        if cursor < self.duration:
            self.kept.append(dict(start=cursor, end=self.duration))

    def contains(self, value):
        value = finite_time(value)
        index = bisect_right(self.starts, value) - 1
        if index < 0:
            return False
        # A deletion through the recording end also removes its endpoint.
        return value < self.ends[index] or self.ends[index] == self.duration

    def map_time(self, value):
        value = min(finite_time(value), self.duration)
        index = bisect_right(self.starts, value) - 1
        if index < 0:
            return value
        removed = self.prefix[index] + min(value - self.starts[index], self.ends[index] - self.starts[index])
        return min(self.edited_duration, max(0.0, value - removed))

    def segment(self, value):
        return bisect_right(self.ends, value)

    def observation_interval(self, elapsed, interval):
        interval = finite_time(interval)
        index = bisect_right(self.starts, elapsed)
        if index < len(self.starts):
            interval = min(interval, max(0.0, self.starts[index] - elapsed))
        return interval

    def scenes(self, scenes):
        result = []
        for ordinal, scene in enumerate(scenes,1):
            first, last = self.map_time(scene["start"]), self.map_time(scene["end"])
            if last <= first:
                continue
            def clock(value):
                return f"{int(value)//60:02d}:{value%60:05.2f}"
            name = scene.get("name") or scene.get("title") or f"场景 {ordinal}"
            result.append(dict(scene, start=first, end=last, name=name,
                title=f"{name}  {clock(first)}—{clock(last)}"))
        return result

    def capture_settings(self, initial, changes):
        """Restore the state at each retained segment, including toggles cut out."""
        state = deepcopy(initial or {})
        events = sorted((deepcopy(row) for row in changes or []
                         if isinstance(row, dict) and isinstance(row.get("settings"), dict)),
                        key=lambda row: finite_time(row.get("elapsed", 0)))
        output, baseline, index = [], {}, 0
        for ordinal, segment in enumerate(self.kept):
            first, last = segment["start"], segment["end"]
            while index < len(events) and finite_time(events[index].get("elapsed", 0)) <= first:
                state.update(events[index]["settings"])
                index += 1
            if ordinal == 0:
                baseline = deepcopy(state)
            else:
                output.append(dict(elapsed=self.map_time(first), settings=deepcopy(state), reason="edit_splice"))
            while index < len(events):
                event = events[index]
                stamp = finite_time(event.get("elapsed", 0))
                if stamp > last or stamp == last and last != self.duration:
                    break
                output.append(dict(event, elapsed=self.map_time(stamp)))
                state.update(event["settings"])
                index += 1
        return baseline, output

    def cuts(self, previous, source_id):
        cuts = {}
        def add(cut):
            stamp = round(cut["time"], 9)
            if not 0 < stamp < self.edited_duration:
                return
            if stamp in cuts:
                cuts[stamp]["removed_seconds"] += cut.get("removed_seconds", 0)
                cuts[stamp]["edits"].extend(cut.get("edits") or [])
            else:
                cuts[stamp] = dict(cut, time=stamp, edits=list(cut.get("edits") or []))
        for cut in previous or []:
            stamp = finite_time(cut["time"])
            if not self.contains(stamp):
                add(dict(cut, time=self.map_time(stamp)))
        for row in self.ranges:
            add(dict(time=self.map_time(row["start"]), removed_seconds=row["end"]-row["start"],
                     edits=[dict(source_id=source_id, start=row["start"], end=row["end"])]))
        return [cuts[key] for key in sorted(cuts)]
