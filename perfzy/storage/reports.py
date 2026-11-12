"""Local HTML report inventory; read bounded metadata, never execute HTML."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re

HEADER_BYTES = 1024 * 1024
HTML_SUFFIXES = {'.html', '.htm'}
_DATA_MARKER = '<script id="reportData" type="application/json">'
_RESERVED_NAMES = {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}


def report_path(root: Path, identifier: str) -> Path:
    identifier=str(identifier)
    if not identifier or identifier in {'.','..'} or any(char in identifier for char in '/\\\0:'):
        raise ValueError('报告文件名无效')
    folder=(Path(root)/'exports').resolve()
    path=folder/identifier
    if path.suffix.casefold() not in HTML_SUFFIXES or path.resolve().parent!=folder:
        raise ValueError('只能管理本地报告目录中的 HTML 文件')
    if not path.is_file():raise FileNotFoundError('报告文件不存在，可能已被移动或删除')
    return path


def _text(value,limit=400):
    return value.strip()[:limit] if isinstance(value,str) else ''


def _read_metadata(path: Path) -> dict:
    with path.open('rb') as source:
        header=source.read(HEADER_BYTES).decode('utf-8-sig',errors='replace')
    metadata={}
    marker=header.find(_DATA_MARKER)
    if marker>=0:
        decoder=json.JSONDecoder()
        data=header[marker+len(_DATA_MARKER):].lstrip()
        position=1
        try:
            if not data.startswith('{'):raise ValueError('not an object')
            while position<len(data):
                while position<len(data) and data[position] in ' \r\n\t,':position+=1
                if position>=len(data) or data[position]=='}':break
                key,position=decoder.raw_decode(data,position)
                while position<len(data) and data[position].isspace():position+=1
                if data[position]!=':':break
                position+=1
                while position<len(data) and data[position].isspace():position+=1
                # Samples and embedded pictures can be hundreds of megabytes.
                if key in {'samples','screenshots','app_icon','metrics','groups'}:break
                value,position=decoder.raw_decode(data,position)
                if key in {'id','name','target','device','started_at','source_revision'}:metadata[key]=value
        except (ValueError,IndexError,RecursionError):
            pass  # Keep already-decoded fields, with honest filename fallbacks.
        metadata['kind']='capture'
    elif re.search(r'<body\b[^>]*class=["\'][^"\']*\breport-comparison\b',header,re.I):
        metadata['kind']='comparison'
    else:
        metadata['kind']='html'
    return metadata


def format_file_size(value: int) -> str:
    number=float(value)
    for unit in ('B','KB','MB','GB'):
        if number<1024 or unit=='GB':return f'{int(number)} {unit}' if unit=='B' else f'{number:.1f} {unit}'
        number/=1024



def link_report_source(row: dict, source: dict) -> dict:
    revision = source.get("sourceRevision")
    exported = row.get("reportSourceRevision")
    changed = False
    if revision:
        if exported:
            changed = exported != revision
        else:
            file_ns = row.get("reportModifiedNs")
            changed = bool(file_ns and source.get("sourceModifiedNs", 0) > file_ns)
    return dict(row, sourceAvailable=bool(source), sourceChanged=changed)


def load_html_reports(root: Path, *, cache=None, sessions=()):
    folder=Path(root)/'exports'
    previous=cache or {}
    refreshed={}
    sources={str(item.get('id')):item for item in sessions if isinstance(item,dict)}
    rows=[]
    if not folder.is_dir():return rows,refreshed
    for path in folder.iterdir():
        if path.suffix.casefold() not in HTML_SUFFIXES:continue
        try:
            path=report_path(root,path.name)
            stat=path.stat()
            signature=(stat.st_mtime_ns,stat.st_size)
            saved=previous.get(path.name)
            metadata=saved['metadata'] if saved and tuple(saved['signature'])==signature else _read_metadata(path)
            refreshed[path.name]={'signature':signature,'metadata':metadata}
            source_id=_text(metadata.get('id'))
            source=sources.get(source_id,{})
            target=metadata.get('target') if isinstance(metadata.get('target'),dict) else {}
            device=metadata.get('device') if isinstance(metadata.get('device'),dict) else {}
            application=_text(target.get('name') or target.get('label')) or _text(source.get('application'))
            device_name=_text(device.get('name') or device.get('label') or device.get('model')) or _text(source.get('deviceName') or source.get('device'))
            model=_text(device.get('model')) or _text(source.get('deviceModel'))
            os_version=_text(device.get('os_version'))
            changed=datetime.fromtimestamp(stat.st_mtime).astimezone()
            row = {'id':path.name,'filename':path.name,'title':path.stem,
                         'application':application,'device':device_name,'deviceModel':model,
                         'deviceOS':os_version,'kind':metadata.get('kind','html'),
                         'kindLabel':{'capture':'采集报告','comparison':'对比报告'}.get(metadata.get('kind'),'HTML 文件'),
                         'updatedAt':changed.strftime('%Y-%m-%d %H:%M:%S'),
                         'sortTimestamp':stat.st_mtime,'sizeBytes':stat.st_size,'size':format_file_size(stat.st_size),
                         'sourceSessionId':source_id,'reportSourceRevision':_text(metadata.get('source_revision')),
                         'reportModifiedNs':stat.st_mtime_ns}
            rows.append(link_report_source(row, source))
        except (OSError,ValueError):
            continue
    rows.sort(key=lambda row:(-row['sortTimestamp'],row['filename'].casefold()))
    return rows,refreshed


def rename_html_report(root: Path, identifier: str, title: str) -> Path:
    original=report_path(root,identifier)
    title=str(title).strip()
    if title.casefold().endswith(original.suffix.casefold()):
        title=title[:-len(original.suffix)]
    if (not title or len(title)>160 or title[-1] in '. ' or title in {'.','..'}
            or any(ord(char)<32 or char in '<>:"/\\|?*' for char in title)
            or title.split('.')[0].upper() in _RESERVED_NAMES):
        raise ValueError('请输入有效文件名，不能包含系统保留字符或名称')
    destination=original.with_name(title+original.suffix)
    if destination.exists() and destination.resolve()!=original.resolve():
        raise ValueError('已经有同名报告，请换一个文件名')
    if destination!=original:original.rename(destination)
    return destination


def delete_html_report(root: Path, identifier: str) -> None:
    report_path(root,identifier).unlink()
