"""Download public model assets in verified HTTP ranges; retry/resume chunks."""
from __future__ import annotations
import concurrent.futures
import hashlib
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path


def ranged_download(url: str, target: Path, expected: str = "", workers: int = 8) -> None:
    def digest(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
        return h.hexdigest()
    if target.is_file() and (not expected or digest(target)==expected):return
    target.parent.mkdir(parents=True,exist_ok=True)
    headers={'User-Agent':'VoiceStudio/1.0','Accept':'application/octet-stream','Range':'bytes=0-0'}
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=30) as r:
                direct=r.url
                content_range=r.headers.get('Content-Range','')
                size=int(content_range.split('/')[-1]) if '/' in content_range else int(r.headers.get('Content-Length','0'))
                supports_range=r.status==206 or r.headers.get('Accept-Ranges','')=='bytes'
            break
        except Exception:
            if attempt==3:raise
            time.sleep(attempt+1)
    if not size or not supports_range:raise RuntimeError('Server does not support ranged download: '+target.name)
    chunks=target.with_suffix(target.suffix+'.chunks');chunks.mkdir(exist_ok=True)
    step=8*1024*1024
    def part(start):
        end=min(size,start+step)-1
        path=chunks/str(start)
        if path.is_file() and path.stat().st_size==end-start+1:return
        for attempt in range(5):
            try:
                request=urllib.request.Request(direct,headers={**headers,'Range':f'bytes={start}-{end}'})
                with urllib.request.urlopen(request,timeout=60) as r:
                    actual=r.headers.get('Content-Range','')
                    if actual!=f'bytes {start}-{end}/{size}':raise ValueError('Unexpected range: '+actual)
                    with path.with_suffix('.part').open('wb') as f:shutil.copyfileobj(r,f,1024*1024)
                if path.with_suffix('.part').stat().st_size!=end-start+1:raise ValueError('Incomplete range')
                os.replace(path.with_suffix('.part'),path)
                return
            except Exception:
                if attempt==4:raise
                time.sleep(1+attempt)
    offsets=list(range(0,size,step))
    print('RANGED',target.name,round(size/1024**2,1),'MiB',flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for i,future in enumerate(concurrent.futures.as_completed([pool.submit(part,start) for start in offsets]),1):
            future.result()
            if i%8==0 or i==len(offsets):print('PROGRESS',target.name,round(i/len(offsets)*100),'%',flush=True)
    output=target.with_suffix(target.suffix+'.assembling')
    with output.open('wb') as f:
        for start in offsets:
            with (chunks/str(start)).open('rb') as source:shutil.copyfileobj(source,f,1024*1024)
    if output.stat().st_size!=size or (expected and digest(output)!=expected):raise ValueError('Asset checksum mismatch: '+target.name)
    os.replace(output,target)
    print('VERIFIED',target.name,flush=True)


if __name__=='__main__':
    import sys
    ranged_download(sys.argv[1],Path(sys.argv[2]),sys.argv[3] if len(sys.argv)>3 else '')
