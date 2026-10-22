"""The graph a turn leaves behind: what it did, in what order, and what each step cost.

A node opens when the step starts and closes when it ends; the same node is streamed to the
client while the turn runs and stored with the final answer.  Tools hang off the step that
called them and join at the next node, so a step that ran two tools in parallel draws as a fork
and a join.  Everything written here is bounded and redacted: this is user-visible history, not
an audit log for credentials.
"""
from __future__ import annotations
import copy,re,time
from .references import bounded_stage

WIRE_KEYS=('id','kind','name','step','status','after','start_ms','ms','output')
# One slot is kept for the closing node, so a turn cut off at the cap still ends with an answer.
MAX_NODES=60
TOOL_KINDS={'tool'}
CLOSING_KINDS={'output','ask'}

def resolver_output(stage):
    """What the resolver decided, in the words the graph and the UI share."""
    stage=stage if isinstance(stage,dict) else {}
    validator=stage.get('validator') if isinstance(stage.get('validator'),dict) else {}
    passed=bool(validator.get('passed'))
    referents=[r for r in (stage.get('referents') or []) if isinstance(r,dict)]
    output={'canonical_question':stage.get('canonical_question'),'relationship':stage.get('relationship'),
        'referents':[str(r.get('text') or r.get('mention') or '')[:100] for r in referents][:3],
        'passed':passed,'rejected_reason':validator.get('rejected_reason'),'skipped':bool(stage.get('skipped'))}
    if output['skipped']:return 'done',output
    return ('done' if passed else 'warn'),output

def classifier_output(stage):
    stage=stage if isinstance(stage,dict) else {}
    scope=stage.get('scope') if isinstance(stage.get('scope'),dict) else {}
    output={'category':stage.get('category'),**{k:scope.get(k) for k in ('symbol','company','metrics','period','days_back')},
        'requested_actions':list(stage.get('requested_actions') or [])}
    # A classification that needed a second attempt still decides the turn, but the graph shows it.
    return ('warn' if stage.get('retries') else 'done'),output

def decision_output(decision):
    calls=decision.get('tool_calls') if isinstance(decision,dict) else None
    if isinstance(calls,list) and calls:
        return {'calls':[str((c.get('function') or {}).get('name')) for c in calls if isinstance(c,dict)]}
    return {'answer':True}

def tool_output(result):
    result=result if isinstance(result,dict) else {}
    ok=bool(result.get('ok'));data=result.get('data')
    status='done' if ok and (not isinstance(data,list) or data) else ('warn' if ok else 'failed')
    return status,{'ok':ok,'count':len(data) if isinstance(data,list) else (0 if data is None else 1),'error_code':result.get('error_code')}

def verify_output(result,issues,citations):
    result=result if isinstance(result,dict) else {}
    issues=list(issues or [])
    status='failed' if result.get('status')=='failed' else ('warn' if issues or result.get('status')=='partially_succeeded' else 'done')
    return status,{'status':result.get('status'),'reason':result.get('reason'),'citations':citations,'issues':[str(i)[:80] for i in issues[:3]]}

def final_output(result):
    result=result if isinstance(result,dict) else {}
    status='failed' if result.get('status')=='failed' else ('warn' if result.get('status')=='partially_succeeded' else 'done')
    output={'status':result.get('status'),'reason':result.get('reason'),'citations':len(result.get('citations') or [])}
    pending=result.get('pending_request')
    if isinstance(pending,dict):
        return 'ask',status,{**output,'question':str(pending.get('question') or '')[:200]}
    return 'output',status,output

def error_code(exc):
    """The provider's own short code when it has one, otherwise the exception type."""
    detail=getattr(exc,'detail',None)
    if isinstance(detail,str) and re.fullmatch(r'[a-z_]{3,80}',detail):return detail
    return type(exc).__name__

class TraceNode:
    """A mutable handle: a step may set its own status and output before the block closes."""

    __slots__=('id','kind','name','step','status','after','start_ms','ms','output')

    def __init__(self,identity=None,*,kind=None,name=None,step=None,start_ms=0,after=None):
        # An open node has no status yet; only the wire form says 'running'.
        self.id=identity;self.kind=kind;self.name=name;self.step=step;self.status=None
        self.after=list(after or []);self.start_ms=start_ms;self.ms=None;self.output=None

    def wire(self):
        node={k:getattr(self,k) for k in WIRE_KEYS}
        node['status']=self.status or 'running'
        return node

class TurnTrace:
    """Records one turn.  All methods are safe on a missing emit and on an exhausted graph."""

    def __init__(self,emit,*,secret='',clock=time.monotonic):
        self.emit=emit;self.secret=secret;self.clock=clock
        self.created=clock();self.nodes_seen=[];self.open_calls={}
        self.last_step=None;self.pending_tools=[]

    def now(self):return int(round((self.clock()-self.created)*1000))

    async def send(self,node):
        if self.emit is None:return
        await self.emit({'event':'trace',**node.wire()})

    async def begin(self,kind,*,name=None,step=None,key=None):
        # The graph stops one node short of the cap so that finish() can always close it.
        if len(self.nodes_seen)>=MAX_NODES or (len(self.nodes_seen)>=MAX_NODES-1 and kind not in CLOSING_KINDS):return None
        if kind in TOOL_KINDS:
            after=[self.last_step] if self.last_step else []
        else:
            after=list(self.pending_tools) or ([self.last_step] if self.last_step else [])
        node=TraceNode('n'+str(len(self.nodes_seen)+1),kind=kind,name=name,step=step,start_ms=self.now(),after=after)
        if kind in TOOL_KINDS:self.pending_tools.append(node.id)
        else:self.last_step=node.id;self.pending_tools=[]
        self.nodes_seen.append(node)
        if key is not None:self.open_calls[key]=node
        await self.send(node)
        return node

    async def end(self,node,*,status=None,output=None):
        if node is None or node.id is None or node.ms is not None:return
        node.status=status or node.status or 'done'
        if output is not None:node.output=output
        if node.output is not None:node.output=bounded_stage(node.output,self.secret)
        node.ms=max(0,self.now()-node.start_ms)
        await self.send(node)

    async def mark(self,kind,*,status='done',output=None,name=None):
        node=await self.begin(kind,name=name)
        await self.end(node,status=status,output=output)
        return node

    def step(self,kind,*,enabled=True,step=None):
        return _OpenStep(self,kind,enabled,step)

    async def relay(self,event):
        """Be the Turn's emit: forward first, then draw the tool this event carries."""
        if self.emit is not None:await self.emit(event)
        if not isinstance(event,dict):return
        kind=event.get('event')
        if kind=='tool_start':
            self.open_calls[event.get('call_id')]=await self.begin('tool',name=event.get('name'),key=event.get('call_id'))
        elif kind=='tool_result':
            call_id=event.get('call_id')
            node=self.open_calls.pop(call_id,None)
            # A card or a proposal only reports its result, so its node starts here.
            if node is None:node=await self.begin('tool',name=event.get('name'))
            status,output=tool_output(event.get('result'))
            await self.end(node,status=status,output=output)

    async def finish(self,result):
        for node in list(self.nodes_seen):
            if node.ms is None:await self.end(node,status='failed',output={'error':'not_finished'})
        kind,status,output=final_output(result)
        await self.mark(kind,status=status,output=output)

    def nodes(self):
        return [copy.deepcopy(n.wire()) for n in self.nodes_seen]

class _OpenStep:
    """One step: opens on enter, marks failure when the block raises, ends when it closes."""

    def __init__(self,trace,kind,enabled,step):
        self.trace=trace;self.kind=kind;self.enabled=enabled;self.step=step;self.node=TraceNode()

    async def __aenter__(self):
        if self.enabled:self.node=await self.trace.begin(self.kind,step=self.step) or TraceNode()
        return self.node

    async def __aexit__(self,exc_type,exc,tb):
        if exc is not None:
            await self.trace.end(self.node,status='failed',output={'error':error_code(exc)})
            return False
        await self.trace.end(self.node)
        return False
