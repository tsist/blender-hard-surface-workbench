"""Thin GUI adapter. Registration/interactive testing requires exact-package approval.
No import-time registration; no live scene modeling or implicit current-file overwrite.
"""
import json,copy,subprocess,sys,time,os,uuid
from pathlib import Path
import bpy
from .planner import plan_request
from .contract import strict_loads
from .core import loaded_design,inspect_scene,CoreError,save_json_new,file_record

def prepare_submission(request,snapshot_path,request_path):
    """Export a new saved snapshot and strict request, preserving the current file.
    The caller submits these through the same host CLI used in background execution.
    """
    request=copy.deepcopy(request);plan=plan_request(request,saved_state=loaded_design())
    path=Path(snapshot_path).resolve()
    if path.exists():raise CoreError('OUTPUT_COLLISION','GUI snapshot must be a new path')
    if Path(request_path).resolve().exists():raise CoreError('OUTPUT_COLLISION','GUI request must be a new path')
    # copy=True saves a snapshot without moving current GUI filepath or saving over it.
    current_file=bpy.data.filepath
    bpy.ops.wm.save_as_mainfile(filepath=str(path),check_existing=False,copy=True)
    if bpy.data.filepath!=current_file:raise CoreError('GUI_FILEPATH_CHANGED','Snapshot unexpectedly changed active GUI filepath')
    actual=file_record(path);unit=request['params']['source']['length_unit']
    request['params']['source']={'kind':'saved_blend','file':actual['file'],'expected_sha256':actual['sha256'],'bytes':actual['bytes'],'length_unit':unit}
    # Preserve design expected_revision; the snapshot contains the exact same managed state.
    plan=plan_request(request,saved_state=loaded_design())
    save_json_new(request_path,request)
    return {'snapshot':str(path),'request':str(Path(request_path).resolve()),'plan':plan,'gui_unsaved_state':'captured_copy','current_filepath_preserved':True}

class HostBridge:
    """Static bridge to the same public CLI. No shell, install, or active-file replacement.
    All executable paths are explicit configuration; jobs_dir is a dedicated new root.
    Background submit returns quickly; poll never resubmits lost requests.
    """
    def __init__(self,host_python,host_cli,blender,jobs_dir):
        self.python=Path(host_python).resolve();self.cli=Path(host_cli).resolve();self.blender=Path(blender).resolve();self.jobs=Path(jobs_dir).resolve()
        for path in (self.python,self.cli,self.blender):
            if not path.is_file():raise CoreError('GUI_BRIDGE_PATH','Configured executable/CLI path missing',{'path':str(path)})
        self.processes={}
    def _call(self,args,timeout=30):
        prefix=self.jobs.parent/('unused-ui-pycache-'+str(uuid.uuid4()))
        if prefix.exists():raise CoreError('GUI_CACHE_PREFIX','Fresh cache prefix unexpectedly exists')
        env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',PYTHONPYCACHEPREFIX=str(prefix),PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        argv=[str(self.python),'-B','-X','pycache_prefix='+str(prefix),str(self.cli),'--jobs-dir',str(self.jobs),'--blender',str(self.blender),'--compact',*args]
        result=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,check=False,shell=False,env=env)
        try:payload=json.loads(result.stdout)
        except ValueError:raise CoreError('GUI_BRIDGE_OUTPUT','CLI returned malformed receipt',{'returncode':result.returncode,'stderr':result.stderr[-2000:]})
        if result.returncode:raise CoreError('GUI_BRIDGE_FAILED','CLI command failed',payload)
        return payload
    def submit(self,request_path):
        path=Path(request_path).resolve()
        if not path.is_file():raise CoreError('GUI_REQUEST_MISSING','Snapshot request file missing')
        return self._call(['--async','hardsurface','run','--request',str(path)])
    def status(self,job_id):return self._call(['job','status',job_id])
    def cancel(self,job_id):return self._call(['job','cancel',job_id])
    def report(self,job_id):return self._call(['hardsurface','report',job_id])
    def preview_paths(self,job_id):
        # Only validated artifacts within this configured jobs root; UI can display them.
        report=self.report(job_id);result=[]
        def walk(value):
            if isinstance(value,dict):
                if isinstance(value.get('file'),str):
                    p=Path(value['file']).resolve()
                    if p.suffix.lower() in ('.png','.jpg','.jpeg') and p.is_relative_to(self.jobs) and p.is_file():result.append(str(p))
                for item in value.values():walk(item)
            elif isinstance(value,list):
                for item in value:walk(item)
        walk(report);return sorted(set(result))
    def load_preview(self,path):
        p=Path(path).resolve()
        if not p.is_relative_to(self.jobs) or not p.is_file() or p.suffix.lower() not in ('.png','.jpg','.jpeg'):raise CoreError('GUI_PREVIEW_PATH','Preview path is outside job outputs')
        if p.stat().st_size>2*1024*1024:raise CoreError('GUI_PREVIEW_BYTES','Preview exceeds bound')
        image=bpy.data.images.load(str(p),check_existing=True)
        width,height=image.size[:]
        if width<1 or height<1 or width>2048 or height>2048:raise CoreError('GUI_PREVIEW_DIMENSIONS','Decoded preview exceeds 2048px or is invalid; image is not displayed',{'width':width,'height':height})
        return image

class HSW_OT_validate_request(bpy.types.Operator):
    bl_idname='hsw.validate_request';bl_label='Validate Work Unit';bl_description='Validate the named request Text through the shared domain planner'
    def execute(self,context):
        try:
            text=bpy.data.texts.get(context.scene.hsw_request_text)
            if text is None:raise ValueError('Select a request Text datablock')
            plan=plan_request(strict_loads(text.as_string()),saved_state=loaded_design())
            self.report({'INFO'},'Validated %d bounded steps; no model changed'%len(plan['steps']));return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}

class HSW_PT_workbench(bpy.types.Panel):
    bl_label='Hard Surface Workbench';bl_idname='HSW_PT_workbench';bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Hard Surface'
    def draw(self,context):
        layout=self.layout;layout.prop(context.scene,'hsw_request_text');layout.operator('hsw.validate_request')
        layout.label(text='Candidate-only execution via host CLI')
        layout.label(text='Reference approval required before production')

CLASSES=(HSW_OT_validate_request,HSW_PT_workbench)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Scene.hsw_request_text=bpy.props.StringProperty(name='Request Text')
def unregister():
    if hasattr(bpy.types.Scene,'hsw_request_text'):del bpy.types.Scene.hsw_request_text
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
