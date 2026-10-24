"""Hardware detection is separate from verified training support."""
from __future__ import annotations
import ctypes
import json
import subprocess
import sys
import time
import psutil


def resource_snapshot() -> dict:
    memory = psutil.virtual_memory()
    result = {'available_ram_mb': int(memory.available / 2**20), 'total_ram_mb': int(memory.total / 2**20),
              'cpu_logical': psutil.cpu_count(), 'gpu': None}
    try:
        text = subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total,memory.used','--format=csv,noheader,nounits'], timeout=5, text=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        name, total, used = text.splitlines()[0].rsplit(',',2)
        result['gpu'] = {'name':name.strip(),'total_mb':int(total),'used_mb':int(used),'available_mb':int(total)-int(used)}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        pass
    return result


def nvidia_opencl_device() -> dict | None:
    if sys.platform != 'win32':
        return None
    try:
        cl = ctypes.WinDLL('OpenCL.dll')
        pointer = ctypes.c_void_p
        uint = ctypes.c_uint
        size = ctypes.c_size_t
        cl.clGetPlatformIDs.argtypes = [uint, ctypes.POINTER(pointer), ctypes.POINTER(uint)]
        cl.clGetDeviceIDs.argtypes = [pointer, ctypes.c_ulonglong, uint, ctypes.POINTER(pointer), ctypes.POINTER(uint)]
        cl.clGetDeviceInfo.argtypes = [pointer,uint,size,pointer,ctypes.POINTER(size)]
        count = uint()
        if cl.clGetPlatformIDs(0,None,ctypes.byref(count)) != 0:
            return None
        platforms = (pointer * count.value)()
        cl.clGetPlatformIDs(count,platforms,None)
        for platform_id, platform in enumerate(platforms):
            device_count = uint()
            if cl.clGetDeviceIDs(platform,0xFFFFFFFF,0,None,ctypes.byref(device_count)) != 0:
                continue
            devices = (pointer * device_count.value)()
            cl.clGetDeviceIDs(platform,0xFFFFFFFF,device_count,devices,None)
            for device_id, device in enumerate(devices):
                buffer = ctypes.create_string_buffer(1024)
                if cl.clGetDeviceInfo(device,0x102B,1024,buffer,None) != 0:
                    continue
                name = buffer.value.decode(errors='replace')
                if 'NVIDIA' in name.upper() or 'RTX' in name.upper():
                    return {'gpu_platform_id':platform_id,'gpu_device_id':device_id,'name':name}
    except (OSError, ValueError, AttributeError):
        return None
    return None


def capability_probe() -> dict:
    import lightgbm as lgb
    import numpy as np
    report = {'hardware':resource_snapshot(),'opencl':nvidia_opencl_device(),'lightgbm_version':lgb.__version__,
              'gpu_verified':False,'synthetic_only':True}
    rng = np.random.default_rng(20260919)
    x = rng.normal(size=(4096,24)).astype('float32')
    y = x[:,0] + 0.5*x[:,1] + rng.normal(scale=0.1,size=len(x))
    for device in ('cpu','gpu'):
        if device == 'gpu' and report['opencl'] is None:
            report['gpu_error'] = 'NVIDIA OpenCL device not found'
            continue
        parameters = {'objective':'regression','verbosity':-1,'num_threads':2,'seed':42,'max_bin':63,'device_type':device}
        if device == 'gpu':
            parameters.update({k:report['opencl'][k] for k in ('gpu_platform_id','gpu_device_id')})
        start = time.perf_counter()
        try:
            model = lgb.train(parameters,lgb.Dataset(x,label=y),num_boost_round=5)
            prediction = model.predict(x,num_threads=2)
            report[device] = {'seconds':round(time.perf_counter()-start,4),'finite_predictions':bool(np.isfinite(prediction).all())}
            if device == 'gpu': report['gpu_verified'] = report[device]['finite_predictions']
        except Exception as exc:
            report[device+'_error'] = str(exc)[:1000]
    return report


if __name__ == '__main__':
    print(json.dumps(capability_probe(),ensure_ascii=False,indent=2))
