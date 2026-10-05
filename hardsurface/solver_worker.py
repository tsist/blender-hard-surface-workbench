"""One-shot native worker. Never import this module to solve in Blender.

All generated helper constraints map back to their originating design IDs.
No reference dimension is submitted as a driving equation.
"""
from __future__ import annotations
import copy
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import platform
from pathlib import Path
import resource
import sys
import time
import zipfile

from .solver import (SolverError,PROTOCOL_VERSION,WHEEL_SHA256,MAX_MESSAGE_BYTES,
                     canonical,digest,strict_json,normalize_result,failure,estimate_native_expansion)
from .sketch import entity_map,point,line,curve,foot,arc_point,sub,add,mul,dist,verify_constraints,SketchError


def verify_package(package_dir,wheel_path):
    wheel=Path(wheel_path); root=Path(package_dir).resolve()
    if hashlib.sha256(wheel.read_bytes()).hexdigest()!=WHEEL_SHA256:
        raise SolverError('backend_identity_mismatch','Worker observed a different wheel')
    matched=0;expected_files=set();runtime_manifest=[]
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir() or '.dist-info/' in info.filename:continue
            expected_files.add(info.filename)
            path=root/info.filename
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                raise SolverError('backend_identity_mismatch','Installed wheel file missing, symlinked, or outside package directory')
            observed_sha=hashlib.sha256(path.read_bytes()).hexdigest()
            if observed_sha!=hashlib.sha256(archive.read(info)).hexdigest():
                raise SolverError('backend_identity_mismatch','Installed wheel differs: '+info.filename)
            runtime_manifest.append([info.filename,observed_sha])
            matched+=1
    expected_dirs={str(parent) for name in expected_files for parent in Path(name).parents if str(parent)!='.'}
    count=0
    for installed in root.rglob('*'):
        count+=1
        if count>10000:raise SolverError('backend_identity_mismatch','Installed runtime tree exceeds scan budget')
        relative=installed.relative_to(root).as_posix()
        if installed.is_symlink():raise SolverError('backend_identity_mismatch','Symlinked package member')
        if any(part.endswith('.dist-info') for part in installed.relative_to(root).parts):continue
        if '__pycache__' in installed.relative_to(root).parts:raise SolverError('backend_identity_mismatch','Unverified bytecode cache in pinned runtime')
        if installed.is_dir():
            if relative not in expected_dirs:raise SolverError('backend_identity_mismatch','Unexpected runtime directory: '+relative)
            continue
        if not installed.is_file():raise SolverError('backend_identity_mismatch','Unexpected runtime special file')
        if relative not in expected_files:raise SolverError('backend_identity_mismatch','Unexpected runtime file: '+relative)
    if not matched:raise SolverError('backend_identity_mismatch','Wheel contains no runtime files')
    return {'runtime_tree_sha256':digest(sorted(runtime_manifest)),'runtime_files':matched,'package_dir':str(root)}


class SlvsBuilder:
    def __init__(self,slvs,sketch,dimensions):
        self.s=slvs;self.sketch=copy.deepcopy(sketch);self.es=entity_map(self.sketch);self.dimensions=dimensions
        self.handles={};self.entities={};self.radius_entities={};self.arc_endpoints={};self.helper_counter=0
        self.s.clear_sketch()
        self.normal=self.s.add_normal_3d(1,1.,0.,0.,0.)
        origin=self.s.add_point_3d(1,0.,0.,0.)
        self.wp=self.s.add_workplane(1,origin,self.normal)
        p0=self.s.add_point_2d(1,0.,0.,self.wp);px=self.s.add_point_2d(1,1.,0.,self.wp)
        self.xaxis=self.s.add_line_2d(1,p0,px,self.wp)
        for e in sorted(self.es.values(),key=lambda x:x['id']):
            if e['kind']=='point2d':self.entities[e['id']]=self.s.add_point_2d(2,*point(self.es,e['id']),self.wp)
        for e in sorted(self.es.values(),key=lambda x:x['id']):
            n=e['id'];kind=e['kind']
            if kind=='line_segment2d':
                line(self.es,n)
                self.entities[n]=self.s.add_line_2d(2,self.entities[e['start']],self.entities[e['end']],self.wp)
            elif kind=='circle2d':
                _,r=curve(self.es,n);rad=self.s.add_distance(2,r,self.wp);self.radius_entities[n]=rad
                self.entities[n]=self.s.add_circle(2,self.normal,self.entities[e['center']],rad,self.wp)
            elif kind=='arc2d':
                curve(self.es,n)
                start=self.s.add_point_2d(2,*arc_point(self.es,n,e['start_angle_seed']),self.wp)
                end=self.s.add_point_2d(2,*arc_point(self.es,n,e['start_angle_seed']+e['sweep_seed']),self.wp)
                self.arc_endpoints[n]=(start,end)
                native_start,native_end=(start,end) if e['sweep_seed']>0 else (end,start)
                self.entities[n]=self.s.add_arc(2,self.normal,self.entities[e['center']],native_start,native_end,self.wp)
            elif kind!='point2d':raise SolverError('unsupported_entity',kind)
        for c in sorted(self.sketch.get('constraints',[]),key=lambda x:x['id']):
            if c.get('mode','driving')=='reference':continue
            self.add_constraint(c)

    def record(self,c,handle):
        if not isinstance(handle,dict) or type(handle.get('h')) is not int:raise SolverError('protocol_error','slvs constraint has no handle')
        self.handles[handle['h']]=c['id'];return handle

    def constrain(self,c,kind,value=0.,**kw):
        # Shipped .pyi says p1/p2; qualified 3.2 Cython runtime uses ptA/ptB.
        names={'p1':'ptA','p2':'ptB','e1':'entityA','e2':'entityB','e3':'entityC','e4':'entityD'}
        actual={names.get(key,key):value for key,value in kw.items()}
        return self.record(c,self.s.add_constraint(2,getattr(self.s.ConstraintType,kind),self.wp,float(value),**actual))

    def coincident(self,c,a,b):return self.record(c,self.s.coincident(2,a,b,self.wp))
    def newpoint(self,p):return self.s.add_point_2d(2,*p,self.wp)
    def newline(self,a,b):return self.s.add_line_2d(2,a,b,self.wp)
    def center(self,n):return self.entities[self.es[n]['center']]
    def value(self,c):return self.dimensions[c['dimension']]*(180/math.pi if c['type'] in ('angle','arc_start_angle','arc_sweep') else 1000.)

    def axis_distance(self,c,a,b,axis,value,pa,pb):
        # Project onto a coordinate axis with an auxiliary point. Signed branch
        # is independently verified; no undocumented native projection API.
        if abs(value)<=1e-12:
            self.constrain(c,'VERTICAL' if axis=='X' else 'HORIZONTAL',p1=a,p2=b);return
        q=self.newpoint((pa[0]+value,pa[1]) if axis=='X' else (pa[0],pa[1]+value))
        self.constrain(c,'HORIZONTAL' if axis=='X' else 'VERTICAL',p1=a,p2=q)
        self.constrain(c,'VERTICAL' if axis=='X' else 'HORIZONTAL',p1=q,p2=b)
        self.record(c,self.s.distance(2,a,q,abs(value),self.wp))

    def add_constraint(self,c):
        t=c['type'];get=lambda k:self.entities[c[k]]
        if 'dimension' in c:
            v=self.value(c)
            if t in ('radius','diameter') and v<=0:raise SolverError('invalid_dimension','Radius and diameter must be positive')
            if t in ('distance','point_line_distance') and v<0:raise SolverError('invalid_dimension','Unsigned length dimensions cannot be negative')
            if t=='arc_sweep' and not 0<abs(v)<360:raise SolverError('invalid_dimension','Arc sweep must be nonzero and strictly below one turn')
            if t=='angle' and not c.get('directed',True) and not 0<=v<=180:raise SolverError('invalid_dimension','Unsigned line angle must lie in [0,180] degrees')
        if t=='point_fixed2d':
            fixed=self.s.add_point_2d(1,*c['at'],self.wp);self.coincident(c,get('point'),fixed)
        elif t=='coincident':self.coincident(c,get('a'),get('b'))
        elif t=='arc_endpoint_coincident':self.coincident(c,self.arc_endpoints[c['arc']][0 if c['endpoint']=='start' else 1],get('point'))
        elif t in ('horizontal','vertical'):self.record(c,getattr(self.s,t)(2,get('line'),self.wp))
        elif t in ('parallel','perpendicular','equal_length','equal_radius'):
            name='equal' if t.startswith('equal_') else t
            self.record(c,getattr(self.s,name)(2,get('a'),get('b'),self.wp))
        elif t=='concentric':self.coincident(c,self.center(c['a']),self.center(c['b']))
        elif t=='distance':self.record(c,self.s.distance(2,get('from'),get('to'),self.value(c),self.wp))
        elif t=='axis_distance2d':self.axis_distance(c,get('from'),get('to'),c['axis'],self.value(c),point(self.es,c['from']),point(self.es,c['to']))
        elif t in ('radius','diameter'):
            self.record(c,self.s.diameter(2,get('circle'),self.value(c)*(2 if t=='radius' else 1)))
        elif t=='angle':
            self.record(c,self.s.angle(2,get('a'),get('b'),abs((self.value(c)+180)%360-180) if c.get('directed',True) else self.value(c),self.wp))
        elif t in ('arc_start_angle','arc_sweep'):
            n=c['arc'];a,b=self.arc_endpoints[n];center=self.center(n)
            ra=self.newline(center,a);rb=self.newline(center,b)
            first,second=(self.xaxis,ra) if t=='arc_start_angle' else (ra,rb)
            value=abs(self.value(c))%360;value=min(value,360-value)
            # Parallel/perpendicular avoid the singular cos(theta) equation at
            # axis-aligned endpoint seeds; exact branch remains independently checked.
            if min(value,180-value)<1e-10:self.record(c,self.s.parallel(2,first,second,self.wp))
            elif abs(value-90)<1e-10:self.record(c,self.s.perpendicular(2,first,second,self.wp))
            else:self.record(c,self.s.angle(2,first,second,value,self.wp))
        elif t=='point_line_distance':
            if self.value(c)==0:
                self.coincident(c,get('point'),get('line'));return
            # Tangency-style perpendicular foot avoids undocumented signed-distance convention.
            p=point(self.es,c['point']);a,b=line(self.es,c['line']);f,_,_=foot(p,a,b)
            q=self.newpoint(f);rline=self.newline(q,get('point'))
            self.coincident(c,q,get('line'))
            self.record(c,self.s.perpendicular(2,rline,get('line'),self.wp))
            self.record(c,self.s.distance(2,q,get('point'),self.value(c),self.wp))
        elif t=='equal_spacing':
            names=c['points'];axis=c['axis'];projections=[]
            index=0 if axis=='X' else 1
            if any(abs(point(self.es,a)[index]-point(self.es,b)[index])<1e-12 for a,b in zip(names,names[1:])):raise SolverError('degenerate_seed','Equal-spacing projection has a zero-length seed segment')
            # Projection entities carry no extra design DOF: orthogonal position
            # fixed at zero, coordinate aligned with the design point.
            for name in names:
                p=point(self.es,name);q=self.newpoint((p[0],0.) if axis=='X' else (0.,p[1]))
                self.constrain(c,'VERTICAL' if axis=='X' else 'HORIZONTAL',p1=self.entities[name],p2=q)
                fixed=self.s.add_point_2d(1,0.,0.,self.wp)
                self.constrain(c,'HORIZONTAL' if axis=='X' else 'VERTICAL',p1=fixed,p2=q)
                projections.append(q)
            lines=[self.newline(a,b) for a,b in zip(projections,projections[1:])]
            for ln in lines[1:]:self.record(c,self.s.equal(2,lines[0],ln,self.wp))
        elif t in ('tangent_line_circle','tangent_line_arc'):
            n=c.get('circle',c.get('arc'));ctr,r=curve(self.es,n);a,b=line(self.es,c['line']);f,_,_=foot(ctr,a,b)
            if dist(f,ctr)<1e-10:
                v=sub(b,a);s=1 if c['side']=='left' else -1;length=dist(a,b)
                f=add(ctr,(s*v[1]/length*r,-s*v[0]/length*r))
            q=self.newpoint(f);radial=self.newline(self.center(n),q)
            self.coincident(c,q,get('line'));self.coincident(c,q,self.entities[n])
            self.record(c,self.s.perpendicular(2,radial,get('line'),self.wp))
        elif t in ('tangent_circle_circle','tangent_arc_arc'):
            a,r=curve(self.es,c['a']);b,s=curve(self.es,c['b']);d=dist(a,b)
            if d<=1e-12:raise SolverError('degenerate_geometry','Concentric circles cannot define tangent branch')
            sign=1 if c['branch']=='external' or r>s else -1
            q=self.newpoint(add(a,mul(sub(b,a),r/d*sign)))
            self.coincident(c,q,get('a'));self.coincident(c,q,get('b'))
            ra=self.newline(self.center(c['a']),q);rb=self.newline(self.center(c['b']),q)
            self.record(c,self.s.parallel(2,ra,rb,self.wp))
        else:raise SolverError('unsupported_constraint',t)

    def read_point(self,entity):return [self.s.get_param_value(h) for h in entity['param'][:2]]

    def result_sketch(self):
        out=copy.deepcopy(self.sketch);es=entity_map(out)
        for n,e in es.items():
            if e['kind']=='point2d':e['seed']=self.read_point(self.entities[n])
            elif e['kind']=='circle2d':e['radius_seed']=self.s.get_param_value(self.radius_entities[n]['param'][0])
        for n,e in es.items():
            if e['kind']=='arc2d':
                a,b=[self.read_point(p) for p in self.arc_endpoints[n]];center=point(es,e['center'])
                r1,r2=dist(a,center),dist(b,center)
                if abs(r1-r2)>1e-6:raise SolverError('residual_verification_failed','Native arc endpoint radii disagree')
                start=math.degrees(math.atan2(a[1]-center[1],a[0]-center[0]));end=math.degrees(math.atan2(b[1]-center[1],b[0]-center[0]))
                sweep=(end-start)%360 if e['sweep_seed']>0 else -((start-end)%360)
                e.update({'radius_seed':r1,'start_angle_seed':start,'sweep_seed':sweep})
        return out


def native_solve(slvs,request):
    builder=SlvsBuilder(slvs,request['sketch'],request['dimension_values'])
    started=time.monotonic();raw=slvs.solve_sketch(2,request['phase']=='diagnostics');result=normalize_result(raw,builder.handles)
    first_seconds=time.monotonic()-started
    detail_seconds=first_seconds if request['phase']=='diagnostics' else None
    result.update({'accepted':False,'status':result['backend_status'],'backend':{'name':'slvs','version':'3.2','wheel_sha256':WHEEL_SHA256},
                   'verification':{'status':'not_run'},'profiles':{},'solve_seconds':first_seconds,'diagnostic_seconds':detail_seconds})
    if result['backend_result_code'] in (0,4):result['solved_sketch']=builder.result_sketch()
    return result


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if len(argv)!=4:return 64
    request_file,output_file,package_dir,wheel_file=map(Path,argv)
    # Resource bounds apply to the owned worker only; no global configuration.
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(8*1024*1024,8*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(600,600))
    request=strict_json(request_file.read_bytes(),MAX_MESSAGE_BYTES)
    input_sha256=digest(request)
    expected={'protocol_version','request_id','job_id','sketch','dimension_values','backend','diagnostics','phase'}
    if not isinstance(request,dict) or set(request)!=expected or request['protocol_version']!=PROTOCOL_VERSION:return 65
    try:
        if request['backend']!={'name':'slvs','version':'3.2','wheel_sha256':WHEEL_SHA256}:raise SolverError('backend_identity_mismatch','Unexpected backend identity')
        from .contract import validate_sketch
        local=copy.deepcopy(request['sketch']);local['depends_on']=[]
        validate_sketch(local)
        expansion=estimate_native_expansion(request['sketch'])
        verify_constraints(request['sketch'],request['dimension_values'])
        if request['phase'] not in ('primary','diagnostics'):raise SolverError('protocol_error','Unknown execution phase')
        package_identity=verify_package(package_dir,wheel_file)
        sys.dont_write_bytecode=True
        sys.path.insert(0,package_identity['package_dir'])
        try:slvs=importlib.import_module('slvs')
        except (ImportError,OSError) as e:raise SolverError('abi_import_failure',str(e)) from e
        if not Path(slvs.__file__).resolve().is_relative_to(package_dir.resolve()):raise SolverError('backend_identity_mismatch','slvs imported outside pinned directory')
        result=native_solve(slvs,request)
        result['expansion_estimate']=expansion
        after_identity=verify_package(Path(package_identity['package_dir']),wheel_file)
        if after_identity!=package_identity:raise SolverError('backend_identity_mismatch','Runtime package changed during solve')
        executable=Path(sys.executable).resolve()
        result['backend_identity']={**result['backend'],**package_identity,'python_executable':str(executable),'python_sha256':hashlib.sha256(executable.read_bytes()).hexdigest(),'python_version':sys.version,'machine':platform.machine(),'libc':list(platform.libc_ver()),'module_file':str(Path(slvs.__file__).resolve()),'implementation_sha256':digest({name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ('solver.py','solver_worker.py','sketch.py')})}
    except (SolverError,SketchError) as e:result=failure(e.code,str(e))
    except Exception as e:result=failure('worker_implementation_error',type(e).__name__+': '+str(e))
    envelope={'protocol_version':PROTOCOL_VERSION,'request_id':request['request_id'],'request_sha256':input_sha256,'result':result}
    data=canonical(envelope);temporary=output_file.with_suffix('.tmp')
    with temporary.open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,output_file)
    return 0


if __name__=='__main__':raise SystemExit(main())
