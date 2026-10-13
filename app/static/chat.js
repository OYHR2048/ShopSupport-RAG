const $=id=>document.getElementById(id);
let session=null,busy=false;
const examples=["你好","七天无理由退货需要满足什么条件？","定制马克杯过了7天但是有质量问题，还能退吗，运费谁承担？","支持火星配送吗？","查一下订单 ORD10001","它的物流到哪了？","帮我取消订单 ORD10003","给 ORD10002 申请退款，原因是质量问题"];
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function showRuntime(r){$("mode").textContent=({demo:"演示模式 · 离线规则与摘录",model:"真实模型模式",degraded:"降级模式 · 请查看原因"})[r.mode]||"状态未知";$("backends").textContent=`生成：${r.model} ｜ 向量：${r.embedding} ｜ 重排：${r.reranker} ｜ 存储：${r.vector_store}`;$("warnings").textContent=(r.warnings||[]).join(" ");}
function details(title,data){const d=el("details");d.append(el("summary",title));d.append(el("pre",JSON.stringify(data,null,2)));return d;}
function card(role,text){const a=el("article",undefined,role);a.append(el("div",role==="user"?"你":"演示商店客服","role"),el("div",text,"answer"));$("messages").append(a);a.scrollIntoView({block:"nearest"});return a;}
async function send(message,action){if(busy)return;busy=true;$("send").disabled=true;card("user",message);
try{const res=await fetch("/api/v1/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message,user_id:"u_demo",session_id:session,confirm_action_id:action||null})});const b=await res.json();if(!res.ok)throw new Error(b.message||b.detail||"请求失败");session=b.session_id;showRuntime(b.runtime);const a=card("assistant",b.answer);const d=b.diagnostics||{};a.append(el("div",`路由 ${(b.strategy||"unknown").toUpperCase()} · ${b.latency.total_ms.toFixed(0)} ms · 模型逻辑调用 ${d.llm_calls||0} 次 · 检索重试 ${d.retry_count||0} 次${b.refused?" · 暂不能可靠回答":""}`,"tags"));
if(b.citations.length){const s=el("details");s.append(el("summary",`引用来源（${b.citations.length}）`));b.citations.forEach(c=>{const p=el("p");const link=el("a",c.source+" · "+(c.section||c.title));link.href="/api/v1/sources/"+encodeURIComponent(c.chunk_id);link.target="_blank";p.append(link,el("p",c.excerpt));s.append(p);});a.append(s);}
a.append(details("查看执行过程：路由、改写、证据检查与停止原因",{intent:b.intent,strategy:b.strategy,resolved_query:d.resolved_query,rewritten_queries:d.rewritten_queries,retrieval:d.retrieval,stop_reason:d.stop_reason,retry_count:d.retry_count,faithfulness:d.faithfulness,tools:d.tools,latency:b.latency,token_usage:b.token_usage,model_calls:d.model_calls,backend_calls:d.backend_calls,network_attempts:d.network_attempts,runtime:b.runtime}));
if(b.pending_action){const p=b.pending_action;const box=el("div",undefined,"confirm");box.append(el("div",p.summary),el("pre",JSON.stringify(p.arguments,null,2)));const confirm=el("button","确认模拟操作");confirm.onclick=async()=>{if(busy)return;confirm.disabled=true;await send("确认模拟操作",p.action_id);confirm.disabled=false;confirm.textContent="再次查看确认结果（幂等）";};box.append(confirm);a.append(box);}
a.scrollIntoView({block:"nearest"});
}catch(e){card("assistant",e.message).classList.add("error");}finally{busy=false;$("send").disabled=false;$("input").focus();}}
examples.forEach(q=>{const b=el("button",q);b.onclick=()=>{$("input").value=q;$("input").focus();};$("examples").append(b);});
$("form").onsubmit=e=>{e.preventDefault();const q=$("input").value.trim();if(q){$("input").value="";send(q);}};
$("input").onkeydown=e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("form").requestSubmit();}};
$("new").onclick=()=>{if(busy)return;session=null;$("messages").replaceChildren(el("article","已开始新对话，历史上下文不会带入。","welcome"));};
fetch("/api/v1/runtime").then(r=>r.json()).then(showRuntime).catch(()=>{$("mode").textContent="后端不可达";});
