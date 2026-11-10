import json,sys,os
for f in sys.argv[1:]:
    if not os.path.exists(f): continue
    name=os.path.basename(f)
    for line in open(f):
        if line.startswith(('control ','optimize ','control2 ')):
            ph,_,js=line.partition(' '); d=json.loads(js)
            print(name, ph, {k:d[k] for k in ('writes_ok_main','writes_ok_side','throughput_main_per_s','p50','p99','max','runs','writer_yield_ms','gate_hold_ms','writes_by_publications_inside','reprepares_load','side_p99','writes_other')})
            print('  slowest', d['slowest (latency, pubs inside, holds overlapped, held s inside, kind)'][:3])
        if line.startswith('acked'): print(' ', line.strip()[:200])
