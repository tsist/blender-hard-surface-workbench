# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual Linux guard reuse and original/snapshot evidence are separate."""
from __future__ import annotations
from pathlib import Path
import sys,time,shutil
from .io import RuntimeFailure,copy_verified,descriptor,verify_descriptor,checked_path,atomic_json
ROOT=Path(__file__).resolve().parents[1]

def guard_class():
    from .linux_file_guard import LinuxFileGuard
    return LinuxFileGuard

class ProtectedInputs:
    def __init__(self,files):self.files=files;self.guards=[];self.started=None;self.ended=None
    def __enter__(self):
        self.started=time.time()
        try:
            for f in self.files:
                verify_descriptor(f,expected=False);g=guard_class()(f['file']);self.guards.append(g);g.check()
        except Exception:
            for g in self.guards:g.close(validate=False)
            raise
        return self
    def check(self):
        for g in self.guards:g.check()
    def __exit__(self,typ,val,tb):
        first=None
        for g in reversed(self.guards):
            try:g.close(validate=typ is None)
            except Exception as exc:first=first or exc
        self.ended=time.time()
        if first:raise first
    def report(self):
        return {'guard_type':'linux_read_lease_inotify_pinned_directory','start':self.started,'end':self.ended,'files':self.files,'accepted':self.ended is not None,'limits':'Namespace changes are detected; not a security sandbox or overlay original immutability proof'}

def stage_inputs(params,stage_dir,*,disk_budget):
    root=checked_path(stage_dir,exists=False)
    if not str(root).startswith('/tmp/') or root.exists():raise RuntimeFailure('STAGE_PATH_REFUSED','Fresh /tmp staging directory required')
    need=sum(r.get('bytes',Path(r['file']).stat().st_size) for r in params['resources'])
    if params['source']['kind']=='saved_blend':need+=params['source'].get('bytes',Path(params['source']['file']).stat().st_size)
    free=shutil.disk_usage(root.parent if root.parent.exists() else '/tmp').free
    if need*3+16*1024*1024>free or need>disk_budget:raise RuntimeFailure('BUDGET_EXCEEDED','Insufficient staging and recovery space',needed=need,free=free)
    root.mkdir(parents=True)
    observations=[];resources=[];mapping=[];source=None
    if params['source']['kind']=='saved_blend':
        source,obs=copy_verified(params['source'],root/'source_original.blend',limit_bytes=disk_budget);observations.append(obs)
    for n,r in enumerate(params['resources']):
        copy,obs=copy_verified(r,root/'resources'/f'{n:04d}_{Path(r["file"]).name}',limit_bytes=disk_budget-need+int(r.get('bytes',Path(r['file']).stat().st_size)))
        resources.append(copy);observations.append(obs);mapping.append({'original':r['file'],'staged':copy['file'],'kind':r.get('kind','file')})
    report={'source':source,'resources':resources,'mapping':mapping,'original_observations':observations,'original_source_observed':{'status':'not_applicable'} if source is None else observations[0]}
    atomic_json(root/'stage.json',report)
    return report

def recheck_originals(params,observations):
    descriptors=list(params['resources'])
    if params['source']['kind']=='saved_blend':descriptors.insert(0,params['source'])
    results=[]
    for d in descriptors:results.append({'at':time.time(),**verify_descriptor(d),'written_by_this_tool':False,'continuous_immutability_proven':False})
    return results
