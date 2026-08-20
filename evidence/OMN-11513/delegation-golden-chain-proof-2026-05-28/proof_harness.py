#!/usr/bin/env python3
"""OMN-12294 live golden-chain proof (stability-test). Pure-Kafka chain, no bridge."""
import json, subprocess, sys, time, uuid
from datetime import datetime, timezone
RP="omnibase-infra-stability-test-redpanda"
CID=str(uuid.uuid4()); NOW=datetime.now(timezone.utc).isoformat()
CMD_TOPIC="onex.cmd.omnibase-infra.delegation-request.v1"
HOPS=[
 ("routing_request","onex.cmd.omnibase-infra.delegation-routing-request.v1"),
 ("routing_decision","onex.evt.omnibase-infra.routing-decision.v1"),
 ("inference_request","onex.cmd.omnibase-infra.delegation-inference-request.v1"),
 ("inference_response","onex.evt.omnibase-infra.inference-response.v1"),
 ("quality_gate_request","onex.cmd.omnibase-infra.delegation-quality-gate-request.v1"),
 ("quality_gate_result","onex.evt.omnibase-infra.quality-gate-result.v1"),
 ("delegation_completed","onex.evt.omnibase-infra.delegation-completed.v1"),
 ("delegation_failed","onex.evt.omnibase-infra.delegation-failed.v1"),
]
PROMPT=("Code review request. Review this Python function for correctness, security, and style:\n\n"
 "def transfer_funds(accounts, src, dst, amount):\n    if accounts[src] >= amount:\n"
 "        accounts[src] = accounts[src] - amount\n        accounts[dst] = accounts[dst] + amount\n"
 "        return True\n    return False\n\nIdentify: (1) missing input validation, (2) the "
 "race-condition / atomicity risk under concurrency, (3) missing handling for unknown account "
 "keys, (4) absence of an audit log. Provide concrete, actionable findings.")
def ex(args,inp=None,timeout=40,i=False):
    return subprocess.run(["docker","exec"]+(["-i"] if i else [])+[RP,"rpk"]+args,
        input=inp,capture_output=True,text=True,timeout=timeout)
def describe(topic):
    out=ex(["topic","describe",topic,"-p"],timeout=20).stdout
    parts={}
    for ln in out.splitlines():
        f=ln.split()
        if len(f)>=6 and f[0].isdigit(): parts[int(f[0])]=(int(f[4]),int(f[5]))  # ls,hwm
    return parts
def scan(topic):
    found=None
    for p,(ls,hwm) in describe(topic).items():
        if hwm<=ls: continue
        c=ex(["topic","consume",topic,"-p",str(p),"-o",str(ls),"--num",str(hwm-ls),"-f","%v\n"],timeout=25)
        for line in c.stdout.splitlines():
            line=line.strip()
            if not line or CID not in line: continue
            try: return json.loads(line)
            except Exception: found={"raw":line[:2000]}
    return found
open("/tmp/omn12294-cid.txt","w").write(CID)
env={"payload":{"prompt":PROMPT,"task_type":"review","source_session_id":"omn-12294-stage2-bus-proof",
  "source_file_path":None,"correlation_id":CID,"max_tokens":1024,"emitted_at":NOW,
  "output_schema_key":None,"compliance_budget":None,"quality_contract_mode":"extend_task_class",
  "acceptance_criteria":[]},"envelope_id":str(uuid.uuid4()),"envelope_timestamp":NOW,
  "correlation_id":CID,"source_tool":"omn-12294-stage2-bus-proof",
  "event_type":"omnibase-infra.delegation-request","payload_type":"ModelDelegationRequest",
  "onex_version":{"major":1,"minor":0,"patch":0},"envelope_version":{"major":1,"minor":0,"patch":0},
  "priority":5,"retry_count":0}
p=ex(["topic","produce",CMD_TOPIC,"-k",CID],inp=json.dumps(env)+"\n",i=True)
print(f"PUBLISH cid={CID} rc={p.returncode} {p.stdout.strip()} {p.stderr.strip()}",flush=True)
if p.returncode!=0: sys.exit(1)
results={n:{"topic":t,"found":False} for n,t in HOPS}
deadline=time.time()+180
while time.time()<deadline:
    for name,topic in HOPS:
        if results[name]["found"]: continue
        try: m=scan(topic)
        except subprocess.TimeoutExpired: m=None
        if m is not None: results[name]={"topic":topic,"found":True,"sample":m}
    if (results["delegation_completed"]["found"] or results["delegation_failed"]["found"]) and results["quality_gate_result"]["found"]:
        break
    time.sleep(6)
for name,_ in HOPS: print(f"HOP {name}: {'FOUND' if results[name]['found'] else 'MISSING'}",flush=True)
open("/tmp/omn12294-proof-result.json","w").write(json.dumps({"correlation_id":CID,"started_at":NOW,
  "lane":"stability-test","hops":results},indent=2,default=str))
print("RESULT_WRITTEN")
